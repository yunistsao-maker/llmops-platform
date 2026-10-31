"""Deterministically stage a proposal onto a copy of the portal documents.

Used with identical logic by the agent's validator (to prove the result is schema-valid *before* the proposal is
returned), by the publishing pipeline (which replays a saved proposal on the data that is live) and by
``scripts/apply_proposal.py`` (to write the files into a portal checkout).  No model is involved here.

The rules that make a proposal mean the same thing in all three places live here: every operation is about the data as
it is *today* (not about what an earlier operation of the same proposal left behind), and every time stamp is written
by this code, never taken from the proposal, and never moves back.
"""

from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import difflib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from .portal.checks import is_date, is_datetime, new_problems, validate_docs
from .portal.data import PortalData
from .portal.diff import Change, diff_docs, diff_value, same
from .portal.patch import (Op, PatchError, apply_op, creates_file, file_is_editable, get_at, is_index, positions_on_the_way,
                           split_path)
from .portal.protected import protected_changes

PROPOSAL_SCHEMA = "llmops-research-proposal/v1"
#: top-level fields of a data file that only this module writes (generatedAt on every change, asOf through `asOfFiles`
#: - and in a new deep dive: ``created_file``)
FILE_STAMPS = ("asOf", "generatedAt", "schema")
#: the `schema` a deep dive is written in (the portal schema's const for DeepDive.schema)
DEEP_DIVE_SCHEMA = "deep-dive/v1"
_DIGEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")  # becomes a file name: data/digests/<id>.json
#: The values (texts, numbers, lists, objects) one proposal may hold, everything in it counted.  All of a portal's data
#: is some twenty thousand values and a proposal changes a few records of it; a research run's largest proposal holds
#: a few thousand.  Working through a proposal takes a difference per value it writes and, where it is wrong, a schema
#: error per value: what is not bounded here is bounded by the memory of the function that replays it.
MAX_PROPOSAL_VALUES = 30_000


def count_values(node: Any, stop_after: int | None = None) -> int:
    """How many values ``node`` holds - itself and everything in it - without recursion, and (with ``stop_after``)
    without counting further than one past it."""
    seen, todo = 0, [node]
    while todo and (stop_after is None or seen <= stop_after):
        item = todo.pop()
        seen += 1
        if isinstance(item, dict):
            todo.extend(item.values())
        elif isinstance(item, (list, tuple)):
            todo.extend(item)
    return seen


def too_many_values(proposal: Any) -> bool:
    return count_values(proposal, MAX_PROPOSAL_VALUES) > MAX_PROPOSAL_VALUES


def _source_list(docs: dict[str, Any]) -> list | None:
    doc = docs.get("data/sources.json")
    items = doc.get("sources") if isinstance(doc, dict) else None
    return items if isinstance(items, list) else None


def file_stamp(path: str) -> str | None:
    """The field an operation path addresses when it is one of ``FILE_STAMPS`` (``/asOf``, ``/generatedAt`` ...)."""
    try:
        segs = split_path(path)
    except PatchError:
        return None
    return segs[0] if len(segs) == 1 and segs[0] in FILE_STAMPS else None


def _pointer(segs: list[str]) -> str:
    return "".join("/" + seg.replace("~", "~0").replace("/", "~1") for seg in segs)


def created_file(value: Any, today_iso: str) -> Any:
    """A new deep dive as staging writes it.  Its `asOf` - "the content is current as of" - is the day of the research
    that wrote it: a stamp, like every `asOf`, and not the proposal's to say (a page published with a later `asOf` would
    claim a verification nobody made).  Its `schema` is the one deep dives are written in, unless the value names one
    (which the portal schema then checks)."""
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    doc = {"schema": DEEP_DIVE_SCHEMA, **copy.deepcopy(value)}
    doc["asOf"] = today_iso
    return doc


