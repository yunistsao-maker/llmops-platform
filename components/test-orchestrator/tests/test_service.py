"""Event handling and run completion against PostgreSQL (runner results are simulated here; see test_e2e for the
real runner)."""
from __future__ import annotations

import json

import pytest
from conftest import ROUTES, approve

from llmops_common import queue
from test_orchestrator.finalize import compare
from test_orchestrator.service import PLAN_MODEL


def deliver(db, orch, event, payload, event_id=1):
    """Publish an event and run the orchestrator's worker on its job."""
    with db.connection() as c:
        queue.publish(c, event, payload, source="test", dedupe_key=f"{event}:{json.dumps(payload, sort_keys=True)}")
    return drain(db, orch)


def drain(db, orch):
    w = queue.Worker("test-orchestrator", orch, poll_seconds=0.01)
    n = 0
    while w.run_once():
        n += 1
    return n


def runner_jobs(db):
    with db.connection() as c:
        return [r["payload"] for r in c.execute("SELECT payload FROM platform.jobs WHERE queue = 'test-runner' ORDER BY id")]


def finish_job(db, store, job, status="done", attempts=None, failures=0, models=None, stop=None, signals=0):
    """What test-runner leaves behind for a job: summaries, result rows, one test.job.completed event."""
    from psycopg.types.json import Jsonb

    with db.connection() as c:
        for item in job["items"]:
            n, trials = max(1, item.get("n") or 1), max(1, item.get("trials") or 1)
            att = attempts if attempts is not None else n * trials
            rule = dict(item["rule"])
            if item.get("baseline"):
                rule.update(baseline="pending", baseline_route=item["baseline"]["route_id"])
            verdict = "pass" if status == "done" else "error"
            summary = {"run_id": job["run_id"], "route_id": job["route"]["id"], "sec_id": item["sec_id"], "n": n, "trials": trials,
                       "attempts": att, "failures": failures, "executions": n * trials, "errors": 0, "verdict": verdict,
                       "rate": failures / att if att else None, "rule": rule, "safeguard_signals": signals,
                       "models_seen": models or {"m": n * trials}, "stop_reasons": stop or {"end_turn": n * trials}}
            key = f"{job['run_id']}/{job['route']['id']}/{item['sec_id']}"
            store.put_json("llmops-runs", key + ".summary.json", summary)
            c.execute("INSERT INTO tests.results (run_id, route_id, sec_id, n, trials, failures, verdict, rule, models_seen, "
                      "stop_reasons, details_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (job["run_id"], job["route"]["id"], item["sec_id"], n, trials, failures, verdict, Jsonb(rule),
                       Jsonb(summary["models_seen"]), Jsonb(summary["stop_reasons"]), key + ".jsonl"))
        queue.publish(c, "test.job.completed", {"plan_id": job["plan_id"], "run_id": job["run_id"], "job_key": job["job_key"],
                                                "route_id": job["route"]["id"], "sec_ids": [i["sec_id"] for i in job["items"]],
                                                "status": status, "error": None if status == "done" else "boom"},
                      source="test-runner", dedupe_key=f"test.job.completed:{job['job_key']}")


def plan_row(db):
    with db.connection() as c:
        return c.execute("SELECT * FROM tests.plans ORDER BY created_at DESC LIMIT 1").fetchone()


def test_route_added_upserts_and_plans_once(db, store, orch):
    approve(store, "SEC-04")
    payload = {"model_id": "newm", "provider": "aws", "region": "us-west-2", "model_name": "us.anthropic.newm", "status": "ga"}
    deliver(db, orch, "route.added", payload)
    with db.connection() as c:
        route = c.execute("SELECT * FROM tests.routes WHERE id = 'direct:bedrock:us-west-2:newm'").fetchone()
        internal = c.execute("SELECT payload, dedupe_key FROM platform.jobs WHERE queue = 'test-orchestrator' "
                             "AND payload->>'event' = %s", (PLAN_MODEL,)).fetchall()
        plans = c.execute("SELECT * FROM tests.plans").fetchall()
    assert route["enabled"] and route["target"] == {"model": "us.anthropic.newm", "region": "us-west-2"}
    assert len(internal) == 1 and len(plans) == 1
    assert plans[0]["trigger"] == "route.added" and plans[0]["model_id"] == "newm"
    assert plans[0]["trigger_detail"]["purpose"] == "pre-release" and plans[0]["trigger_detail"]["source_key"]
    # the same internal job delivered again (crash before it was marked done) plans nothing new
    from llmops_common.queue import Job

    orch.handle(Job(id=1, queue="test-orchestrator", payload=internal[0]["payload"], attempts=2, max_attempts=5,
                    dedupe_key=internal[0]["dedupe_key"]))
    with db.connection() as c:
        assert c.execute("SELECT count(*) AS n FROM tests.plans").fetchone()["n"] == 1


