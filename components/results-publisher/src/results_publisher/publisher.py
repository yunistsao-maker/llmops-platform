"""test.run.completed -> the portal's security data (the `security` key of contracts/fixtures/portal-view.sample.json).

    {runId, date, purpose, litellm, judge, cases, status,
     models: {model_id: {label, counts: {verdict: n}, issues: [{id, t, tEn, v, rate, rule}]}},
     items:  [{id, t, tEn, tier, layer, rule, ruleEn, res: {label: {v, rate, n, upper95, routes: {route_id: {...}}}}}]}

A model can have several routes (direct per cloud, through the gateway); its result for an item is its worst route
(fail > error > review > pass > info > not_run), and every route is kept under res[label].routes. Gateway-level items
(SEC-16) are keyed "gateway" and do not count toward any model. n is case executions (cases x trials), as in secsuite.

Written to the curated bucket: portal/security/{run_id}.json (once per run) and the pointer portal/security/current.json
{"current": run_id}. The pointer moves only for runs whose purpose is in CURRENT_PURPOSES (default pre-release: a smoke
run after a gateway upgrade must not replace a model's full results), whose status is not failed, and that are not
older than the current one; security.results.published {run_id, key} is published when it moves.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import timezone
from functools import lru_cache
from pathlib import Path

from llmops_common.config import env, env_list
from llmops_common.ids import iso
from llmops_common.log import get_logger, kv
from llmops_common.queue import Job, PermanentError, publish
from llmops_common.storage import bucket

log = get_logger("results_publisher")
SOURCE = "results-publisher"
SEVERITY = ("fail", "error", "review", "pass", "info", "not_run")  # worst first
ISSUES = ("fail", "review", "error")
IMAGE_CATALOG = Path("/app/contracts/sec-catalog.json")


def catalog_path() -> Path:
    """SEC_CATALOG_PATH, else the copy in the image, else the repo's contracts/ (development and tests)."""
    configured = env("SEC_CATALOG_PATH", None)
    if configured:
        return Path(configured)
    if IMAGE_CATALOG.exists():
        return IMAGE_CATALOG
    parents = Path(__file__).resolve().parents  # <repo>/components/results-publisher/src/results_publisher/publisher.py
    return parents[min(4, len(parents) - 1)] / "contracts" / "sec-catalog.json"


@lru_cache(maxsize=2)
def load_catalog(path: str | None = None) -> dict:
    return json.loads(Path(path or catalog_path()).read_text(encoding="utf-8"))


def _pct(x) -> str:
    return f"{float(x):.0%}"


def rule_text(rule: dict) -> tuple[str, str]:
    """(Chinese, English) one-sentence pass rule, in secsuite's wording."""
    kind = (rule or {}).get("type", "info")
    if kind == "not_worse_than_baseline":
        fb = rule.get("fallback") or {"type": "info"}
        zh, en = rule_text(fb)
        if fb.get("type", "info") == "info":
            return ("失敗比例不得高於現役模型（需與現役模型一起測）；未設現役模型時僅記錄",
                    "Failure rate must not exceed the current production model's (tested together); recorded only without one")
        return (f"失敗比例不得高於現役模型（需與現役模型一起測）；未設現役模型時：{zh}",
                f"Failure rate must not exceed the current production model's (tested together); without one: {en}")
    if kind == "zero":
        if rule.get("review_failures"):
            return "任何一筆失敗即不通過（零容忍；失敗須經人工確認）", "Any failure fails the item (zero tolerance; failures are confirmed by a person)"
        return "任何一筆失敗即不通過（零容忍）", "Any failure fails the item (zero tolerance)"
    if kind == "max_rate":
        t = rule.get("max", 0)
        tbd = rule.get("tbd")
        return (f"失敗比例超過 {_pct(t)} 即不通過" + ("（門檻暫定）" if tbd else ""),
                f"Fails when the failure rate exceeds {_pct(t)}" + (" (provisional threshold)" if tbd else ""))
    if kind == "control":
        return "控制措施每次都必須生效（任何一筆失敗即不通過）", "The control must hold every time (any failure fails the item)"
    return "僅記錄，不設門檻", "Recorded only, no threshold"


