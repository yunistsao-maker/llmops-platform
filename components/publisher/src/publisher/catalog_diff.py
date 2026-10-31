"""What changed in the catalog between two releases, in the terms of the platform's events (contracts/events.md).

    routes_added     a route is a model offering on a cloud (data/catalog.json: models[].offerings[]) that can be called:
                     {route_id, model_id, provider, region, model_name, status}; one ``route.added`` event each
    prices_changed   price sheets of an offering that exists in both releases: {model_id, offering, provider, scope,
                     before: {field: value}, after: {field: value}} (only the fields that differ; a sheet that is new or
                     gone has before / after null)
    retirements      an offering that is on its way out: its status became legacy / deprecated / extended-access /
                     retired, its retirementDate or deprecationDate was set or moved, or it left the catalog:
                     {model_id, offering, provider, change, before, after}
    status_changes   (for ``model.status_changed``, not part of release.published) a model's platform status moved:
                     {model_id, model_name, from, to} in the contract's terms - see PLATFORM_STATUS

Routes.  The offering's cloud and service name the provider of the platform's route ids
(``direct:<provider>:<region>:<model_id>``, tests.routes.provider): Bedrock -> bedrock, Microsoft Foundry -> azure,
Google Cloud Agent Platform -> vertex, the Anthropic API -> anthropic.  Offerings of other services (the OpenAI and Gemini
APIs) have no provider on the platform and are not routes.  Offerings that cannot be called (status announced or
retired) are not routes either.  The catalog does not say which region the platform calls a cloud in: that is
``ROUTE_REGION_<PROVIDER>`` (defaults below).  The model name is what the provider's API is called with: for Bedrock the
inference profile that fits the region (global.* when there is one), otherwise the provider's model id.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping

CATALOG = "data/catalog.json"
#: (cloud, service) -> route provider (tests.routes.provider CHECK: bedrock | azure | vertex | anthropic | gateway | mock)
PROVIDERS: dict[tuple[str, str], str] = {
    ("aws", "bedrock"): "bedrock",
    ("azure", "foundry"): "azure",
    ("gcp", "agent-platform"): "vertex",
    ("gcp", "vertex-ai"): "vertex",
    ("vendor-direct", "anthropic-api"): "anthropic",
}
DEFAULT_REGIONS = {"bedrock": "us-east-1", "vertex": "global", "azure": "global", "anthropic": "global"}
NOT_CALLABLE = ("announced", "retired")
RETIRING = ("legacy", "deprecated", "extended-access", "retired")
#: The catalog's platformStatus (the platform team's decision about a model, schema PlatformStatus) in the terms of
#: model.status_changed (contracts/events.md: watch, evaluating, trial, available, paused, retired).  beta is the trial
#: stage, ga is available; deprecating models can still be requested; restricted and not-planned cannot (paused, no ETA).
PLATFORM_STATUS = {"watch": "watch", "evaluating": "evaluating", "beta": "trial", "ga": "available", "deprecating": "available",
                   "restricted": "paused", "not-planned": "paused", "removed": "retired"}
#: a model that was not in the catalog before was at most being watched
NOT_LISTED = "watch"
PRICE_FIELDS = ("input", "output", "cacheRead", "cacheWrite", "cacheWrite1h", "batchDiscountPct", "validFrom", "validUntil")
NESTED_PRICE_FIELDS = ("longContext", "thereafter")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]*")
#: Bedrock geography prefixes of inference profiles, by the region's prefix
_GEO_PREFIX = (("us-gov-", "us-gov"), ("us-", "us"), ("ca-", "us"), ("eu-", "eu"), ("ap-northeast-1", "jp"), ("ap-northeast-3", "jp"),
               ("ap-southeast-2", "au"), ("ap-southeast-4", "au"), ("ap-", "apac"))


def route_regions(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Region per provider: ``ROUTE_REGION_BEDROCK`` etc., else DEFAULT_REGIONS."""
    e = os.environ if env is None else env
    return {p: (e.get(f"ROUTE_REGION_{p.upper()}") or "").strip() or r for p, r in DEFAULT_REGIONS.items()}


def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _list(v: Any) -> list:
    return v if isinstance(v, list) else []


def load_catalog(raw: bytes | None) -> dict:
    if not raw:
        return {}
    try:
        doc = json.loads(raw)
    except ValueError:
        return {}
    return doc if isinstance(doc, dict) else {}


def offerings(catalog: dict) -> dict[str, tuple[dict, dict]]:
    """offering key -> (model, offering).  The key is the offering's id, else "<model id>@<service>"."""
    out: dict[str, tuple[dict, dict]] = {}
    for model in _list(catalog.get("models")):
        if not isinstance(model, dict) or not isinstance(model.get("id"), str):
            continue
        for off in _list(model.get("offerings")):
            if isinstance(off, dict):
                key = off.get("id") if isinstance(off.get("id"), str) and off.get("id") else f"{model['id']}@{off.get('service')}"
                out.setdefault(key, (model, off))
    return out


def provider_of(offering: dict) -> str | None:
    return PROVIDERS.get((str(offering.get("cloud") or ""), str(offering.get("service") or "")))


def _plain(name: Any) -> str | None:
    """The leading identifier of a model id as the catalog writes it ("gpt-6-astra（version 2026-09-03）" -> "gpt-6-astra")."""
    m = _NAME.match(str(name or "").strip())
    return m.group(0) if m else None


