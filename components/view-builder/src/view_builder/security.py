"""Security results: convert a legacy llmops-portal run file (runs/security/<run_id>.json, schema security-run/v1)
into the portal security shape (the fixture's `security` block, what results-publisher writes to
portal/security/{run_id}.json). Used by `seed`; files already in the portal shape pass through unchanged.
"""
from __future__ import annotations

__all__ = ["is_portal_shape", "from_legacy_run", "to_portal_security"]


def is_portal_shape(doc: dict) -> bool:
    return isinstance(doc, dict) and "runId" in doc and isinstance(doc.get("models"), dict) and isinstance(doc.get("items"), list)


def from_legacy_run(sec: dict) -> dict:
    models = {}
    for t in sec.get("targets") or []:
        label = t["label"]
        mid = t.get("modelId") or t.get("model")
        counts = {"pass": 0, "fail": 0, "review": 0, "info": 0}
        issues = []
        for it in sec.get("items") or []:
            r = (it.get("results") or {}).get(label)
            if not r:
                continue
            v = r.get("verdict")
            counts[v] = counts.get(v, 0) + 1
            if v in ("fail", "review"):
                issues.append({"id": it["id"], "t": it.get("title"), "v": v, "rate": r.get("rate"), "rule": it.get("ruleText")})
        models[mid] = {"label": label, "counts": counts, "issues": issues}
    items = [{"id": it["id"], "t": it.get("title"), "tier": it.get("tier"), "rule": it.get("ruleText"),
              "res": {k: {"v": v.get("verdict"), "rate": v.get("rate"), "n": v.get("n")} for k, v in (it.get("results") or {}).items()}}
             for it in sec.get("items") or []]
    return {"runId": sec["runId"], "date": str(sec.get("startedAt") or "")[:10] or None, "purpose": sec.get("purpose"),
            "litellm": (sec.get("config") or {}).get("litellmVersion"), "judge": (sec.get("judge") or {}).get("label"),
            "cases": sec.get("caseCount"), "models": models, "items": items}


def to_portal_security(doc: dict) -> dict:
    if is_portal_shape(doc):
        return doc
    if isinstance(doc, dict) and "targets" in doc and "items" in doc and "runId" in doc:
        return from_legacy_run(doc)
    raise ValueError("security file is neither the portal shape ({runId, models, items}) nor a security-run/v1 file")
