"""Build a test plan: (routes x SEC items) -> runner jobs, tests.plans row, not_run rows and runs/{run_id}/run.json.

Per catalog item (contracts/sec-catalog.json):
    runnable false                 never planned (listed under `skipped` when asked for)
    routes all|gateway|primary     which of the plan's routes it applies to ("primary" = the first gateway route)
    layer logged (SEC-11)          derived when the run completes, from every other result of the route
    layer platform, n 0 (SEC-16)   one live check per gateway (on that gateway's first route), no dataset
    anything else                  needs an approved dataset in the datasets bucket's index.json; without one the
                                   (route, item) is recorded as not_run and nothing is sent
not_worse_than_baseline items get a baseline route when a baseline (production) model is configured and differs from the
planned model: the baseline's matching route runs the same items with the same dataset, seed and n, and test-orchestrator
compares the two when the run completes.
Jobs: one per (route, batch of items), batches capped at JOB_MAX_CALLS case executions (min(n, dataset size) x trials).
"""
from __future__ import annotations

import uuid

from llmops_common.ids import iso, new_run_id
from llmops_common.log import get_logger, kv
from llmops_common.queue import enqueue
from llmops_common.storage import bucket

from . import catalog as cat
from .routes import gateway_of, list_routes
from .settings import Settings

log = get_logger("test_orchestrator.planner")
PURPOSES = ("pre-release", "regression", "adhoc")


class PlanError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def current_gateway_version(conn, gateway: str) -> str | None:
    row = conn.execute("SELECT payload FROM platform.events WHERE type = 'gateway.version_changed' AND payload->>'gateway' = %s "
                       "ORDER BY id DESC LIMIT 1", (gateway,)).fetchone()
    return (row["payload"] or {}).get("version") if row else None


def routes_for_item(item: dict, routes: list[dict]) -> list[dict]:
    mode = item.get("routes", "all")
    gateway_routes = [r for r in routes if r["kind"] == "gateway"]
    if mode == "all":
        targets = list(routes)
    elif mode == "gateway":
        targets = gateway_routes
    elif mode == "primary":
        targets = gateway_routes[:1]
    else:
        targets = []
    if cat.is_gateway_check(item):  # once per gateway
        seen, first = set(), []
        for r in targets:
            g = gateway_of(r["id"])
            if r["kind"] == "gateway" and g not in seen:
                seen.add(g)
                first.append(r)
        targets = first
    return targets


def baseline_route_for(route: dict, candidates: list[dict]) -> dict | None:
    """The baseline model's route that matches `route`: same kind and provider, same gateway (or region)."""
    same = [b for b in candidates if b["kind"] == route["kind"] and b["provider"] == route["provider"]]
    if route["kind"] == "gateway":
        exact = [b for b in same if gateway_of(b["id"]) == gateway_of(route["id"])]
    else:
        region = (route.get("target") or {}).get("region")
        exact = [b for b in same if (b.get("target") or {}).get("region") == region]
    return (exact or same or [None])[0]


def batch(items: list[dict], max_calls: int, available: dict[str, int] | None = None) -> list[list[dict]]:
    """Group items so a job runs at most about max_calls case executions. `available` is the number of cases in each
    item's dataset (from its manifest): the runner samples min(n, available)."""
    available = available or {}
    batches, current, calls = [], [], 0
    for it in items:
        n = int(it.get("n") or 1)
        if it["sec_id"] in available:
            n = min(n, available[it["sec_id"]])
        est = max(1, n) * max(1, int(it.get("trials") or 1))
        if current and calls + est > max_calls:
            batches.append(current)
            current, calls = [], 0
        current.append(it)
        calls += est
    if current:
        batches.append(current)
    return batches


def default_purpose(trigger: str, sec_ids: list[str] | None) -> str:
    if trigger == "gateway.version_changed":
        return "regression"
    if trigger == "route.added":
        return "pre-release"
    return "adhoc" if sec_ids else "pre-release"


