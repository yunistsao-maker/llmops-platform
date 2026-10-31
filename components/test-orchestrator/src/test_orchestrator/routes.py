"""tests.routes: callable targets, from ROUTES_FILE (configured direct and gateway routes) and route.added events.

Route ids (contracts/events.md): direct:<provider>:<region>:<model_id> or gw:<gateway>:<model_id>.

ROUTES_FILE (JSON):
    {
      "baseline_model_id": "claude-sonnet-5-5",          optional: the production model for not_worse_than_baseline
      "provider_defaults": {                             optional: completes targets of routes created from events
        "azure":  {"endpoint": "https://<res>.services.ai.azure.com", "api_version": "2025-04-01-preview"},
        "vertex": {"project": "<gcp project>"}
      },
      "routes": [
        {"model_id": "claude-sonnet-5-5", "kind": "gateway", "provider": "gateway", "gateway": "onprem",
         "target": {"model": "claude-sonnet-5-5"}},
        {"model_id": "claude-sonnet-5-5", "kind": "direct", "provider": "bedrock", "region": "us-east-1",
         "target": {"model": "global.anthropic.claude-sonnet-5-5", "region": "us-east-1"}, "enabled": true}
      ]
    }
"id" may be given; otherwise it is derived. Routes in the file are the source of truth: a sync overwrites them.
"""
from __future__ import annotations

import json
from pathlib import Path

from llmops_common.log import get_logger, kv

log = get_logger("test_orchestrator.routes")

PROVIDERS = ("bedrock", "azure", "vertex", "anthropic", "gateway", "mock")
PROVIDER_ALIASES = {
    "aws": "bedrock", "bedrock": "bedrock", "amazon-bedrock": "bedrock",
    "azure": "azure", "foundry": "azure", "azure-foundry": "azure", "azure-ai-foundry": "azure",
    "gcp": "vertex", "vertex": "vertex", "vertex-ai": "vertex", "vertex_ai": "vertex", "google": "vertex", "agent-platform": "vertex",
    "anthropic": "anthropic", "claude-api": "anthropic",
    "gateway": "gateway", "mock": "mock",
}
RETIRED = {"retired", "deprecated", "removed", "disabled"}


class RouteError(ValueError):
    pass


def make_route_id(kind: str, provider: str, model_id: str, region: str | None = None, gateway: str | None = None) -> str:
    if kind == "gateway":
        return f"gw:{gateway or 'onprem'}:{model_id}"
    return f"direct:{provider}:{region or 'global'}:{model_id}"


def gateway_of(route_id: str) -> str | None:
    parts = route_id.split(":")
    return parts[1] if len(parts) >= 3 and parts[0] == "gw" else None


def normalise(raw: dict) -> dict:
    model_id = raw.get("model_id")
    kind = raw.get("kind") or ("gateway" if raw.get("provider") == "gateway" else "direct")
    provider = PROVIDER_ALIASES.get(str(raw.get("provider") or "").lower(), raw.get("provider"))
    if not model_id:
        raise RouteError(f"route without model_id: {raw}")
    if kind not in ("direct", "gateway"):
        raise RouteError(f"route {model_id}: kind must be direct or gateway")
    if provider not in PROVIDERS:
        raise RouteError(f"route {model_id}: provider {raw.get('provider')!r} is not one of {', '.join(PROVIDERS)}")
    if kind == "gateway" and provider != "gateway":
        raise RouteError(f"route {model_id}: a gateway route uses provider gateway")
    target = dict(raw.get("target") or {})
    if provider == "gateway":
        target.setdefault("model", model_id)
    region = raw.get("region") or target.get("region")
    rid = raw.get("id") or make_route_id(kind, provider, model_id, region, raw.get("gateway"))
    if kind == "gateway" and not rid.startswith("gw:"):
        raise RouteError(f"route {rid}: gateway route ids start with gw:<gateway>:")
    if kind == "direct" and not rid.startswith("direct:"):
        raise RouteError(f"route {rid}: direct route ids start with direct:<provider>:<region>:")
    return {"id": rid, "model_id": model_id, "kind": kind, "provider": provider, "target": target,
            "enabled": bool(raw.get("enabled", True))}