def out_of_step(today: dict[str, Any], now: dict[str, Any], op: Op) -> str | None:
    """Why ``op`` is not about the portal data as it is today (None when it is).

    Operations are applied one after another, but each one is checked, shown to a reviewer and compared with the live
    data against *today's* value (``before``).  That is only true while no operation works on something an earlier
    operation of the same proposal created, moved or changed: ``now`` is the data as those earlier operations left it.

    The creation of a deep dive (``add`` at the path "") is the one operation that may rest on an earlier one: it has
    no ``before`` - nothing of today's that it is compared with or shown next to - and all it reads of the earlier
    operations is whether the catalog has the model by then (``patch.create_problem``), so it may follow the operation
    that adds the model.  That operation is checked against today's catalog like any other, a replay applies both in
    the same order (and refuses both when either no longer applies), and a later operation that takes the model away
    again leaves a deep dive of no catalog model, which the cross-file check refuses.  Nothing may follow it into the
    new file: its whole content is the one operation that creates it (the first check here).
    """
    if op.file not in today and op.file in now:
        return ("the file does not exist in the portal data as it is today: an earlier operation of this proposal creates it. Put the "
                "complete content into that operation instead")
    if op.file not in today or op.file not in now or not file_is_editable(op.file):
        return None  # apply_op says what is wrong with the file
    try:
        segs = split_path(op.path)
    except PatchError:
        return None  # ... and with the path
    if not segs:
        return None
    loose = positions_on_the_way(today[op.file], segs)
    if loose:
        k, ident = loose[0]
        here = _pointer(segs[: k + 1])
        if ident is not None:
            better = _pointer(segs[:k] + [ident] + segs[k + 1:])
            return (f"the path goes through a list position ({here}); a position is only good for the moment it was read at. "
                    f"Address the element by its id: {better}")
        return (f"the path goes through a list position ({here}) whose element has no id; change that element as a whole instead: "
                f"`replace {here}` with the complete new element (only the fields that differ count as changed)")
    if op.op in ("replace", "remove"):
        try:
            current = get_at(now, op.file, op.path)
        except PatchError:
            return None
        try:
            original = get_at(today, op.file, op.path)
        except PatchError:
            return ("the target does not exist in the portal data as it is today (it is created by an earlier operation of this "
                    "proposal); put the final value into that operation instead")
        if not same(original, current):
            return ("what is at this path is no longer what the portal has today: an earlier operation of this proposal changed it, "
                    "or moved the positions in the list. Address list elements by their id, or merge the changes into one operation.")
        return None
    if op.op != "add":
        return None
    parent_path = _pointer(segs[:-1])
    try:
        parent = get_at(now, op.file, parent_path)
    except PatchError:
        return None
    try:
        original = get_at(today, op.file, parent_path)
    except PatchError:
        return ("the place this is added to does not exist in the portal data as it is today (an earlier operation of this proposal "
                "creates it); put the value into that operation instead")
    if isinstance(parent, list) and is_index(segs[-1]) and not same(original, parent):
        return ("inserts at a position of a list that an earlier operation of this proposal already changed; append with '/-', "
                "or merge the changes into one operation")
    return None


_DATE_ONLY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _moment(text: Any) -> dt.datetime | None:
    if not isinstance(text, str):
        return None
    try:
        if _DATE_ONLY.fullmatch(text):
            return dt.datetime.combine(dt.date.fromisoformat(text), dt.time(), dt.timezone.utc)
        when = dt.datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def newest(existing: Any, stamp: str) -> Any:
    """``stamp``, unless what is there is already later.

    A proposal that waited for approval is replayed after newer ones went live: it may add its changes, but it must not
    make a file (or a source) look older than it is.
    """
    was, now = _moment(existing), _moment(stamp)
    return existing if was is not None and now is not None and was > now else stamp


def digest_problems(digest: Any, today_iso: str) -> list[str]:
    """What is wrong with a new digest's period (its shape is the schema's business)."""
    if not isinstance(digest, dict):
        return []
    end, start = digest.get("periodEnd"), digest.get("periodStart")
    # said here, not left to the schema check: these rules must not depend on what a schema happens to let through
    if not (isinstance(end, str) and is_date(end)):
        return [f"digest.periodEnd must be a date (YYYY-MM-DD), got {json.dumps(end, ensure_ascii=True)[:60]}"]
    out = []
    if digest.get("id") != end:
        out.append(f"digest.id must equal periodEnd ({end}), got {str(digest.get('id'))[:60]!r}")
    if not (isinstance(start, str) and is_date(start)):
        out.append(f"digest.periodStart must be a date (YYYY-MM-DD), got {json.dumps(start, ensure_ascii=True)[:60]}")
    elif start > end:
        out.append("digest.periodStart is after periodEnd")
    if end > today_iso:
        out.append(f"digest.periodEnd {end} is in the future (today is {today_iso} Taipei)")
    return out


