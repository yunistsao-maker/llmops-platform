"""Deterministic checks of the portal as a whole: the portal QA reviewer's starting point (tool ``portal_health``).

The research roles each look after their own records; nothing looks at the portal across files.  That is how a digest
can say a model was "added to the catalog" while the catalog never got it, or a top model can be missing from the
comparison page.  These checks find such gaps in the data that is loaded - they read no web page, they decide nothing
and they change nothing: every finding is a lead the reviewer verifies on official pages.

Pure functions over ``PortalData.docs`` (and the date "today", passed in).  Every list in the report is bounded and
what is cut is counted; a malformed file spoils only the section that reads it.
"""

from __future__ import annotations

import datetime as dt
import difflib
import re
import unicodedata
from typing import Any, Callable, Iterator

CATALOG = "data/catalog.json"
LIFECYCLE = "data/lifecycle.json"
OPEN_MODELS = "data/open-models.json"
DIGESTS = "data/digests/"
DEEP_DIVES = "data/models/"
TIERS = ("frontier", "balanced", "efficient")
#: a record not re-verified for this long is reported (the shared method rotates records older than 7 days)
STALE_DAYS = 14
#: how many of the newest digests are read for model names
NEWEST_DIGESTS = 3
#: action items of the digests of this many days are compared with each other
ACTION_DAYS = 30
SIMILAR = 0.8
MAX_ACTIONS = 150
MAX_LIST = 15
MAX_TEXT = 240
#: a name shorter than this matches too much ("o3" in every other word)
MIN_KEY = 4

_PARENS = re.compile(r"\([^()]*\)")
_SCRIPT_EDGE = re.compile(r"(?<=[a-z0-9])(?=[^\x00-\x7f])|(?<=[^\x00-\x7f])(?=[a-z0-9])")
_NON_WORD = re.compile(r"[\W_]+")
_ALTERNATIVES = re.compile(r"[/、,，;；]")
#: what a digest entry says when it reports that a model went into the catalog
_CATALOG_CLAIM = re.compile(r"(?:加入|新增(?:到|至)?|列入)\s*(?:模型目錄|模型清單|catalog)|added to (?:the )?(?:model )?catalog", re.I)


def name_key(text: Any, *, keep_parens: bool = False) -> str:
    """A spelling-independent form of a model name: "GPT-6.1 Sol" / "gpt-6-1-sol" / "GPT 6.1 Sol（Foundry）" -> "gpt 6 1 sol"."""
    t = unicodedata.normalize("NFKC", str(text or "")).lower()
    if not keep_parens:
        for _ in range(3):  # (nested brackets)
            t = _PARENS.sub(" ", t)
    t = _SCRIPT_EDGE.sub(" ", t)
    return " ".join(_NON_WORD.sub(" ", t).split())


def _keys(*names: Any) -> set[str]:
    """Keys of the names of one record; a name like "GLM 5.3／GLM 5.3 Flash" stands for both models."""
    out: set[str] = set()
    for name in names:
        if not isinstance(name, str) or not name.strip():
            continue
        plain = _PARENS.sub(" ", unicodedata.normalize("NFKC", name))
        for part in [name, *_ALTERNATIVES.split(plain)]:
            key = name_key(part)
            if len(key) >= MIN_KEY:
                out.add(key)
    return out