def load_routes_file(path: str | None) -> dict:
    if not path:
        return {"routes": [], "provider_defaults": {}, "baseline_model_id": None}
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(doc, list):
        doc = {"routes": doc}
    return {"routes": [normalise(r) for r in doc.get("routes", [])], "provider_defaults": doc.get("provider_defaults") or {},
            "baseline_model_id": doc.get("baseline_model_id")}


def upsert_routes(conn, routes: list[dict], *, overwrite: bool = True) -> int:
    """Insert routes; with overwrite (ROUTES_FILE) also update existing ones. Returns how many rows changed."""
    from psycopg.types.json import Jsonb

    changed = 0
    for r in routes:
        if overwrite:
            sql = ("INSERT INTO tests.routes (id, model_id, kind, provider, target, enabled) VALUES (%s, %s, %s, %s, %s, %s) "
                   "ON CONFLICT (id) DO UPDATE SET model_id = EXCLUDED.model_id, kind = EXCLUDED.kind, "
                   "provider = EXCLUDED.provider, target = EXCLUDED.target, enabled = EXCLUDED.enabled")
        else:
            sql = ("INSERT INTO tests.routes (id, model_id, kind, provider, target, enabled) VALUES (%s, %s, %s, %s, %s, %s) "
                   "ON CONFLICT (id) DO NOTHING")
        cur = conn.execute(sql, (r["id"], r["model_id"], r["kind"], r["provider"], Jsonb(r["target"]), r["enabled"]))
        changed += cur.rowcount or 0
    return changed


def route_from_event(payload: dict, provider_defaults: dict) -> dict | None:
    """route.added {model_id, provider, region, model_name, status} -> a direct route, or None if unusable."""
    model_id = payload.get("model_id")
    provider = PROVIDER_ALIASES.get(str(payload.get("provider") or "").lower())
    if not model_id or not provider:
        log.warning("route.added ignored: unknown provider", extra=kv(model_id=model_id, provider=payload.get("provider")))
        return None
    region = payload.get("region") or None
    name = payload.get("model_name") or model_id
    defaults = dict(provider_defaults.get(provider) or {})
    if provider == "gateway":
        return normalise({"model_id": model_id, "kind": "gateway", "provider": "gateway", "gateway": payload.get("gateway"),
                          "target": {"model": name}})
    if provider == "bedrock":
        target = {"model": name, "region": region or defaults.get("region") or "us-east-1"}
        region = target["region"]
    elif provider == "azure":
        target = {**defaults, "deployment": name}
    elif provider == "vertex":
        target = {**defaults, "region": region or defaults.get("region") or "global", "model": name}
        region = target["region"]
    else:
        target = {**defaults, "model": name}
    complete = {"azure": ("endpoint", "deployment"), "vertex": ("project", "region", "model")}.get(provider, ("model",))
    enabled = all(target.get(k) for k in complete) and str(payload.get("status") or "").lower() not in RETIRED
    if not enabled:
        log.warning("route.added stored disabled", extra=kv(model_id=model_id, provider=provider,
                                                            missing=[k for k in complete if not target.get(k)]))
    return normalise({"model_id": model_id, "kind": "direct", "provider": provider, "region": region, "target": target,
                      "enabled": enabled})


def list_routes(conn, *, enabled_only: bool = True, model_id: str | None = None, ids: list[str] | None = None,
                gateway: str | None = None) -> list[dict]:
    where, args = [], []
    if enabled_only:
        where.append("enabled")
    if model_id:
        where.append("model_id = %s")
        args.append(model_id)
    if ids:
        where.append("id = ANY(%s)")
        args.append(list(ids))
    if gateway:
        where.append("kind = 'gateway' AND id LIKE %s")
        args.append(f"gw:{gateway}:%")
    sql = "SELECT id, model_id, kind, provider, target, enabled, first_seen FROM tests.routes"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY first_seen, id"
    return list(conn.execute(sql, args).fetchall())