#: the sections of a digest whose entries the portal's Radar merges by title (the portal's tools/build_radar.py SECTIONS)
DIGEST_NEWS_SECTIONS = ("newModels", "lifecycleChanges", "pricingChanges", "litellm", "security", "regulatory")
#: what one entry's `merges` may list (the schema's maxItems)
MAX_MERGES = 10
_MERGE_PROBLEMS = 20  # told one by one; the rest are counted
_MERGE_HINTS = 5  # problems that get a "did you mean" (each one is a fuzzy search through the earlier titles)
# the portal's tools/build_radar.py: the "【10-06 補充】" marker of a supplemented digest, and what counts as punctuation
_MARKER = re.compile(r"【[^】]{0,40}補充[^】]*】")
_PUNCT = re.compile(r"[\s，,。．.：:；;、（）()「」『』\"'“”‘’!！?？…—–\-/]+")


def merge_key(text: str) -> str:
    """``text`` the way the portal's Radar compares titles and action texts (tools/build_radar.py ``norm``): NFKC, without
    the marker of a supplemented digest, every run of punctuation and white space one space, lower case."""
    t = unicodedata.normalize("NFKC", text)
    t = _MARKER.sub("", t)
    return _PUNCT.sub(" ", t).strip().lower()


def merge_problems(digest: Any, docs: dict[str, Any]) -> list[str]:
    """`merges` of a new digest that do not name an entry of an earlier one.

    An entry's `merges` lists the titles of entries of the same section in earlier digests that it updates (an action
    item's, the `action` texts of earlier action items); the portal's Radar folds those into this one.  They are
    references, not news, so the agent's validator does not hold the dates and amounts in them to a quote - which is
    only sound while each of them IS the title of an entry the portal already has (and that was checked when it went
    live).  So every one must be - after the Radar's own normalisation (:func:`merge_key`) - the title of an entry of
    the same section of a digest in ``docs`` whose period ends before this one's.  Any shape is answered, never raised.
    """
    if not isinstance(digest, dict) or not isinstance(docs, dict):
        return []
    end = digest.get("periodEnd")
    if not (isinstance(end, str) and is_date(end)):
        return []  # digest_problems says what is wrong with the period
    places = [(section, "title") for section in DIGEST_NEWS_SECTIONS] + [("actionItems", "action")]
    known: dict[str, dict[str, str]] = {section: {} for section, _field in places}  # section -> merge key -> the text as written
    for rel, doc in docs.items():
        if not (isinstance(rel, str) and rel.startswith("data/digests/") and isinstance(doc, dict)):
            continue
        when = doc.get("periodEnd")
        if not (isinstance(when, str) and is_date(when) and when < end):
            continue
        for section, field in places:
            items = doc.get(section)
            for item in items if isinstance(items, list) else []:
                key = merge_key(item[field]) if isinstance(item, dict) and isinstance(item.get(field), str) else ""
                if key:
                    known[section].setdefault(key, item[field])

    out: list[str] = []
    untold = 0

    def tell(text: str) -> None:
        nonlocal untold
        if len(out) < _MERGE_PROBLEMS:
            out.append(text)
        else:
            untold += 1

    for section, field in places:
        what = "title" if field == "title" else "action text"
        of = f"an entry of {section}" if field == "title" else "an action item"
        items = digest.get(section)
        for n, item in enumerate(items if isinstance(items, list) else []):
            if not isinstance(item, dict) or "merges" not in item:
                continue
            where = f"digest/{section}/{n}/merges"
            merges = item["merges"]
            if not isinstance(merges, list) or not all(isinstance(m, str) for m in merges):
                tell(f"{where}: must be a list of texts - the exact {what}s of earlier entries this one updates")
                continue
            if len(merges) > MAX_MERGES:
                tell(f"{where}: lists {len(merges)} earlier entries; at most {MAX_MERGES}")
                continue
            for k, value in enumerate(merges):
                key = merge_key(value)
                if key and key in known[section]:
                    continue
                hint = ""
                if key and len(out) < _MERGE_HINTS:
                    close = difflib.get_close_matches(key[:300], list(known[section]), n=1, cutoff=0.6)
                    hint = f" (the closest is {known[section][close[0]][:200]!r})" if close else ""
                tell(f"{where}/{k}: {value[:200]!r} is not the {what} of {of} in an earlier digest{hint}. `merges` names entries "
                     f"the portal already has, which this one updates: copy each {what} exactly as it stands in "
                     "data/digests/<date>.json (lookup_portal shows them), or leave it out")
    if untold:
        out.append(f"... and {untold} more `merges` that name no entry of an earlier digest")
    return out