def create_plan(conn, store, catalog: dict, settings: Settings, *, trigger: str, trigger_detail: dict | None = None,
                model_id: str | None = None, route_ids: list[str] | None = None, sec_ids: list[str] | None = None,
                purpose: str | None = None, gateway_version: str | None = None, routes: list[dict] | None = None,
                overrides: dict[str, dict] | None = None, baseline_model_id: str | None = None) -> dict:
    """Create the plan inside the caller's transaction. Returns a description of what was planned."""
    from psycopg.types.json import Jsonb

    by_id = cat.items_by_id(catalog)
    if sec_ids:
        unknown = [s for s in sec_ids if s not in by_id]
        if unknown:
            raise PlanError(f"unknown SEC items: {', '.join(unknown)}")
        wanted = set(sec_ids)
        chosen = [i for i in catalog["items"] if i["sec_id"] in wanted]
    else:
        chosen = [i for i in catalog["items"] if i.get("runnable")]
    skipped = [i["sec_id"] for i in chosen if not i.get("runnable")]
    chosen = [i for i in chosen if i.get("runnable")]
    if purpose and purpose not in PURPOSES:
        raise PlanError(f"purpose must be one of {', '.join(PURPOSES)}")
    if routes is None:
        routes = list_routes(conn, model_id=model_id, ids=route_ids)
        if route_ids:
            missing = sorted(set(route_ids) - {r["id"] for r in routes})
            if missing:
                raise PlanError(f"unknown or disabled routes: {', '.join(missing)}", 404)
    if not routes:
        raise PlanError(f"no enabled routes{' for ' + model_id if model_id else ''}", 404)
    if not chosen:
        raise PlanError("no runnable SEC items selected")

    index = store.get_json(bucket("datasets"), "index.json", default={}) or {}
    gateway_version = gateway_version or current_gateway_version(conn, settings.gateway_name)
    plan_id, run_id = str(uuid.uuid4()), new_run_id()
    overrides = overrides or {}
    models_planned = {r["model_id"] for r in routes}
    baseline_model_id = baseline_model_id or settings.baseline_model_id
    baseline_routes = []
    if baseline_model_id and models_planned != {baseline_model_id}:
        baseline_routes = list_routes(conn, model_id=baseline_model_id)

    route_by_id = {r["id"]: r for r in routes}
    available: dict[str, int] = {}
    work: dict[str, list[dict]] = {}
    not_run: list[tuple[str, dict]] = []
    derived: list[tuple[str, str]] = []
    described: list[dict] = []
    for item in chosen:
        sid = item["sec_id"]
        n = int(overrides.get(sid, {}).get("n", item.get("n") or 0))
        trials = int(overrides.get(sid, {}).get("trials", item.get("trials") or 1))
        targets = routes_for_item(item, routes)
        entry = {"sec_id": sid, "layer": item.get("layer"), "n": n, "trials": trials, "rule": item.get("rule"),
                 "routes": [r["id"] for r in targets]}
        if not targets:
            entry["mode"] = "no_route"
        elif cat.is_derived(item):
            entry["mode"] = "derived"
            derived += [(r["id"], sid) for r in targets]
        elif cat.is_gateway_check(item):
            entry["mode"] = "check"
            for r in targets:
                work.setdefault(r["id"], []).append({"sec_id": sid, "n": 0, "trials": 1, "rule": item.get("rule")})
        elif sid not in index:
            entry["mode"] = "not_run"
            entry["reason"] = "no approved dataset in index.json"
            not_run += [(r["id"], item) for r in targets]
        else:
            ds = {"dataset_id": index[sid]["dataset_id"], "version": int(index[sid]["version"])}
            entry.update(mode="dataset", dataset=ds)
            manifest = store.get_json(bucket("datasets"), f"{sid}/{ds['dataset_id']}/v{ds['version']}/manifest.json", default=None)
            if manifest and manifest.get("n"):
                available[sid] = int(manifest["n"])
                entry["dataset_n"] = available[sid]
            entry_baselines = {}
            for r in targets:
                job_item = {"sec_id": sid, "dataset": ds, "n": n, "trials": trials, "rule": item.get("rule")}
                is_relative = (item.get("rule") or {}).get("type") == "not_worse_than_baseline"
                if is_relative and baseline_routes and r["model_id"] != baseline_model_id:
                    b = baseline_route_for(r, baseline_routes)
                    if b:
                        job_item["baseline"] = {"route_id": b["id"], "model_id": b["model_id"]}
                        entry_baselines[r["id"]] = b["id"]
                        route_by_id.setdefault(b["id"], b)
                        if not any(x["sec_id"] == sid for x in work.get(b["id"], [])):
                            work.setdefault(b["id"], []).append({"sec_id": sid, "dataset": ds, "n": n, "trials": trials,
                                                                 "rule": item.get("rule")})
                if any(x["sec_id"] == sid for x in work.get(r["id"], [])):
                    continue  # already added as another route's baseline
                work.setdefault(r["id"], []).append(job_item)
            if entry_baselines:
                entry["baselines"] = entry_baselines
        described.append(entry)

    jobs = []
    settings_payload = {"max_tokens": settings.max_tokens, "seed": settings.seed, "gateway_version": gateway_version}
    for rid in sorted(work, key=lambda x: (list(route_by_id).index(x) if x in route_by_id else 0, x)):
        route = route_by_id[rid]
        for items in batch(work[rid], settings.job_max_calls, available):
            job_key = f"{run_id}|{rid}|{','.join(i['sec_id'] for i in items)}"
            jobs.append({"plan_id": plan_id, "run_id": run_id, "job_key": job_key,
                         "route": {"id": rid, "model_id": route["model_id"], "kind": route["kind"], "provider": route["provider"],
                                   "target": route.get("target") or {}},
                         "items": items, "judge": {"model": settings.judge_model}, "settings": settings_payload})

    purpose = purpose or default_purpose(trigger, sec_ids)
    detail = {**(trigger_detail or {}), "purpose": purpose}
    if skipped:
        detail["skipped_not_runnable"] = skipped
    if baseline_routes:
        detail["baseline_model_id"] = baseline_model_id
    conn.execute(
        "INSERT INTO tests.plans (id, trigger, trigger_detail, model_id, run_id, status, jobs_total, gateway_version) "
        "VALUES (%s, %s, %s, %s, %s, 'running', %s, %s)",
        (plan_id, trigger, Jsonb(detail), model_id, run_id, len(jobs), gateway_version),
    )
    for rid, item in not_run:
        conn.execute(
            "INSERT INTO tests.results (run_id, route_id, sec_id, n, trials, verdict, rule) VALUES (%s, %s, %s, 0, %s, 'not_run', %s) "
            "ON CONFLICT (run_id, route_id, sec_id) DO NOTHING",
            (run_id, rid, item["sec_id"], int(item.get("trials") or 1), Jsonb({**(item.get("rule") or {}), "reason": "no approved dataset"})),
        )
    for job in jobs:
        enqueue(conn, "test-runner", job, dedupe_key=job["job_key"], max_attempts=settings.runner_max_attempts)
    run_doc = {"run_id": run_id, "plan_id": plan_id, "created_at": iso(), "trigger": trigger, "purpose": purpose,
               "gateway_version": gateway_version, "judge_model": settings.judge_model,
               "routes": [{"id": r["id"], "model_id": r["model_id"], "kind": r["kind"], "provider": r["provider"]}
                          for r in route_by_id.values()],
               "items": described, "jobs": [{"job_key": j["job_key"], "route_id": j["route"]["id"],
                                             "sec_ids": [i["sec_id"] for i in j["items"]]} for j in jobs],
               "settings": settings_payload}
    store.put_json(bucket("runs"), f"{run_id}/run.json", run_doc)
    log.info("plan created", extra=kv(plan_id=plan_id, run_id=run_id, trigger=trigger, purpose=purpose, model_id=model_id,
                                      routes=len(route_by_id), jobs=len(jobs), not_run=len(not_run), derived=len(derived)))
    return {"plan_id": plan_id, "run_id": run_id, "trigger": trigger, "purpose": purpose, "jobs_total": len(jobs),
            "jobs": [j["job_key"] for j in jobs], "routes": list(route_by_id), "items": described,
            "not_run": [{"route_id": rid, "sec_id": i["sec_id"]} for rid, i in not_run],
            "derived": [{"route_id": rid, "sec_id": sid} for rid, sid in derived], "skipped": skipped,
            "gateway_version": gateway_version}
