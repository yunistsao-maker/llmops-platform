"""``research.proposal.ready``: tell the publisher which proposals the run produced (contracts/events.md).

Payload ``{run_date, run_id, proposal_keys, roles}``:
    run_date        the run's day in Asia/Taipei (YYYY-MM-DD; the day the handoff notes and digests are filed under)
    run_id          this run's id (llmops_common.ids.new_run_id)
    proposal_keys   curated-bucket keys of the accepted proposals, in role order (research/proposals/<id>.json)
    roles           the roles this run ran, in order (each proposal also records its role in task.role)

Published with the dedupe key ``research.proposal.ready:<run_id>``, so publishing again (a retry) fans out once.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Iterable

from llmops_common.log import kv

from research.objectstore import split_uri

if TYPE_CHECKING:  # (no Strands import just to publish an event)
    from .daily import RoleOutcome

log = logging.getLogger("crawler_agent.events")

EVENT = "research.proposal.ready"
SOURCE = "crawler-agent"


def proposal_key(saved: str | None, curated_bucket: str) -> str | None:
    """The curated-bucket key of a saved proposal (``s3://<curated>/research/proposals/<id>.json``), else None."""
    if not saved or not saved.startswith("s3://"):
        return None
    bucket, key = split_uri(saved)[0], saved[len("s3://"):].partition("/")[2]
    if bucket != curated_bucket or "/proposals/" not in "/" + key or not key.endswith(".json"):
        return None
    return key


def payload(*, run_date: str, run_id: str, outcomes: Iterable["RoleOutcome"], curated_bucket: str, roles: Iterable[str]) -> dict:
    keys: list[str] = []
    for o in outcomes:
        if o.status != "accepted" or not o.proposal_id:
            continue
        key = proposal_key(o.saved, curated_bucket)
        if key is None:  # (the CLI refuses an OUTPUT_URI outside the curated bucket; a proposal must never be dropped silently)
            log.error("accepted proposal is not in the curated bucket; the publisher cannot read it",
                      extra=kv(role=o.role, proposal_id=o.proposal_id, saved=o.saved))
        elif key not in keys:  # (two roles that propose the very same content share one proposal id)
            keys.append(key)
    return {"run_date": run_date, "run_id": run_id, "proposal_keys": keys, "roles": list(roles)}


_KEY = re.compile(r"research/proposals/[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.json")
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}")
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def check_payload(body: object) -> dict:
    """Raise ValueError unless ``body`` has the contract's shape (used when a payload is typed in by a person)."""
    if not isinstance(body, dict) or set(body) != {"run_date", "run_id", "proposal_keys", "roles"}:
        raise ValueError("payload must be an object with exactly run_date, run_id, proposal_keys, roles")
    if not isinstance(body["run_date"], str) or not _DAY.fullmatch(body["run_date"]):
        raise ValueError("run_date must be YYYY-MM-DD")
    if not isinstance(body["run_id"], str) or not _RUN_ID.fullmatch(body["run_id"]):
        raise ValueError("run_id is not a run id")
    keys = body["proposal_keys"]
    if not isinstance(keys, list) or not all(isinstance(k, str) and _KEY.fullmatch(k) for k in keys):
        raise ValueError("proposal_keys must be a list of research/proposals/<id>.json keys")
    if not isinstance(body["roles"], list) or not all(isinstance(r, str) for r in body["roles"]):
        raise ValueError("roles must be a list of role names")
    return body


def dedupe_key(run_id: str) -> str:
    return f"{EVENT}:{run_id}"


def publish_ready(body: dict, *, attempts: int = 5, wait: float = 2.0, connection=None) -> int | None:
    """Publish the event in its own transaction; retried with backoff (the research behind it is already saved, and a
    lost event would mean re-running hours of research).  Returns the event id (None: published before)."""
    from llmops_common import db, queue

    connect = connection or db.connection
    for attempt in range(1, attempts + 1):
        try:
            with connect() as conn:
                event_id = queue.publish(conn, EVENT, body, source=SOURCE, dedupe_key=dedupe_key(body["run_id"]))
            log.info("event published", extra=kv(event=EVENT, event_id=event_id, run_id=body["run_id"],
                                                 proposals=len(body["proposal_keys"]), duplicate=event_id is None))
            return event_id
        except Exception as e:  # noqa: BLE001 - database hiccup: wait and retry
            if attempt >= attempts:
                raise
            log.warning("publishing the event failed; retrying", extra=kv(attempt=attempt, error=f"{type(e).__name__}: {e}"))
            time.sleep(wait * attempt)
    return None  # pragma: no cover