def stage_proposal(
    portal: PortalData,
    *,
    ops: list[Op],
    new_sources: list[dict],
    digest: dict | None,
    as_of_files: list[str],
    today_iso: str,
    now_iso: str,
    touch_sources: list[str] | None = None,
    effects: list[list[Change]] | None = None,
) -> tuple[PortalData, list[str], list[str], set[str]]:
    """Return (staged portal, applied descriptions, errors, touched files).

    ``touch_sources`` are ids of EXISTING sources whose pages were re-read for this proposal; their
    ``accessed`` date is set to today (the portal's own convention for "last looked at").

    ``effects`` (a list to fill) receives, for every operation in order, what that operation really did: the
    difference between its file just before and just after it.  The new side of those changes is the staged data
    itself, so once everything is applied ``Change.above`` shows each written value's surroundings as they will be
    published; the written values themselves (``Change.after``) are copies made at that moment.
    """
    staged = portal.clone()
    applied: list[str] = []
    errors: list[str] = []
    touched: set[str] = set()

    if new_sources:
        sources = _source_list(staged.docs)
        if sources is None:
            errors.append("portal data has no data/sources.json with a sources list")
        else:
            for src in new_sources:
                entry = copy.deepcopy(src)
                if isinstance(entry, dict):
                    entry["accessed"] = today_iso  # the day a page was read is not the proposal's to say
                sources.append(entry)
            touched.add("data/sources.json")
            applied.append(f"add {len(new_sources)} source(s) to data/sources.json")

    for i, op in enumerate(ops):
        label = f"ops[{i}] ({op.op} {op.file}{op.path})"
        effect: list[Change] = []
        try:
            stamp = file_stamp(op.path)
            problem = out_of_step(portal.docs, staged.docs, op)
            if stamp:
                errors.append(f"{label}: {stamp} is written when a proposal is applied, not by an operation")
            elif problem:
                errors.append(f"{label}: {problem}")
            else:
                if creates_file(op):
                    op = dataclasses.replace(op, value=created_file(op.value, today_iso))
                existed = op.file in staged.docs
                before = copy.deepcopy(staged.docs.get(op.file)) if effects is not None else None
                applied.append(apply_op(staged.docs, op))
                touched.add(op.file)
                if effects is not None:
                    # a file this operation created is added as a whole - every value in it is new (as in the
                    # publisher's comparison of the live data with the result)
                    found = diff_value(before, staged.docs[op.file], file=op.file) if existed else diff_docs({}, {op.file: staged.docs[op.file]})
                    effect = [dataclasses.replace(c, after=copy.deepcopy(c.after)) for c in found]
        except PatchError as e:
            errors.append(f"{label}: {e}")
        if effects is not None:
            effects.append(effect)

    if touch_sources:
        sources = _source_list(staged.docs)
        if sources is not None:
            wanted = {sid for sid in touch_sources if isinstance(sid, str)}
            bumped = 0
            for src in sources:
                if isinstance(src, dict) and isinstance(src.get("id"), str) and src["id"] in wanted:
                    stamp = newest(src.get("accessed"), today_iso)
                    if src.get("accessed") != stamp:
                        src["accessed"] = stamp
                        bumped += 1
            if bumped:
                touched.add("data/sources.json")
                applied.append(f"set accessed={today_iso} on {bumped} existing source(s)")

    if digest is not None:
        digest_id = digest.get("id")
        key = f"data/digests/{digest_id}.json"
        if not isinstance(digest_id, str) or not _DIGEST_ID.fullmatch(digest_id):
            errors.append(f"digest.id {str(digest_id)[:60]!r} cannot be a file name; use the periodEnd date (YYYY-MM-DD)")
        elif key in staged.docs:
            errors.append(f"{key} already exists; choose a new digest id (the periodEnd date)")
        else:
            entry = copy.deepcopy(digest)
            entry["generatedAt"] = now_iso
            # `merges` must name entries the portal had before this proposal: checked here, so a replay checks it again
            problems = digest_problems(entry, today_iso) + merge_problems(entry, portal.docs)
            errors.extend(problems)
            if not problems:
                staged.docs[key] = entry
                applied.append(f"create {key}")

    for rel in touched:
        doc: Any = staged.docs.get(rel)
        if isinstance(doc, dict) and "generatedAt" in doc:
            doc["generatedAt"] = newest(doc["generatedAt"], now_iso)
    for rel in as_of_files:
        doc = staged.docs.get(rel)
        if isinstance(doc, dict) and "asOf" in doc:
            doc["asOf"] = newest(doc["asOf"], today_iso)
    return staged, applied, errors, touched