# ---------------------------------------------------------------------------
# labels
# ---------------------------------------------------------------------------
def model_names(store) -> dict[str, str]:
    """Catalog model names from the current portal release (empty when there is none)."""
    try:
        manifest = store.get_json(bucket("curated"), "portal/manifest.json", default=None) or {}
        current = manifest.get("current")
        if not current:
            return {}
        doc = store.get_json(bucket("curated"), f"portal/releases/{current}/data/catalog.json", default=None) or {}
        models = doc.get("models", doc) if isinstance(doc, dict) else doc
        return {m["id"]: m.get("name") or m["id"] for m in models if isinstance(m, dict) and m.get("id")}
    except Exception as exc:  # noqa: BLE001 - labels fall back to the model id
        log.warning("catalog names unavailable", extra=kv(error=type(exc).__name__))
        return {}


def derive_label(model_id: str) -> str:
    tokens = [t for t in model_id.split("-") if t]
    if tokens and tokens[0] in ("claude",):
        tokens = tokens[1:]
    words: list[str] = []
    for t in tokens:
        if t.isdigit() and words and words[-1].replace(".", "").isdigit():
            words[-1] += "." + t
        elif t.isdigit():
            words.append(t)
        else:
            words.append(t.upper() if len(t) <= 3 and t.isalpha() and t in ("gpt",) else t.capitalize())
    return " ".join(words) or model_id


def label_for(model_id: str, names: dict[str, str], strip: tuple[str, ...]) -> str:
    name = names.get(model_id)
    if not name:
        return derive_label(model_id)
    for prefix in strip:
        if prefix and name.startswith(prefix) and len(name) > len(prefix):
            return name[len(prefix):]
    return name


def judge_label(seen: Counter, alias: str | None, names: dict[str, str], strip: tuple[str, ...]) -> str | None:
    configured = env("JUDGE_LABEL", None)
    if configured:
        return configured
    for model, _ in seen.most_common():
        if not model or model == alias:
            continue  # through the gateway the answer carries the alias, which says nothing about the model
        hits = sorted((mid for mid in names if mid in model), key=len, reverse=True)
        if hits:
            return label_for(hits[0], names, strip)
        # not in the catalog: bedrock/global.anthropic.claude-opus-5-5 -> claude-opus-5-5 -> "Opus 5.5"
        parts = [p for p in re.split(r"[/.:@]", model) if "-" in p and re.search(r"[a-z]", p)]
        return derive_label(parts[-1]) if parts else model
    return alias


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
def worst(verdicts) -> str:
    return min(verdicts, key=lambda v: SEVERITY.index(v) if v in SEVERITY else len(SEVERITY))


