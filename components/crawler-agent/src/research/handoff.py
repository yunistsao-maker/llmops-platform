"""Handoff notes between the daily research roles.

The roles run one after another, each in a fresh session: without notes, one role cannot tell the role that owns a
finding about it ("the Bedrock page lists a new model, but its vendor facts are the vendor-API role's"), and what one run
could not confirm or did not reach is invisible to the others and to tomorrow's run.

* After a role run - accepted or not - a small record is written next to the run records, in the same result store
  (``OUTPUT_URI``, S3 or a folder): ``handoff/<YYYY-MM-DD Asia/Taipei>/<HHMMSS>-<role>-<runId>.json`` with the run's
  status, summary, ``unverified`` lines, ``leads`` (findings for another role, proposal.py), ``notReached`` (the
  ``checked`` entries whose result says "not reached: ..." or "skipped: ...") and ``qa`` (the portal reviewer's
  ``checked`` lines of topic "portal-qa": PASS / FAIL / TODO - what the Radar editor must not contradict).
* At the start of a role run the newest records of the day are read back (the first role of the day - no record yet -
  and the overview roles also get yesterday's leads and not-reached items; every role gets the leads addressed to it and
  its own not-reached items of yesterday) and rendered into the task brief as a DATA block.

The notes were written by earlier model runs from web pages: they are data, never instructions.  Every string is one
line, bounded and quoted; text that reads like an instruction to an AI is withheld (and the run is marked for a person);
the number of records and the size of the block are bounded.  Reading or writing notes never fails a run.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from typing import Any

from .clock import TAIPEI, now_utc
from .proposal import LEAD_ANY, report_parts
from .skills import ROLES
from .store import put_json, read_json_folder
from .validate.injection import scan, visible

log = logging.getLogger(__name__)

SCHEMA = "handoff/v1"
FOLDER = "handoff"
MAX_SUMMARY = 1200
MAX_ITEMS = 20
MAX_CLAIM, MAX_REASON, MAX_NOTE, MAX_TOPIC, MAX_RESULT, MAX_URL = 300, 200, 300, 160, 300, 400
#: newest records read per day, the most a record may weigh, the most the rendered block may take in a prompt
MAX_RECORDS = 30
MAX_RECORD_BYTES = 64_000
MAX_BLOCK = 12_000
#: the convention for a worklist item a run did not get to: a `checked` entry whose result starts with one of these
NOT_REACHED = re.compile(r"^\s*(?:not reached|skipped)\b", re.I)
#: the `checked` topic of the portal reviewer's report lines (pipeline publish.py carries the same lines into notifications)
QA_TOPIC = "portal-qa"
MAX_QA = 20
WITHHELD = "[withheld: this note reads like instructions to an AI]"
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")


def _line(value: Any, limit: int) -> str:
    """One line of visible text, at most ``limit`` characters (it was written by a model, from web pages)."""
    text = " ".join(_CONTROL.sub(" ", visible(str(value if value is not None else ""))).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _url(value: Any) -> str | None:
    text = value.strip() if isinstance(value, str) else ""
    return text if text.startswith("https://") and len(text) <= MAX_URL and not any(c.isspace() for c in text) else None


def _clean_lead(lead: Any) -> dict | None:
    if not isinstance(lead, dict) or (lead.get("forRole") not in ROLES and lead.get("forRole") != LEAD_ANY):
        return None
    note = _line(lead.get("note"), MAX_NOTE)
    if not note:
        return None
    url = _url(lead.get("url"))
    return {"forRole": lead["forRole"], "note": note, **({"url": url} if url else {})}


def not_reached(checked: Any) -> list[dict]:
    """The ``checked`` entries that say the run did not get to them."""
    out = []
    for c in checked if isinstance(checked, list) else []:
        if isinstance(c, dict) and (NOT_REACHED.match(str(c.get("result") or "")) or (not c.get("result") and NOT_REACHED.match(str(c.get("topic") or "")))):
            out.append({"topic": _line(c.get("topic"), MAX_TOPIC), "result": _line(c.get("result"), MAX_RESULT)})
    return out[:MAX_ITEMS]


def qa_lines(checked: Any) -> list[str]:
    """The ``checked`` results of topic "portal-qa" (the portal reviewer's PASS / FAIL / TODO lines), FAIL and TODO first."""
    lines = [_line(c.get("result"), MAX_RESULT) for c in checked if isinstance(c, dict) and c.get("topic") == QA_TOPIC] \
        if isinstance(checked, list) else []
    rank = {"FAIL": 0, "TODO": 1}
    lines = [x for x in lines if x]
    return sorted(lines, key=lambda x: rank.get(x.split(" ", 1)[0].upper(), 2))[:MAX_QA]


# ---------------------------------------------------------------------------------------------- writing
def build_record(result: dict, session: Any = None, *, day: str) -> dict:
    """The handoff record of a finished run: from its accepted proposal, else from the last proposal it submitted."""
    role = str((result.get("task") or {}).get("role") or "")
    proposal = result.get("proposal") if isinstance(result.get("proposal"), dict) else None
    parts = proposal or (report_parts(session, getattr(session, "last_submitted", None)) if session is not None else {})
    summary = parts.get("summary") if isinstance(parts.get("summary"), str) else ""
    if result.get("status") != "accepted" and result.get("error"):
        summary = f"{summary} （這次執行沒有通過驗證的提案：{result['error']}）".strip()
    unverified = [{"claim": _line(u.get("claim"), MAX_CLAIM), "reason": _line(u.get("reason"), MAX_REASON)}
                  for u in parts.get("unverified") or [] if isinstance(u, dict) and u.get("claim")]
    leads = [lead for lead in (_clean_lead(x) for x in parts.get("leads") or []) if lead]
    return {
        "schema": SCHEMA, "role": role, "runId": _line(result.get("runId"), 100), "date": day,
        "finishedAt": _line(result.get("finishedAt"), 40), "status": _line(result.get("status"), 20),
        **({"proposalId": _line(proposal.get("id"), 100)} if proposal else {}),
        "summary": _line(summary, MAX_SUMMARY),
        "unverified": unverified[:MAX_ITEMS],
        "leads": leads[:MAX_ITEMS],
        "notReached": not_reached(parts.get("checked")),
        "qa": qa_lines(parts.get("checked")),
    }


def record_key(day: str, when: dt.datetime, role: str, run_id: str) -> str:
    return f"{FOLDER}/{day}/{when.astimezone(TAIPEI).strftime('%H%M%S')}-{_SAFE.sub('', role)[:64]}-{_SAFE.sub('', run_id)[:100] or 'run'}.json"


def save(output_uri: str, result: dict, session: Any, *, today: dt.date, store: Any = None) -> str | None:
    """Write the handoff record of a role run next to its run record.  Never raises: a note that cannot be written is
    logged, and the run's own result stands."""
    role = (result.get("task") or {}).get("role")
    if not output_uri or role not in ROLES:
        return None
    try:
        record = build_record(result, session, day=today.isoformat())
        return put_json(output_uri, record_key(today.isoformat(), now_utc(), role, str(result.get("runId") or "")), record, store=store)
    except Exception:  # noqa: BLE001 - see the docstring
        log.exception("writing the handoff note failed")
        return None


# ---------------------------------------------------------------------------------------------- reading
def clean_record(raw: Any) -> dict | None:
    """A stored record as the next run may see it - or None.  It is read back from storage: nothing in it is trusted."""
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA or raw.get("role") not in ROLES:
        return None
    lists = {k: v if isinstance(v, list) else [] for k, v in ((k, raw.get(k)) for k in ("unverified", "leads", "notReached"))}
    date = str(raw.get("date") or "")
    return {
        # (the fields shown unquoted are reduced to the characters they are made of)
        "role": raw["role"], "runId": _SAFE.sub("", str(raw.get("runId") or ""))[:100], "date": date if _DAY.match(date) else "",
        "status": re.sub(r"[^a-z_]+", "", str(raw.get("status") or ""))[:20], "proposalId": _SAFE.sub("", str(raw.get("proposalId") or ""))[:100],
        "summary": _line(raw.get("summary"), MAX_SUMMARY),
        "unverified": [{"claim": _line(u.get("claim"), MAX_CLAIM), "reason": _line(u.get("reason"), MAX_REASON)}
                       for u in lists["unverified"][:MAX_ITEMS] if isinstance(u, dict) and u.get("claim")],
        "leads": [lead for lead in (_clean_lead(x) for x in lists["leads"][:MAX_ITEMS]) if lead],
        "notReached": [{"topic": _line(n.get("topic"), MAX_TOPIC), "result": _line(n.get("result"), MAX_RESULT)}
                       for n in lists["notReached"][:MAX_ITEMS] if isinstance(n, dict)],
        "qa": [line for line in (_line(x, MAX_RESULT) for x in (raw.get("qa") if isinstance(raw.get("qa"), list) else [])[:MAX_QA]
                                 if isinstance(x, str)) if line],
    }


def read(output_uri: str, today: dt.date, *, store: Any = None) -> dict:
    """Today's and yesterday's records (the newest ``MAX_RECORDS`` of each day, oldest first)."""
    out: dict[str, list[dict]] = {}
    for name, day in (("today", today), ("yesterday", today - dt.timedelta(days=1))):
        docs = read_json_folder(output_uri, f"{FOLDER}/{day.isoformat()}", newest=MAX_RECORDS, max_bytes=MAX_RECORD_BYTES, store=store)
        out[name] = [r for r in (clean_record(doc) for _key, doc in docs) if r]
    return out


def render(notes: dict, role: str) -> tuple[str, list[str]]:
    """The notes ``role`` gets, as lines of quoted data, and the notes withheld because they read like instructions.

    Leads addressed to ``role`` (or to any role) come first.  An overview role (the Radar editor, the portal reviewer)
    sees everything; the first role of a day (no record yet today) sees yesterday's leads and not-reached items; every
    role sees its own not-reached items of yesterday and a short line per earlier run of today.
    """
    today_recs = [r for r in notes.get("today") or [] if isinstance(r, dict)]
    yday_recs = [r for r in notes.get("yesterday") or [] if isinstance(r, dict)]
    overview = bool(getattr(ROLES.get(role), "overview", False))
    first = not today_recs
    withheld: list[str] = []

    def q(text: str, rec: dict) -> str:
        if scan(text, max_hits=1):
            if len(withheld) < 20:
                withheld.append(f"{rec.get('role')} {rec.get('runId')}: {text[:160]}")
            return json.dumps(WITHHELD)
        # (quoted, and unable to spell the line that ends the block)
        return json.dumps(re.sub(r"(?i)handoff notes", "handoff-notes", text), ensure_ascii=False)

    def shown(when: str, rec_role: str) -> bool:
        """May ``role`` see what another role's run of ``when`` left for others?"""
        return rec_role == role or overview or (when == "yesterday" and first)

    dated = [("today", r) for r in today_recs] + [("yesterday", r) for r in yday_recs]
    mine = [(w, r, lead) for w, r in dated for lead in r["leads"] if lead["forRole"] in (role, LEAD_ANY)]
    others = [(w, r, lead) for w, r in dated for lead in r["leads"] if lead["forRole"] not in (role, LEAD_ANY) and shown(w, "")]
    missed = [(w, r, n) for w, r in dated for n in r["notReached"] if shown(w, r["role"])]

    def lead_line(w: str, r: dict, lead: dict) -> str:
        page = f" page={json.dumps(lead['url'])}" if lead.get("url") else ""
        return f"- [{w}] {r['role']} -> {lead['forRole']}: {q(lead['note'], r)}{page}"

    lines: list[str] = []
    if mine:
        lines += [f"Leads for you ({role}) or for any role:"] + [lead_line(*x) for x in mine]
    if others:
        lines += ["Leads for other roles:"] + [lead_line(*x) for x in others]
    if missed:
        lines += ["Not reached by earlier runs (worklist items they did not get to):"]
        lines += [f"- [{w}] {r['role']}: {q(n['topic'], r)} {q(n['result'], r)}" for w, r, n in missed]
    if overview:
        qa = [(r, line) for r in today_recs for line in r.get("qa") or []]
        if qa:
            lines += ["Portal QA report of today (portal-reviewer; the digest must not contradict a FAIL line):"]
            lines += [f"- {q(line, r)}" for r, line in qa]
    if today_recs:
        lines += ["Earlier runs today:"]
        for r in today_recs:
            head = f"- {r['role']} ({r['status']}{', ' + r['proposalId'] if r['proposalId'] else ''}): "
            lines.append(head + q(_line(r["summary"], 600 if overview else 240), r))
            if overview:
                lines += [f"    unverified: {q(u['claim'], r)}" + (f" because {q(u['reason'], r)}" if u["reason"] else "") for u in r["unverified"][:8]]
    out: list[str] = []
    used = 0
    for i, line in enumerate(lines):
        if used + len(line) + 1 > MAX_BLOCK:
            out.append(f"... ({len(lines) - i} more lines not shown)")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out), withheld


def notes_for(output_uri: str, role: str, today: dt.date, session: Any = None, *, store: Any = None) -> str:
    """Read and render the notes for a role run; "" when there are none or they cannot be read (never raises).  Notes
    withheld for reading like instructions are recorded on ``session`` (the proposal is then marked for a person)."""
    if not output_uri or role not in ROLES:
        return ""
    try:
        text, withheld = render(read(output_uri, today, store=store), role)
    except Exception:  # noqa: BLE001 - see the docstring
        log.exception("reading the handoff notes failed")
        return ""
    if session is not None and withheld:
        session.suspicious_notes.extend(withheld)
    return text