# ---------------------------------------------------------------------------------------------
# replaying a saved proposal on a real checkout
# ---------------------------------------------------------------------------------------------
def envelope_problems(proposal: Any) -> list[str]:
    """Wrong shapes in a saved proposal.  It is data from somewhere else: nothing about it is taken for granted."""
    if not isinstance(proposal, dict) or proposal.get("schema") != PROPOSAL_SCHEMA:
        return [f"not a research proposal (schema={proposal.get('schema') if isinstance(proposal, dict) else None!r})"]
    if too_many_values(proposal):
        return [f"the proposal holds more than {MAX_PROPOSAL_VALUES:,} values (texts, numbers, lists and objects, all counted); "
                "that is more than all of a portal's data, and not something a research run writes"]
    out: list[str] = []
    ops = proposal.get("ops", [])
    if not isinstance(ops, list) or not all(isinstance(o, dict) for o in ops):
        out.append("ops must be a list of operation objects")
    sources = proposal.get("newSources") or []
    if not isinstance(sources, list) or not all(isinstance(x, dict) for x in sources):
        out.append("newSources must be a list of source objects")
    if proposal.get("digest") is not None and not isinstance(proposal["digest"], dict):
        out.append("digest must be an object or null")
    for key in ("asOfFiles", "touchSources"):
        value = proposal.get(key) or []
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            out.append(f"{key} must be a list of strings")
    for i, op in enumerate(ops if isinstance(ops, list) else []):
        if isinstance(op, dict) and not isinstance(op.get("rationale", ""), str):
            out.append(f"ops[{i}].rationale must be text")
    unverified = proposal.get("unverified") or []
    if isinstance(unverified, list):
        for i, item in enumerate(unverified):
            plain = isinstance(item, str) or (isinstance(item, dict) and set(item) <= {"claim", "reason"}
                                              and all(isinstance(v, str) for v in item.values()))
            if not plain:
                out.append(f"unverified[{i}] must be text, or an object with the text fields claim and reason")
                break
    created = proposal.get("createdAt")
    if not isinstance(created, str) or not is_datetime(created):
        out.append(f"createdAt must be a timestamp such as 2026-10-05T03:00:00Z, got {str(created)[:60]!r}")
    today = proposal.get("today")
    if today is not None and not (isinstance(today, str) and is_date(today)):
        out.append(f"today must be a date such as 2026-10-05, got {str(today)[:60]!r}")
    return out


def apply_saved_proposal(portal: PortalData, proposal: dict, effects: list[list[Change]] | None = None) -> tuple[PortalData, list[str]]:
    """Replay an accepted proposal (as saved by the agent) onto ``portal``.

    Returns (staged portal, problems).  ``problems`` lists wrong shapes, operations that no longer apply (the portal
    changed since the proposal was made), NEW schema / cross-file problems and changes to fields only the platform team
    may set; an empty list means the result is as valid as the input was.  ``effects``: see :func:`stage_proposal`.
    """
    problems = envelope_problems(proposal)
    if problems:
        return portal.clone(), problems
    raw_ops = proposal.get("ops", [])
    try:
        ops = [Op.from_dict({k: v for k, v in o.items() if k != "before"}) for o in raw_ops]
    except PatchError as e:
        return portal.clone(), [str(e)]
    created = proposal["createdAt"]
    staged, _applied, errors, _touched = stage_proposal(
        portal, ops=ops, new_sources=proposal.get("newSources") or [], digest=proposal.get("digest"),
        as_of_files=proposal.get("asOfFiles") or [], today_iso=proposal.get("today") or created[:10], now_iso=created,
        touch_sources=proposal.get("touchSources") or [], effects=effects,
    )
    problems = list(errors)
    if not problems:
        problems += [f"portal validation: {p}" for p in new_problems(validate_docs(portal.schema, portal.docs),
                                                                     validate_docs(staged.schema, staged.docs))]
        # a saved proposal is data from somewhere else: the platform team's own fields stay off limits however it was made
        problems += [f"protected field: {p}" for p in protected_changes(portal.docs, staged.docs)]
    # ops must still start from the values the research saw
    for op, raw in zip(ops, raw_ops):
        if "before" in raw and op.op in ("replace", "remove"):
            try:
                now = get_at(portal.docs, op.file, op.path)
            except PatchError:
                continue
            if not same(now, raw["before"]):
                problems.append(f"{op.file}{op.path} is {json.dumps(now, ensure_ascii=False)[:80]} in this checkout but the proposal "
                                f"was made against {json.dumps(raw['before'], ensure_ascii=False)[:80]}; re-run the research")
    return staged, problems