def test_route_added_does_not_override_configured_routes(db, store, orch):
    deliver(db, orch, "route.added", {"model_id": "cand", "provider": "bedrock", "region": "us-east-1", "model_name": "other-name"})
    with db.connection() as c:
        target = c.execute("SELECT target FROM tests.routes WHERE id = 'direct:bedrock:us-east-1:cand'").fetchone()["target"]
    assert target["model"] == "global.anthropic.cand"


def test_gateway_version_changed_plans_platform_items_and_smoke_set(db, store, orch):
    approve(store, "SEC-01", "SEC-04", "SEC-07", "SEC-10")
    deliver(db, orch, "gateway.version_changed", {"gateway": "onprem", "previous_version": "1.104.0", "version": "1.105.2"})
    plan = plan_row(db)
    assert plan["trigger"] == "gateway.version_changed" and plan["gateway_version"] == "1.105.2" and plan["model_id"] is None
    assert plan["trigger_detail"]["purpose"] == "regression"
    payloads = runner_jobs(db)
    assert {p["route"]["kind"] for p in payloads} == {"gateway"}
    items = {(p["route"]["id"], i["sec_id"]): i for p in payloads for i in p["items"]}
    assert set(items) == {(r, s) for r in ("gw:onprem:cand", "gw:onprem:prod") for s in ("SEC-01", "SEC-04", "SEC-07", "SEC-10")} | {
        ("gw:onprem:cand", "SEC-16")}
    assert (items[("gw:onprem:cand", "SEC-01")]["n"], items[("gw:onprem:cand", "SEC-01")]["trials"]) == (20, 1)  # smoke
    assert (items[("gw:onprem:cand", "SEC-07")]["n"], items[("gw:onprem:cand", "SEC-07")]["trials"]) == (50, 1)  # full platform item
    assert payloads[0]["settings"]["gateway_version"] == "1.105.2"


def test_completion_counts_derives_sec11_and_publishes_once(db, store, orch):
    approve(store, "SEC-04", "SEC-10")
    with db.connection() as conn:
        plan = orch.plan(conn, trigger="manual", model_id="cand", sec_ids=["SEC-04", "SEC-10", "SEC-11"])
    payloads = runner_jobs(db)
    for i, job in enumerate(payloads):
        finish_job(db, store, job, models={"cand": 10} if job["route"]["kind"] == "direct" else {"cand-alias": 10},
                   stop={"end_turn": 8, "refusal": 2}, signals=2)
        drain(db, orch)
        row = plan_row(db)
        assert row["jobs_done"] == i + 1
        assert row["status"] == ("done" if i == len(payloads) - 1 else "running")
    drain(db, orch)  # nothing left; re-delivery changes nothing
    with db.connection() as c:
        sec11 = {r["route_id"]: r for r in c.execute("SELECT * FROM tests.results WHERE sec_id = 'SEC-11'").fetchall()}
        completed = c.execute("SELECT payload FROM platform.events WHERE type = 'test.run.completed'").fetchall()
    assert len(completed) == 1 and completed[0]["payload"]["status"] == "done"
    gw = sec11["gw:onprem:cand"]
    assert gw["verdict"] == "info" and gw["models_seen"] == {"cand-alias": 20} and gw["stop_reasons"] == {"end_turn": 16, "refusal": 4}
    assert gw["failures"] == 4 and gw["n"] > 0
    summary = store.get_json("llmops-runs", f"{plan['run_id']}/gw:onprem:cand/SEC-11.summary.json")
    assert "gateway alias" in summary["gateway_note"] and set(summary["by_item"]) == {"SEC-04", "SEC-10"}


def test_failed_job_makes_a_partial_run(db, store, orch):
    approve(store, "SEC-04")
    with db.connection() as conn:
        orch.plan(conn, trigger="manual", model_id="cand", sec_ids=["SEC-04"])
    first, second = runner_jobs(db)
    finish_job(db, store, first)
    finish_job(db, store, second, status="failed")
    drain(db, orch)
    row = plan_row(db)
    assert (row["status"], row["jobs_done"], row["jobs_failed"]) == ("partial", 1, 1)