def model_name(offering: dict, provider: str, region: str) -> str | None:
    if provider == "bedrock":
        targets = [t for t in _list(offering.get("inferenceTargets")) if isinstance(t, dict) and _plain(t.get("id"))]
        geo = next((g for prefix, g in _GEO_PREFIX if region.startswith(prefix)), None)
        for wanted in ("global", "geo", "in-region"):
            for t in targets:
                tid = _plain(t.get("id"))
                if t.get("scope") != wanted:
                    continue
                if wanted == "geo" and geo and not tid.startswith(geo + "."):
                    continue
                return tid
    return _plain(offering.get("providerModelId"))


def routes(catalog: dict, regions: Mapping[str, str] | None = None) -> dict[str, dict]:
    """route id -> {route_id, model_id, provider, region, model_name, status} for every callable offering."""
    regions = dict(DEFAULT_REGIONS, **(regions or {}))
    out: dict[str, dict] = {}
    for _key, (model, off) in offerings(catalog).items():
        provider = provider_of(off)
        status = str(off.get("status") or "")
        if provider is None or status in NOT_CALLABLE:
            continue
        region = regions[provider]
        name = model_name(off, provider, region)
        if not name:
            continue
        route_id = f"direct:{provider}:{region}:{model['id']}"
        out.setdefault(route_id, {"route_id": route_id, "model_id": model["id"], "provider": provider, "region": region,
                                  "model_name": name, "status": status})
    return out


def _price_values(sheet: dict) -> dict:
    out = {k: sheet[k] for k in PRICE_FIELDS if k in sheet and not isinstance(sheet[k], (dict, list))}
    for nested in NESTED_PRICE_FIELDS:
        for k, v in _dict(sheet.get(nested)).items():
            if not isinstance(v, (dict, list)):
                out[f"{nested}.{k}"] = v
    return out


def _sheets(offering: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i, sheet in enumerate(_list(offering.get("pricing"))):
        if isinstance(sheet, dict):
            scope = str(sheet.get("scope") or f"#{i}")
            key = scope if scope not in out else f"{scope}#{i}"
            out[key] = _price_values(sheet)
    return out


def diff_catalogs(old: dict, new: dict, regions: Mapping[str, str] | None = None) -> dict[str, list]:
    old_routes, new_routes = routes(old, regions), routes(new, regions)
    added = [new_routes[r] for r in new_routes if r not in old_routes]

    old_off, new_off = offerings(old), offerings(new)
    prices, retirements = [], []
    for key, (model, off) in new_off.items():
        if key not in old_off:
            continue
        _old_model, before = old_off[key]
        common = {"model_id": model["id"], "offering": key, "provider": provider_of(off)}
        was, now = _sheets(before), _sheets(off)
        for scope in list(was) + [s for s in now if s not in was]:
            a, b = was.get(scope), now.get(scope)
            if a == b:
                continue
            if a is None or b is None:
                prices.append({**common, "scope": scope, "before": a, "after": b})
            else:
                fields = sorted(set(a) | set(b), key=lambda f: (f not in PRICE_FIELDS, f))
                changed = [f for f in fields if a.get(f) != b.get(f)]
                prices.append({**common, "scope": scope, "before": {f: a.get(f) for f in changed}, "after": {f: b.get(f) for f in changed}})
        status_was, status_now = before.get("status"), off.get("status")
        if status_now != status_was and status_now in RETIRING:
            retirements.append({**common, "change": "status", "before": status_was, "after": status_now})
        for field in ("retirementDate", "deprecationDate"):
            a, b = _dict(before.get("lifecycle")).get(field), _dict(off.get("lifecycle")).get(field)
            if b and a != b:
                retirements.append({**common, "change": field, "before": a, "after": b})
    for key, (model, off) in old_off.items():
        if key not in new_off:
            retirements.append({"model_id": model["id"], "offering": key, "provider": provider_of(off), "change": "removed",
                                "before": off.get("status"), "after": None})
    return {"routes_added": added, "prices_changed": prices, "retirements": retirements, "status_changes": status_changes(old, new)}


def _models(catalog: dict) -> dict[str, dict]:
    return {m["id"]: m for m in _list(catalog.get("models")) if isinstance(m, dict) and isinstance(m.get("id"), str)}


def status_changes(old: dict, new: dict) -> list[dict]:
    """Models whose platform status (mapped by PLATFORM_STATUS) is different in ``new``; a model new to the catalog
    comes from "watch".  A status this code does not know is left out (nobody can be told what it means)."""
    out = []
    before = _models(old)
    for model_id, model in _models(new).items():
        to = PLATFORM_STATUS.get(str(model.get("platformStatus") or ""))
        was = PLATFORM_STATUS.get(str((before.get(model_id) or {}).get("platformStatus") or "")) if model_id in before else NOT_LISTED
        if to and was and to != was:
            out.append({"model_id": model_id, "model_name": str(model.get("name") or model_id), "from": was, "to": to})
    return out


def diff_release_files(old_files: Mapping[str, bytes] | None, new_files: Mapping[str, bytes],
                       regions: Mapping[str, str] | None = None) -> dict[str, list]:
    """``changes`` of release.published for two releases' files (the catalog is all that is compared)."""
    return diff_catalogs(load_catalog((old_files or {}).get(CATALOG)), load_catalog(new_files.get(CATALOG)), regions)


def status_event(change: dict) -> dict:
    """The ``model.status_changed`` payload: {model_id, model_name, from, to} (no eta: the catalog has none)."""
    return {k: change[k] for k in ("model_id", "model_name", "from", "to")}


def route_event(route: dict) -> dict:
    """The ``route.added`` payload: {model_id, provider, region, model_name, status}."""
    return {k: route[k] for k in ("model_id", "provider", "region", "model_name", "status")}