def _same(a: Any, b: Any) -> bool:
    """Equal, ignoring the top-level ``generatedAt`` stamp that staging rewrites."""
    if isinstance(a, dict) and isinstance(b, dict):
        a, b = {k: v for k, v in a.items() if k != "generatedAt"}, {k: v for k, v in b.items() if k != "generatedAt"}
    return a == b


def already_applied(portal: PortalData, proposal: dict) -> bool:
    """True when ``portal`` already holds everything the proposal would write (it was applied, or merged, earlier).

    Replaying such a proposal would be reported as stale (the "before" values are gone), which is true but unhelpful.
    """
    if envelope_problems(proposal):
        return False
    raw_ops = proposal.get("ops") or []
    new_sources = proposal.get("newSources") or []
    digest = proposal.get("digest")
    if not (raw_ops or new_sources or digest):
        return False
    try:
        return _already_applied(portal, raw_ops, new_sources, digest, proposal.get("today") or proposal["createdAt"][:10])
    except Exception:  # damaged data or a shape nobody thought of: "not applied" sends it through the full validation
        return False


def _already_applied(portal: PortalData, raw_ops: list, new_sources: list, digest: dict | None, today_iso: str) -> bool:
    try:
        for raw in raw_ops:
            op = Op.from_dict({k: v for k, v in raw.items() if k != "before"})
            if op.file not in portal.docs or op.op not in ("add", "replace", "remove"):
                return False
            if creates_file(op):
                # the file the proposal creates, as staging would write it (another file of that name is not this one)
                if not _same(portal.docs[op.file], created_file(op.value, today_iso)):
                    return False
                continue
            if op.op == "remove":
                try:
                    get_at(portal.docs, op.file, op.path)
                except PatchError:
                    continue  # already gone
                return False
            if op.op == "replace":
                if get_at(portal.docs, op.file, op.path) != op.value:
                    return False
                continue
            parent_path, _, last = op.path.rpartition("/")  # add
            parent = get_at(portal.docs, op.file, parent_path)
            if isinstance(parent, list):
                if op.value not in parent:
                    return False
            elif not (isinstance(parent, dict) and parent.get(last) == op.value):
                return False
    except PatchError:
        return False
    have = {s.get("id") for s in portal.sources}
    if any(s.get("id") not in have for s in new_sources):
        return False
    return not digest or _same(portal.docs.get(f"data/digests/{digest.get('id')}.json"), digest)


def dump_json(doc: Any) -> str:
    """The portal repo's JSON style: 2-space indent, non-ASCII kept, trailing newline.

    Raises ValueError for NaN / Infinity instead of writing them: the result must be JSON for every parser.
    """
    return json.dumps(doc, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def changed_files(before: PortalData, after: PortalData) -> list[str]:
    return sorted(k for k in after.docs if k not in before.docs or not same(before.docs[k], after.docs[k]))


def diff_text(before: PortalData, after: PortalData, context: int = 2) -> str:
    out: list[str] = []
    for rel in changed_files(before, after):
        old = dump_json(before.docs[rel]).splitlines(keepends=True) if rel in before.docs else []
        new = dump_json(after.docs[rel]).splitlines(keepends=True)
        out.extend(difflib.unified_diff(old, new, f"a/{rel}", f"b/{rel}", n=context))
    return "".join(out)


def write_changes(root: str | Path, before: PortalData, after: PortalData) -> list[str]:
    """Write the changed data files into a portal checkout (never anything outside ``data/``)."""
    root = Path(root).resolve()
    written = []
    for rel in changed_files(before, after):
        if not rel.startswith("data/") or ".." in rel.split("/"):
            raise ValueError(f"refusing to write {rel!r}: only files under data/ may be changed")
        path = (root / rel).resolve()
        if root not in path.parents:
            raise ValueError(f"refusing to write outside {root}: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_json(after.docs[rel]), encoding="utf-8")
        written.append(rel)
    return written