def _mentions(text_key: str, keys: set[str]) -> list[str]:
    padded = f" {text_key} "
    return sorted(k for k in keys if f" {k} " in padded)


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    text = " ".join(str(value if value is not None else "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _bounded(items: list, limit: int = MAX_LIST) -> dict:
    return {"count": len(items), "items": items[:limit], **({"more": len(items) - limit} if len(items) > limit else {})}


def _date(value: Any) -> dt.date | None:
    if not isinstance(value, str):
        return None
    try:
        return dt.date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _catalog_models(docs: dict) -> list[dict]:
    return [m for m in _list(_dict(docs.get(CATALOG)).get("models")) if isinstance(m, dict) and isinstance(m.get("id"), str)]


def _digests(docs: dict) -> list[tuple[str, dict]]:
    """(id, digest), newest first (by periodEnd, else by the file name)."""
    out = []
    for path, doc in docs.items():
        if path.startswith(DIGESTS) and isinstance(doc, dict):
            day = doc.get("periodEnd") if isinstance(doc.get("periodEnd"), str) else path[len(DIGESTS):].removesuffix(".json")
            out.append((str(doc.get("id") or day), str(day), doc))
    out.sort(key=lambda x: (x[1], x[0]), reverse=True)
    return [(i, d) for i, _day, d in out]


# ---------------------------------------------------------------------------------------------- the checks
def deep_dives(docs: dict) -> dict:
    """(a) every catalog model has a deep dive (data/models/<id>.json) and every deep dive a catalog model."""
    catalog = [m["id"] for m in _catalog_models(docs)]
    dives = {path[len(DEEP_DIVES):].removesuffix(".json"): doc for path, doc in docs.items() if path.startswith(DEEP_DIVES)}
    mismatched = [{"file": f"{DEEP_DIVES}{stem}.json", "modelId": _text(_dict(doc).get("modelId"), 80)}
                  for stem, doc in sorted(dives.items()) if isinstance(doc, dict) and doc.get("modelId") not in (None, stem)]
    return {
        "catalogWithoutDeepDive": _bounded([i for i in catalog if i not in dives], 30),
        "deepDiveWithoutCatalogModel": _bounded(sorted(s for s in dives if s not in set(catalog)), 30),
        "deepDiveModelIdMismatch": _bounded(mismatched),
    }


def watchlist_in_catalog(docs: dict) -> dict:
    """(b) watchlist entries that name a model the catalog has: it should leave the watchlist (or the names differ)."""
    by_key: dict[str, str] = {}
    for m in _catalog_models(docs):
        for key in _keys(m.get("name"), m.get("id"), m.get("vendorModelId")):
            by_key.setdefault(key, m["id"])
    places = [(CATALOG, _dict(docs.get(CATALOG)).get("watchlist"))]
    newest = _digests(docs)[:1]
    if newest:
        places.append((f"{DIGESTS}{newest[0][0]}.json", newest[0][1].get("watchlist")))
    found = []
    for where, entries in places:
        for entry in _list(entries):
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            exact = sorted({by_key[k] for k in _keys(name) if k in by_key})
            if exact:
                found.append({"file": where, "name": _text(name, 120), "catalogIds": exact, "match": "same name"})
                continue
            contained = sorted({by_key[k] for k in _mentions(name_key(name), set(by_key))})
            if contained:
                found.append({"file": where, "name": _text(name, 120), "catalogIds": contained, "match": "name contains a catalog model"})
    return _bounded(found)


def lifecycle_duplicates(docs: dict) -> dict:
    """(c) lifecycle events that look like the same event twice: same service, model (providerModelId, else modelName),
    event kind and version."""
    groups: dict[tuple, list[dict]] = {}
    for e in _list(_dict(docs.get(LIFECYCLE)).get("events")):
        if not isinstance(e, dict):
            continue
        model = e.get("providerModelId") if isinstance(e.get("providerModelId"), str) and e["providerModelId"].strip() else e.get("modelName")
        key = (str(e.get("service")), name_key(model), str(e.get("event")), name_key(e.get("version")))
        if key[1]:
            groups.setdefault(key, []).append(e)
    dupes = [{"service": k[0], "model": _text(v[0].get("providerModelId") or v[0].get("modelName"), 120), "event": k[2],
              "ids": [_text(e.get("id"), 120) for e in v[:6]], "dates": [_text(e.get("date"), 20) for e in v[:6]],
              **({"scopeNotes": [_text(e.get("scopeNote"), 120) for e in v[:6]]} if any(e.get("scopeNote") for e in v) else {})}
             for k, v in groups.items() if len(v) > 1]
    return _bounded(dupes)


def _records_with_last_verified(value: Any, path: str = "", depth: int = 0) -> Iterator[tuple[str, dict]]:
    if depth > 6:
        return
    if isinstance(value, dict):
        if "lastVerified" in value:
            yield path, value
        for k, v in value.items():
            if isinstance(v, (dict, list)):
                yield from _records_with_last_verified(v, f"{path}/{k}", depth + 1)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            if isinstance(v, (dict, list)):
                ref = (v.get("id") or v.get("name") or i) if isinstance(v, dict) else i
                yield from _records_with_last_verified(v, f"{path}/{ref}", depth + 1)


def stale_records(docs: dict, today: dt.date, days: int = STALE_DAYS) -> dict:
    """(d) per file: records whose lastVerified is older than ``days`` (count, oldest first)."""
    cutoff = today - dt.timedelta(days=days)
    out: dict[str, Any] = {}
    for path in sorted(docs):
        found = list(_records_with_last_verified(docs[path]))
        if not found:
            continue
        stale, unreadable = [], 0
        for where, rec in found:
            day = _date(rec.get("lastVerified"))
            if day is None:
                unreadable += 1
            elif day < cutoff:
                stale.append((day, _text(rec.get("id") or rec.get("name") or where, 120)))
        stale.sort()
        out[path] = {"records": len(found), "stale": len(stale), "oldest": [{"id": i, "lastVerified": d.isoformat()} for d, i in stale[:5]],
                     **({"unreadableDates": unreadable} if unreadable else {})}
    return {"olderThanDays": days, "before": cutoff.isoformat(), "files": out}


def digest_new_models(docs: dict) -> dict:
    """(e) the newest digests' newModels titles that name no model the portal tracks (to check, not an error), and entries
    that say a model was added to the catalog when no catalog model is named in their title."""
    catalog: set[str] = set()
    for m in _catalog_models(docs):
        catalog |= _keys(m.get("name"), m.get("id"), m.get("vendorModelId"))
    tracked = set(catalog)
    for entry in _list(_dict(docs.get(CATALOG)).get("watchlist")):
        tracked |= _keys(_dict(entry).get("name"))
    for m in _list(_dict(docs.get(OPEN_MODELS)).get("models")):
        tracked |= _keys(_dict(m).get("name"), _dict(m).get("id"))
    to_check, claimed = [], []
    newest = _digests(docs)[:NEWEST_DIGESTS]
    for did, digest in newest:
        for item in _list(digest.get("newModels")):
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            title_key = name_key(title, keep_parens=True)
            if not _mentions(title_key, tracked):
                to_check.append({"digest": did, "title": _text(title)})
            says_catalog = any(isinstance(t, str) and _CATALOG_CLAIM.search(unicodedata.normalize("NFKC", t)) for t in (title, item.get("detail")))
            if says_catalog and not _mentions(title_key, catalog):
                claimed.append({"digest": did, "title": _text(title)})
    return {"digests": [d for d, _ in newest], "namesNoTrackedModel": _bounded(to_check), "saysAddedToCatalogButNotThere": _bounded(claimed)}


def vendor_tiers(docs: dict) -> dict:
    """(f) per vendor in the catalog: the models of each tier (with: a deep dive? GA somewhere?) and the tiers with none -
    a hint to check whether the vendor has a GA model the catalog is missing."""
    dives = {path[len(DEEP_DIVES):].removesuffix(".json") for path in docs if path.startswith(DEEP_DIVES)}
    vendors: dict[str, dict] = {}
    for m in _catalog_models(docs):
        vendor = _text(m.get("vendor") or "unknown", 40)
        tier = m.get("tier") if m.get("tier") in TIERS else "other"
        ga = any(isinstance(o, dict) and o.get("status") == "ga" for o in _list(m.get("offerings")))
        entry = {"id": _text(m["id"], 80), "deepDive": m["id"] in dives, "ga": ga, **({"releaseDate": m["releaseDate"]} if isinstance(m.get("releaseDate"), str) else {})}
        vendors.setdefault(vendor, {}).setdefault(tier, []).append(entry)
    out = {}
    for vendor, tiers in sorted(vendors.items()):
        out[vendor] = {**{t: tiers[t][:8] for t in (*TIERS, "other") if t in tiers}, "missingTiers": [t for t in TIERS if t not in tiers]}
    return out


def action_item_merges(docs: dict, today: dt.date, days: int = ACTION_DAYS, threshold: float = SIMILAR) -> dict:
    """(g) action items of the last ``days`` days' digests whose texts are nearly the same but not the same: candidates
    for a merge in the curation file (the Radar keeps exact restatements as one item already)."""
    cutoff = today - dt.timedelta(days=days)
    seen: dict[str, str] = {}  # text -> the newest digest that has it
    for did, digest in _digests(docs):
        day = _date(digest.get("periodEnd")) or _date(did)
        if day is None or day < cutoff:
            continue
        for item in _list(digest.get("actionItems")):
            text = " ".join(str(_dict(item).get("action") or "").split())
            if text and text not in seen and len(seen) < MAX_ACTIONS:
                seen[text] = did
    texts = list(seen)
    pairs = []
    for i, a in enumerate(texts):
        for b in texts[i + 1:]:
            m = difflib.SequenceMatcher(None, a, b, autojunk=False)
            if m.real_quick_ratio() < threshold or m.quick_ratio() < threshold:
                continue
            ratio = m.ratio()
            if ratio >= threshold:
                pairs.append((ratio, a, b))
    pairs.sort(key=lambda p: -p[0])
    items = [{"ratio": round(r, 3), "a": {"digest": seen[a], "action": _text(a, 300)}, "b": {"digest": seen[b], "action": _text(b, 300)}}
             for r, a, b in pairs]
    return {"since": cutoff.isoformat(), "actionItems": len(texts), **_bounded(items)}


CHECKS: tuple[tuple[str, Callable[..., Any], bool], ...] = (
    ("deepDives", deep_dives, False),
    ("watchlistInCatalog", watchlist_in_catalog, False),
    ("lifecycleDuplicates", lifecycle_duplicates, False),
    ("staleRecords", stale_records, True),
    ("digestNewModels", digest_new_models, False),
    ("vendorTiers", vendor_tiers, False),
    ("actionItemMergeCandidates", action_item_merges, True),
)


def health_report(docs: dict, today: dt.date) -> dict:
    """Every check over ``docs``; a check that cannot read its data says so instead of failing the others."""
    report: dict[str, Any] = {"today": today.isoformat()}
    for name, check, dated in CHECKS:
        try:
            report[name] = check(docs, today) if dated else check(docs)
        except Exception as e:  # noqa: BLE001 - malformed data must not hide what the other checks found
            report[name] = {"error": f"{type(e).__name__}: {_text(e, 200)}"}
    report["howToRead"] = (
        "Findings are leads, not errors: verify each on official pages before proposing anything. deepDives = catalog models "
        "without data/models/<id>.json and the reverse; watchlistInCatalog = watchlist entries naming a catalog model; "
        "lifecycleDuplicates = events with the same service, model, event kind and version; staleRecords = lastVerified older "
        f"than {STALE_DAYS} days; digestNewModels = newModels titles of the newest {NEWEST_DIGESTS} digests that name no tracked "
        "model, and entries that say a model was added to the catalog when none is; vendorTiers = per vendor, the catalog "
        "models of each tier and the tiers with none; actionItemMergeCandidates = nearly identical action items of the last "
        f"{ACTION_DAYS} days (difflib ratio >= {SIMILAR}).")
    return report
