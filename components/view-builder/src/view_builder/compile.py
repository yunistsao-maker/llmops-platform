"""Compile the portal view model from release data, the editorial overlay, security results and the SEC matrix.

Pure functions only (no storage, no clock): `build_view(inputs, today)` returns the view dict plus warnings.
The record shapes follow contracts/fixtures/portal-view.sample.json, which the prototype produced with
build_data.py + augment.py; this module is their port. Hand-written copy lives in the editorial overlay
(editorial/current.json shape, see README) instead of code.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Inputs", "BuildError", "build_view", "matrix_view", "availability", "REQUIRED_FILES", "OPTIONAL_FILES",
           "MODEL_ID_RE", "DEFAULT_DURATIONS"]

# release data files (relative to portal/releases/{id}/data/) the view needs; models/<id>.json are optional
REQUIRED_FILES = ("catalog.json", "radar.json", "lifecycle.json", "sources.json", "open-models.json", "requirements.json")
OPTIONAL_FILES = ("litellm.json",)
MODEL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,80}$")

SECTION = {"newModels": "上架", "pricingChanges": "價格", "lifecycleChanges": "退役", "litellm": "Gateway",
           "security": "資安", "regulatory": "法規"}
CLOUD_KEY = {"aws": "aws", "azure": "azure", "gcp": "gcp", "vendor-direct": "direct"}
PRIORITY = {"P0": 0, "P1": 1, "P2": 2}
HEAT = {"hit": 0, "rising": 1, "watch": 2}
HOST_RANK = {"ga": 3, "preview": 2, "restricted": 1}
OFFERING_STD = ("status", "service", "since", "price", "geoPrice", "taipei", "residency", "twResidency", "so", "soNote",
                "eol", "litellm", "src")

# availability: standard working days per rollout gate (editorial `availability.durations` overrides them);
# G3-G7 (compatibility, performance, quality, security, cost) run in parallel, so they count once, as the longest
DEFAULT_DURATIONS = {"G1": 3, "G2": 10, "G3": 3, "G4": 3, "G5": 5, "G6": 5, "G7": 2, "G8": 14, "G9": 2}
DEFAULT_PARALLEL = ("G3", "G4", "G5", "G6", "G7")
ROLLOUT = ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9")
AVAIL_STATES = ("available", "trial", "evaluating", "paused")


class BuildError(ValueError):
    """The inputs cannot produce a view (missing or malformed release data). Retrying will not help."""


@dataclass
class Inputs:
    data: dict[str, Any]                  # "catalog.json" -> parsed JSON, ..., "models/<id>.json" -> deep dive
    editorial: dict | None = None         # portal/editorial/current.json
    security: dict | None = None          # portal/security/{run_id}.json (fixture `security` shape)
    matrix: dict | None = None            # portal/security-matrix/current.json (contracts/fixtures/security-matrix.json)
    source: str = ""                      # provenance label shown as `repo`
    warnings: list[str] = field(default_factory=list)

    def file(self, name: str) -> Any:
        try:
            return self.data[name]
        except KeyError as exc:
            raise BuildError(f"release data file {name} is missing") from exc


# ------------------------------------------------------------------------------------------------ helpers
def _sources(data: Inputs) -> dict[str, dict]:
    return {s["id"]: s for s in (data.file("sources.json").get("sources") or []) if isinstance(s, dict) and s.get("id")}


def _srcs(src: dict[str, dict], refs, n: int = 3) -> list[dict]:
    out = []
    for r in refs or []:
        s = src.get((r or {}).get("id")) if isinstance(r, dict) else None
        if s and s.get("url"):
            out.append({"t": str(s.get("title", ""))[:90], "u": s["url"], "p": s.get("publisher", "")})
        if len(out) >= n:
            break
    return out


def _first_price(o: dict):
    pr = o.get("pricing") or []
    g = next((p for p in pr if str(p.get("scope", "")).startswith(("global", "standard"))), pr[0] if pr else None)
    geo = next((p for p in pr if p is not g and "ultrafast" not in str(p.get("scope", ""))), None)

    def f(p):
        return None if not p else {"scope": p.get("scope"), "in": p.get("input"), "out": p.get("output")}

    return f(g), f(geo)


def _taipei(o: dict):
    if o.get("cloud") != "aws":
        return None
    a = next((x for x in o.get("apacAvailability") or [] if x.get("region") == "ap-east-2"), None)
    if not a:
        return "none"
    return "global" if "global" in (a.get("scopes") or []) else ("geo" if a.get("scopes") else "none")


def _feature(o: dict, name: str):
    return next((f.get("status") for f in o.get("features") or [] if f.get("feature") == name), None)


def _first_sentence(text: str, limit: int = 90) -> str:
    m = re.match(r"(.+?(?:[。！？]|[.!?](?=\s|$)))", text or "")
    return (m.group(1) if m else (text or ""))[:limit]


def _clause(text: str, limit: int = 60) -> str:
    """First clause of a long catalog bullet, for models without hand-written copy."""
    head = re.split(r"[；;。]", text or "", maxsplit=1)[0].strip()
    return head if len(head) <= limit else head[: limit - 1] + "…"


def _date(value) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------------------------------------ models
def _offerings(m: dict, src: dict, patches: list[dict]) -> dict:
    offs: dict[str, dict] = {}
    for o in m.get("offerings") or []:
        key = CLOUD_KEY.get(o.get("cloud"))
        if not key or key in offs:
            continue
        g, geo = _first_price(o)
        dp = o.get("dataProcessing") or {}
        offs[key] = {"status": o.get("status"), "service": o.get("service"), "since": o.get("availableSince"),
                     "price": g, "geoPrice": geo, "taipei": _taipei(o), "residency": dp.get("residencyOptions") or [],
                     "twResidency": dp.get("taiwanResidency"), "so": _feature(o, "structured-outputs"), "soNote": None,
                     "eol": (o.get("lifecycle") or {}).get("eolNoSoonerThan"), "litellm": o.get("litellmModel"),
                     "src": _srcs(src, o.get("sources"), 2), "zdr": dp.get("zdr")}
    for p in patches:
        if p.get("model") != m["id"]:
            continue
        cloud = p.get("cloud")
        if "set" in p and cloud in offs:
            offs[cloud].update(p["set"])
        if "add" in p and cloud not in offs:
            a = p["add"]
            off = {"status": a.get("status"), "since": a.get("since"), "note": a.get("note"), "price": None,
                   "geoPrice": None, "taipei": None, "residency": [], "so": None, "eol": None, "litellm": None,
                   "src": a.get("src") or []}
            off.update({k: v for k, v in a.items() if k not in off})
            offs[cloud] = off
        if "default" in p and cloud in offs:
            for k, v in p["default"].items():
                if offs[cloud].get(k) in (None, "", []):
                    offs[cloud][k] = v
    return offs


def _copy(m: dict, ed: dict | None, deep_breaking: list, warnings: list[str]) -> tuple[dict, dict]:
    """Plain-language copy: hand-written when the editorial overlay has it, else derived from the catalog."""
    ed = ed or {}
    summary = _first_sentence(m.get("summary") or "")
    hi = [_clause(s) for s in (m.get("strengths") or [])[:2]]
    cau = [_clause(s) for s in (m.get("weaknesses") or [])[:2]]
    if not ed:
        warnings.append(f"model {m['id']}: no editorial copy, using catalog summary / strengths / weaknesses")
    c = {
        "one": ed.get("one") or summary, "hi": ed.get("hi") or hi, "cau": ed.get("cau") or cau,
    }
    hi_z, cau_z = ed.get("hiZ") or c["hi"], ed.get("cauZ") or c["cau"]
    plain = {
        "oneZ": ed.get("oneZ") or c["one"], "oneE": ed.get("oneE") or "",
        "hiZ": hi_z, "hiE": ed.get("hiE") or hi_z, "cauZ": cau_z, "cauE": ed.get("cauE") or cau_z,
    }
    br_e = ed.get("brE") or []
    if br_e and len(br_e) != len(deep_breaking):
        warnings.append(f"model {m['id']}: editorial brE has {len(br_e)} lines for {len(deep_breaking)} breaking "
                        "changes; English falls back to the Chinese titles")
        br_e = []
    plain["brE"] = br_e
    return c, plain


def _models(inp: Inputs, src: dict) -> list[dict]:
    cat = inp.file("catalog.json")
    ed_models = (inp.editorial or {}).get("models") or {}
    patches = (inp.editorial or {}).get("offerings") or []
    out = []
    for m in cat.get("models") or []:
        if not MODEL_ID_RE.match(str(m.get("id", ""))):
            raise BuildError(f"catalog model id {m.get('id')!r} is not a valid id")
        deep = inp.data.get(f"models/{m['id']}.json")
        breaking = [{"t": b.get("title"), "sev": b.get("severity")} for b in (deep or {}).get("breakingChanges", [])][:6]
        aa = next((b for b in m.get("benchmarks") or [] if "Artificial Analysis" in str(b.get("name")) and b.get("rank")), None)
        pref = next((p for p in m.get("performanceReference") or [] if p.get("outputTokensPerSec")), None)
        copy, plain = _copy(m, ed_models.get(m["id"]), breaking, inp.warnings)
        rec = {
            "id": m["id"], "name": m.get("name"), "vendor": m.get("vendor"), "tier": m.get("tier"),
            "release": m.get("releaseDate"), "status": m.get("platformStatus"), "ctx": m.get("contextWindow"),
            "maxOut": m.get("maxOutputTokens"),
            "aa": {"score": aa.get("score"), "rank": aa.get("rank"), "date": aa.get("date")} if aa else None,
            "speed": {"tps": pref.get("outputTokensPerSec"), "costPerTask": pref.get("costPerIndexTaskUsd"),
                      "date": pref.get("date")} if pref else None,
            **copy,
            "offs": _offerings(m, src, patches), "deepDive": bool(deep),
            "tldr": deep.get("tldr") if deep else None, "breaking": breaking,
            "modelCard": m.get("modelCardUrl"), "src": _srcs(src, m.get("sources"), 3),
            **plain,
            "gw": _gateway(m),
        }
        out.append(rec)
    return out


def _gateway(m: dict) -> dict:
    """How the model is reached through the company gateway: alias (= catalog id, see llm-gateway config),
    the LiteLLM provider strings per service and the version notes from the catalog."""
    lit = m.get("litellm") or {}
    return {"alias": m["id"],
            "providers": [{"svc": p.get("service"), "model": p.get("model"), "note": p.get("note")}
                          for p in lit.get("providerStrings") or [] if isinstance(p, dict) and p.get("model")],
            "minVersion": lit.get("minVersionRouting"), "recVersion": lit.get("recommendedVersion"),
            "caveats": [str(c) for c in (lit.get("caveats") or [])[:4]]}


def _platform_gateway(inp: Inputs) -> dict | None:
    """Platform-wide LiteLLM versions (litellm.json, optional): recommended, minimum baseline, versions to avoid."""
    lit = inp.data.get("litellm.json")
    if not isinstance(lit, dict):
        return None
    avoid = [str(a.get("versions")) for a in lit.get("avoid") or [] if isinstance(a, dict) and a.get("versions")]
    return {"recommended": (lit.get("recommended") or {}).get("version"),
            "minimum": (lit.get("minimumBaseline") or {}).get("version"), "avoid": avoid[:6], "asOf": lit.get("asOf")}


def availability(st: dict | None, today: dt.date, cfg: dict | None = None, override: dict | None = None,
                 warnings: list[str] | None = None, model_id: str = "") -> dict:
    """Whether a model can be requested, from its rollout gates (or the editorial override).

    available = G9 done; trial = G8 in progress; paused = a gate is blocked (no ETA; next_gate is that gate);
    otherwise evaluating, with eta = today + the standard days of the gates not done yet.
    """
    st = st or {}
    cfg = cfg or {}
    durations = dict(DEFAULT_DURATIONS)
    for k, v in (cfg.get("durations") or {}).items():
        if k in DEFAULT_DURATIONS and isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
            durations[k] = int(v)
    parallel = tuple(g for g in (cfg.get("parallel") or DEFAULT_PARALLEL) if g in DEFAULT_DURATIONS)
    pending = [g for g in ROLLOUT if st.get(g) != "done"]
    blocked = [g for g in ("G0",) + ROLLOUT if st.get(g) == "blocked"]

    def eta() -> str:
        seq = sum(durations[g] for g in pending if g not in parallel)
        par = max([durations[g] for g in pending if g in parallel] or [0])
        return (today + dt.timedelta(days=seq + par)).isoformat()

    if st.get("G9") == "done":
        out = {"state": "available", "eta": None, "next_gate": None}
    elif st.get("G8") == "doing":
        out = {"state": "trial", "eta": eta(), "next_gate": pending[0] if pending else None}
    elif blocked:
        out = {"state": "paused", "eta": None, "next_gate": blocked[0]}
    else:
        out = {"state": "evaluating", "eta": eta(), "next_gate": pending[0] if pending else None}
    out["basis"] = "gates"
    if isinstance(override, dict):
        if override.get("state") is not None:
            if override["state"] in AVAIL_STATES:
                out["state"] = override["state"]
                out["basis"] = "editorial"
                if out["state"] in ("available", "paused"):
                    out["eta"] = None
                if out["state"] == "available":
                    out["next_gate"] = None
            elif warnings is not None:
                warnings.append(f"availability override for {model_id}: unknown state {override['state']!r}")
        if "eta" in override:
            if override["eta"] is None or _date(override["eta"]) is not None:
                out["eta"] = override["eta"] if override["eta"] is None else str(override["eta"])[:10]
                out["basis"] = "editorial"
            elif warnings is not None:
                warnings.append(f"availability override for {model_id}: bad eta {override['eta']!r}")
        if override.get("note"):
            out["note"] = str(override["note"])
    return out


# ------------------------------------------------------------------------------------------------ radar / lifecycle
def _news(inp: Inputs, src: dict, models: list[dict], cutoff: str) -> list[dict]:
    radar = inp.file("radar.json")
    name_map = [(mm["name"], mm["id"]) for mm in models if mm.get("name")]
    name_map += [(mm["name"].replace("Claude ", ""), mm["id"]) for mm in models if mm.get("name")]
    news = []
    for sec_key, label in SECTION.items():
        for c in radar.get(sec_key) or []:
            if str(c.get("lastSeen", "")) < cutoff:
                continue
            text = str(c.get("title", "")) + " " + str(c.get("detail", ""))
            linked = sorted({mid for nm, mid in name_map if nm and nm in text})
            news.append({"id": c["id"], "sec": label, "date": c.get("date") or c["lastSeen"], "last": c["lastSeen"],
                         "first": c.get("firstSeen"), "t": c.get("title", ""), "d": c.get("detail", ""),
                         "imp": c.get("impact", "low"), "clouds": c.get("clouds") or [], "vendor": c.get("vendor"),
                         "models": linked, "upd": len(c.get("updates") or []),
                         "isNew": c.get("firstSeen") == radar.get("asOf"), "src": _srcs(src, c.get("sources"), 3)})
    news.sort(key=lambda x: (x["last"], x["date"] or ""), reverse=True)
    return news


def _actions(inp: Inputs) -> list[dict]:
    radar = inp.file("radar.json")
    actions = [{"p": a.get("priority"), "due": a.get("due"), "t": a.get("action", ""), "models": a.get("relatedModelIds") or [],
                "first": a.get("firstSeen"), "last": a.get("lastSeen")} for a in radar.get("actionItems") or []]
    actions.sort(key=lambda a: (PRIORITY.get(a["p"], 3), a["due"] or "9999"))
    return actions


def _open_models(inp: Inputs, src: dict) -> list[dict]:
    om = inp.file("open-models.json")
    opens = []
    for m in om.get("models") or []:
        host: dict[str, str] = {}
        for h in m.get("hosting") or []:
            st = h.get("status")
            rank = HOST_RANK.get(st, 0)
            if rank > HOST_RANK.get(host.get(h.get("cloud")), 0) or h.get("cloud") not in host:
                host[h.get("cloud")] = st
        opens.append({"id": m["id"], "name": m.get("name"), "dev": m.get("developer"), "origin": m.get("origin"),
                      "release": m.get("releaseDate"), "heat": m.get("heat"),
                      "license": (m.get("weights") or {}).get("license"), "one": _first_sentence_cn(m.get("summary") or ""),
                      "host": host, "src": _srcs(src, m.get("sources"), 2)})
    opens.sort(key=lambda x: (HEAT.get(x["heat"], 3), x["release"] or ""))
    return opens


def _first_sentence_cn(text: str) -> str:
    m = re.match(r"(.+?。)", text)
    return (m.group(1) if m else text)[:90]


def _retirements(inp: Inputs, src: dict, today: dt.date, past_days: int) -> list[dict]:
    life = inp.file("lifecycle.json")
    ret = []
    for e in life.get("events") or []:
        d = _date(e.get("date"))
        if d is None:
            inp.warnings.append(f"lifecycle event {e.get('id')}: bad date {e.get('date')!r}")
            continue
        days = (d - today).days
        if days < -past_days or e.get("event") in ("price-change",):
            continue
        ret.append({"id": e.get("id"), "date": e["date"], "days": days, "cloud": e.get("cloud"), "svc": e.get("service"),
                    "ev": e.get("event"), "name": e.get("modelName"), "pmid": e.get("providerModelId"),
                    "repl": e.get("replacement"), "scope": e.get("scopeNote"), "src": _srcs(src, e.get("sources"), 1)})
    ret.sort(key=lambda x: x["date"])
    return ret


# ------------------------------------------------------------------------------------------------ tests
EMPTY_SECURITY = {"runId": None, "date": None, "purpose": None, "litellm": None, "judge": None, "cases": None,
                  "models": {}, "items": []}


def _security(inp: Inputs, names: dict[str, tuple[str, str]]) -> dict:
    s = inp.security
    if not isinstance(s, dict) or not isinstance(s.get("items"), list) or not isinstance(s.get("models"), dict):
        if s is not None:
            inp.warnings.append("security results are not in the portal shape; showing no results")
        return dict(EMPTY_SECURITY)
    import copy

    s = copy.deepcopy(s)
    for it in s["items"]:
        if it.get("id") in names:
            it["t"], it["tEn"] = names[it["id"]]
        else:
            it.setdefault("tEn", it.get("t"))
    for r in s["models"].values():
        for i in r.get("issues") or []:
            if i.get("id") in names:
                i["t"], i["tEn"] = names[i["id"]]
            else:
                i.setdefault("tEn", i.get("t"))
    return s


def matrix_view(matrix: dict | None) -> dict:
    """contracts/fixtures/security-matrix.json -> the short-key `secMatrix` block the page reads."""
    if not matrix:
        return {"intro": [], "introEn": [], "reading": [], "readingEn": [], "items": []}
    items = []
    for it in matrix.get("items") or []:
        x = {"id": it["id"], "tier": it.get("tier"), "pri": it.get("priority"), "n": it.get("name"),
             "nEn": it.get("nameEn") or it.get("name"), "owasp": it.get("owasp2026") or [], "other": it.get("otherLists") or [],
             "atlas": it.get("mitreAtlas") or [], "arch": it.get("architecture") or "",
             "archEn": it.get("architectureEn") or it.get("architecture") or "", "as": it.get("architectureSet") or [],
             "moda": it.get("modaRiskCategory") or [], "modaEn": it.get("modaRiskCategoryEn") or [],
             "what": it.get("what") or "", "whatEn": it.get("whatEn") or it.get("what") or "",
             "pass": it.get("passCriteria") or "", "passEn": it.get("passCriteriaEn") or it.get("passCriteria") or "",
             "tools": it.get("tools") or "", "toolsEn": it.get("toolsEn") or it.get("tools") or "",
             "users": it.get("users") or "", "usersEn": it.get("usersEn") or it.get("users") or "",
             "unit": it.get("testingUnit") or "", "unitEn": it.get("testingUnitEn") or it.get("testingUnit") or "",
             "refs": it.get("references") or "", "rel": it.get("related") or {"papers": 0, "posts": 0, "cases": 0}}
        k = "note" if it.get("tier") == "core" else "why"
        x[k] = it.get(k) or ""
        x[k + "En"] = it.get(k + "En") or x[k]
        items.append(x)
    return {"intro": matrix.get("intro") or [], "introEn": matrix.get("introEn") or [],
            "reading": [re.sub(r"(?<=[。」]) +", "", r) for r in matrix.get("reading") or []],
            "readingEn": matrix.get("readingEn") or [], "items": items}


# ------------------------------------------------------------------------------------------------ editorial blocks
def _highlights(inp: Inputs, news: list[dict]) -> list[dict]:
    by_id = {n["id"]: n for n in news}
    radar = inp.file("radar.json")
    cards = {}
    for sec_key, label in SECTION.items():
        for c in radar.get(sec_key) or []:
            cards.setdefault(c.get("id"), (label, c))
    out = []
    for h in (inp.editorial or {}).get("highlights") or []:
        n = by_id.get(h.get("id"))
        if n is None and h.get("id") in cards:  # older than the news window: take the fields from the radar card
            label, c = cards[h["id"]]
            n = {"sec": label, "imp": c.get("impact", "low"), "date": c.get("date") or c.get("lastSeen"),
                 "last": c.get("lastSeen")}
        if n is None:
            inp.warnings.append(f"highlight {h.get('id')}: no such radar item; skipped")
            continue
        out.append({"id": h["id"], "zh": h.get("zh", ""), "en": h.get("en") or h.get("zh", ""), "sec": n["sec"],
                    "imp": n["imp"], "date": n["date"], "last": n["last"]})
    return out


def _countdown(inp: Inputs, kind: str) -> list[dict]:
    items = ((inp.editorial or {}).get("countdown") or {}).get(kind) or []
    out = []
    for c in items:
        if c.get("date") is not None and _date(c["date"]) is None:
            inp.warnings.append(f"countdown {kind}: bad date {c.get('date')!r}; skipped")
            continue
        out.append({"date": c.get("date"), "zh": c.get("zh", ""), "en": c.get("en") or c.get("zh", ""),
                    "wz": c.get("wz", ""), "we": c.get("we") or c.get("wz", ""), "link": c.get("link") or "#news"})
    return out


# ------------------------------------------------------------------------------------------------ entry point
def build_view(inp: Inputs, today: dt.date, *, news_window_days: int = 30, retire_past_days: int = 14) -> dict:
    """Compile the view model. `today` is the portal's local date (date-relative fields: ret.days, news window)."""
    for name in REQUIRED_FILES:
        inp.file(name)
    try:
        src = _sources(inp)
        radar = inp.file("radar.json")
        models = _models(inp, src)
        cutoff = (today - dt.timedelta(days=max(1, news_window_days) - 1)).isoformat()
        news = _news(inp, src, models, cutoff)
        mx = matrix_view(inp.matrix)
        names = {x["id"]: (x["n"], x["nEn"]) for x in mx["items"]}
        security = _security(inp, names)
        req = inp.file("requirements.json")
        gates = [{"id": g["id"], "name": g.get("name"), "owner": g.get("owner"), "exit": (g.get("exitCriteria") or [""])[0]}
                 for g in req.get("gates") or []]
        progress = {}
        for mm in models:
            st = {"G0": "done", "G1": "done" if mm["deepDive"] else "doing"}
            r = security["models"].get(mm["id"])
            if r:
                st["G6"] = "blocked" if (r.get("counts") or {}).get("fail") else "done"
            progress[mm["id"]] = st
        editorial = inp.editorial or {}
        avail_cfg = editorial.get("availability") or {}
        overrides = avail_cfg.get("models") or {}
        for mm in models:
            mm["avail"] = availability(progress[mm["id"]], today, avail_cfg, overrides.get(mm["id"]), inp.warnings, mm["id"])
        view = {
            "asOf": radar.get("asOf"), "built": today.isoformat(), "repo": inp.source,
            "brief": [{"t": b.get("t", ""), "p": b.get("p", "P2")} for b in editorial.get("brief") or []],
            "news": news, "actions": _actions(inp), "open": _open_models(inp, src),
            "ret": _retirements(inp, src, today, retire_past_days), "models": models, "security": security,
            "mock": editorial.get("mock"), "gates": gates, "progress": progress,
            "hl": _highlights(inp, news), "cdNew": _countdown(inp, "new"), "cdPrice": _countdown(inp, "price"),
            "secMatrix": mx,
            "gw": _platform_gateway(inp),
        }
    except BuildError:
        raise
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise BuildError(f"malformed input: {type(exc).__name__}: {exc}") from exc
    return view