def test_reaper_counts_dead_jobs_without_event(db, store, orch):
    approve(store, "SEC-04")
    with db.connection() as conn:
        orch.plan(conn, trigger="manual", model_id="cand", sec_ids=["SEC-04"])
    first, second = runner_jobs(db)
    finish_job(db, store, first)
    drain(db, orch)
    assert plan_row(db)["status"] == "running"  # the second job is still out
    with db.connection() as c:  # ...then its worker is killed on the last attempt: no event will ever come
        c.execute("UPDATE platform.jobs SET status = 'dead' WHERE dedupe_key = %s", (second["job_key"],))
        c.execute("UPDATE tests.plans SET created_at = now() - interval '10 minutes'")
    assert drain(db, orch) == 0
    assert orch.reap() == 1  # the reaper sees the dead job
    row = plan_row(db)
    assert (row["status"], row["jobs_failed"]) == ("partial", 1)
    with db.connection() as c:
        lost = c.execute("SELECT verdict, rule FROM tests.results WHERE route_id = %s", (second["route"]["id"],)).fetchone()
    assert lost["verdict"] == "error" and lost["rule"] == {"reason": "job lost"}


@pytest.mark.parametrize("cand, base, expected", [
    ((10, 300), (10, 300), "pass"),
    ((5, 300), (10, 300), "pass"),
    ((40, 300), (10, 300), "fail"),
    ((13, 300), (10, 300), "review"),
])
def test_compare(cand, base, expected):
    out = compare({"failures": cand[0], "attempts": cand[1]}, {"failures": base[0], "attempts": base[1]})
    assert out[0] == expected


def test_compare_keeps_undecided_cases_in_view():
    assert compare({"failures": 0, "attempts": 100, "reviews": 50}, {"failures": 3, "attempts": 300})[0] == "review"
    assert compare({"failures": 0, "attempts": 300, "reviews": 1}, {"failures": 3, "attempts": 300})[0] == "pass"
    assert compare({"failures": 0, "attempts": 0, "reviews": 9}, {"failures": 3, "attempts": 300}) is None


def test_baseline_compared_when_run_completes(db, store, orch):
    orch.settings.baseline_model_id = "prod"
    approve(store, "SEC-01")
    with db.connection() as conn:
        plan = orch.plan(conn, trigger="manual", model_id="cand", sec_ids=["SEC-01"])
    for job in runner_jobs(db):
        worse = job["route"]["model_id"] == "cand" and job["route"]["kind"] == "gateway"
        finish_job(db, store, job, attempts=900, failures=90 if worse else 20)
    drain(db, orch)
    with db.connection() as c:
        rows = {r["route_id"]: r for r in c.execute("SELECT * FROM tests.results WHERE run_id = %s AND sec_id = 'SEC-01'",
                                                    (plan["run_id"],)).fetchall()}
    assert rows["gw:onprem:cand"]["verdict"] == "fail" and rows["gw:onprem:cand"]["rule"]["baseline"]["route_id"] == "gw:onprem:prod"
    assert rows["direct:bedrock:us-east-1:cand"]["verdict"] == "pass"
    assert rows["gw:onprem:prod"]["verdict"] == "pass" and "baseline" not in rows["gw:onprem:prod"]["rule"]


def test_release_published_resyncs_routes(db, store, orch, routes_file):
    cfg = json.loads(open(routes_file, encoding="utf-8").read())
    cfg["routes"].append({"model_id": "cand", "kind": "direct", "provider": "azure", "region": "eastus2",
                          "target": {"endpoint": "https://res.services.ai.azure.com", "deployment": "cand"}})
    open(routes_file, "w", encoding="utf-8").write(json.dumps(cfg))
    deliver(db, orch, "release.published", {"release_id": "r1", "previous_release_id": None, "manifest_key": "k", "held": False,
                                            "changes": {"routes_added": [{"model_id": "z", "provider": "bedrock", "model_name": "z"}]}})
    with db.connection() as c:
        ids = {r["id"] for r in c.execute("SELECT id FROM tests.routes")}
        assert c.execute("SELECT count(*) AS n FROM tests.plans").fetchone()["n"] == 0
    assert "direct:azure:eastus2:cand" in ids and "direct:bedrock:us-east-1:z" in ids
    assert len(ids) == len(ROUTES["routes"]) + 2