def build_security(conn, store, run_id: str, catalog: dict | None = None) -> tuple[dict, dict]:
    """Returns (security document, plan row)."""
    catalog = catalog or load_catalog()
    plan = conn.execute("SELECT * FROM tests.plans WHERE run_id = %s", (run_id,)).fetchone()
    if not plan:
        raise PermanentError(f"no plan for run {run_id}")
    rows = conn.execute(
        "SELECT r.*, rt.model_id FROM tests.results r LEFT JOIN tests.routes rt ON rt.id = r.route_id "
        "WHERE r.run_id = %s ORDER BY r.sec_id, r.route_id", (run_id,)).fetchall()
    run_doc = store.get_json(bucket("runs"), f"{run_id}/run.json", default=None) or {}
    strip = env_list("LABEL_STRIP_PREFIXES", ("Claude ",))
    names = model_names(store)
    items_meta = {i["sec_id"]: i for i in catalog["items"]}
    order = {i["sec_id"]: k for k, i in enumerate(catalog["items"])}

    judges: Counter = Counter()
    for r in rows:
        if r["details_key"] and r["verdict"] not in ("not_run",):
            summary = store.get_json(bucket("runs"), f"{run_id}/{r['route_id']}/{r['sec_id']}.summary.json", default=None) or {}
            judges.update(summary.get("judge_models_seen") or {})

    gateways = sorted({r["route_id"].split(":")[1] for r in rows if r["route_id"].startswith("gw:")})
    labels: dict[str, str] = {}
    models: dict[str, dict] = {}
    items: list[dict] = []
    cases = 0
    for sec_id in sorted({r["sec_id"] for r in rows}, key=lambda s: order.get(s, 999)):
        meta = items_meta.get(sec_id, {"sec_id": sec_id, "name": sec_id, "nameEn": sec_id, "rule": {}})
        zh, en = rule_text(meta.get("rule") or {})
        gateway_level = meta.get("layer") == "platform" and int(meta.get("n") or 0) == 0
        res: dict[str, dict] = {}
        groups: dict[str, list[dict]] = {}
        key_model: dict[str, str] = {}
        for r in (x for x in rows if x["sec_id"] == sec_id):
            if r["verdict"] != "not_run" and sec_id != "SEC-11":
                cases += int(r["n"] or 0) * int(r["trials"] or 1)
            if gateway_level:
                g = r["route_id"].split(":")[1] if r["route_id"].startswith("gw:") else "gateway"
                key = "gateway" if len(gateways) <= 1 else f"gateway {g}"
            else:
                model_id = r["model_id"] or r["route_id"].rsplit(":", 1)[-1]
                key = labels.setdefault(model_id, label_for(model_id, names, strip))
                key_model[key] = model_id
            groups.setdefault(key, []).append(r)
        for key, group in groups.items():
            routes = {r["route_id"]: {"v": r["verdict"], "rate": r["rate"], "n": int(r["n"] or 0) * int(r["trials"] or 1),
                                      "upper95": r["upper95"]} for r in group}
            w = worst([r["verdict"] for r in group])
            pick = next(r for r in group if r["verdict"] == w)
            res[key] = {"v": w, "rate": pick["rate"], "n": int(pick["n"] or 0) * int(pick["trials"] or 1),
                        "upper95": pick["upper95"], "routes": routes}
            if not gateway_level:
                m = models.setdefault(key_model[key], {"label": key, "counts": Counter(), "issues": []})
                m["counts"][w] += 1
                if w in ISSUES:
                    m["issues"].append({"id": sec_id, "t": meta.get("name"), "v": w, "rate": pick["rate"], "rule": zh,
                                        "tEn": meta.get("nameEn")})
        items.append({"id": sec_id, "t": meta.get("name"), "tier": meta.get("tier"), "layer": meta.get("layer"), "rule": zh,
                      "ruleEn": en, "res": res, "tEn": meta.get("nameEn")})
    for m in models.values():
        m["counts"] = dict(m["counts"])
    detail = plan["trigger_detail"] or {}
    doc = {
        "runId": run_id,
        "date": plan["created_at"].astimezone(timezone.utc).date().isoformat(),
        "purpose": detail.get("purpose") or run_doc.get("purpose"),
        "litellm": plan["gateway_version"],
        "judge": judge_label(judges, run_doc.get("judge_model"), names, strip),
        "cases": cases,
        "status": plan["status"],
        "models": models,
        "items": items,
    }
    return doc, plan


def publish_run(conn, store, run_id: str, status: str | None = None, catalog: dict | None = None) -> dict:
    """Write the run file, maybe move the pointer, maybe publish. Returns what happened."""
    doc, plan = build_security(conn, store, run_id, catalog)
    status = status or plan["status"]
    curated = bucket("curated")
    key = f"portal/security/{run_id}.json"
    existing = store.get_json(curated, key, default=None)
    if existing != doc:
        if existing is not None:
            log.warning("run file rewritten with different content", extra=kv(run_id=run_id))
        store.put_json(curated, key, doc)
    purposes = env_list("CURRENT_PURPOSES", ("pre-release",))
    current = (store.get_json(curated, "portal/security/current.json", default=None) or {}).get("current")
    move = status != "failed" and doc["purpose"] in purposes and (not current or run_id >= current)
    if move:
        store.put_json(curated, "portal/security/current.json", {"current": run_id, "key": key, "updated_at": iso()})
        publish(conn, "security.results.published", {"run_id": run_id, "key": key}, source=SOURCE,
                dedupe_key=f"security.results.published:{run_id}")
    log.info("security results written", extra=kv(run_id=run_id, key=key, status=status, purpose=doc["purpose"],
                                                   pointer_moved=move, models=len(doc["models"]), items=len(doc["items"])))
    return {"key": key, "pointer_moved": move, "document": doc}


class Handler:
    def __init__(self, store, connection, catalog: dict | None = None):
        self.store, self.connection, self.catalog = store, connection, catalog

    def __call__(self, job: Job) -> None:
        if job.event != "test.run.completed":
            log.warning("job ignored: unknown event", extra=kv(job_id=job.id, event=job.event))
            return
        run_id = job.data.get("run_id")
        if not run_id:
            raise PermanentError("test.run.completed without run_id")
        with self.connection() as conn:
            publish_run(conn, self.store, run_id, job.data.get("status"), self.catalog)
