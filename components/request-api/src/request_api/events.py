"""Events published by request-api (contracts/events.md), always inside the caller's transaction.

Contract fields first; the extra fields (marked +) are additions documented in the README:
request.submitted  {request_id, team, alias, gates, next_gate, +ref, +status, +requester_email, +models, +unsure,
                    +data_class, +data_class_other, +rpd, +external, +taiwan_only, +revision}
request.decided    {request_id, gate, decision, comment, actor, +ref, +team, +alias, +requester_email, +status,
                    +next_gate}
request.approved   {request_id, team, alias, models, stage, budget_usd, rpm, tpm, duration, +ref, +unsure,
                    +budget_duration, +requester_email, +attempt}
"""
from __future__ import annotations

from llmops_common import queue
from llmops_common.log import get_logger, kv

from . import domain

SOURCE = "request-api"
log = get_logger("request_api.events")


def _publish(conn, event: str, payload: dict, dedupe_key: str) -> int | None:
    event_id = queue.publish(conn, event, payload, source=SOURCE, dedupe_key=dedupe_key)
    if event_id is None:
        log.warning("event already published", extra=kv(event=event, dedupe_key=dedupe_key))
    return event_id


def submitted(conn, row: dict, revision: int) -> int | None:
    rid = str(row["id"])
    payload = {
        "request_id": rid,
        "ref": domain.ref(rid),
        "team": row["team"],
        "alias": row["alias"],
        "gates": domain.gate_ids(row["gates"]),
        "next_gate": domain.next_gate(row["status"], row["gates"]),
        "status": row["status"],
        "requester_email": row["requester_email"],
        "models": list(row["models"] or []),
        "unsure": row["unsure"],
        "data_class": row["data_class"],
        "data_class_other": row["data_class_other"],
        "rpd": row["rpd"],
        "external": row["external"],
        "taiwan_only": row["taiwan_only"],
        "revision": revision,
    }
    return _publish(conn, "request.submitted", payload, f"request.submitted:{rid}:{revision}")


def decided(conn, row: dict, decision_id: int, gate: str, decision: str, comment: str | None, actor: str) -> int | None:
    rid = str(row["id"])
    payload = {
        "request_id": rid,
        "gate": gate,
        "decision": decision,
        "comment": comment,
        "actor": actor,
        "ref": domain.ref(rid),
        "team": row["team"],
        "alias": row["alias"],
        "requester_email": row["requester_email"],
        "status": row["status"],
        "next_gate": domain.next_gate(row["status"], row["gates"]),
    }
    return _publish(conn, "request.decided", payload, f"request.decided:{decision_id}")


def approved(conn, row: dict, stage: str, limits: dict, attempt: int = 1) -> int | None:
    rid = str(row["id"])
    payload = {
        "request_id": rid,
        "team": row["team"],
        "alias": row["alias"],
        "models": list(row["models"] or []),
        "stage": stage,
        "budget_usd": limits["budget_usd"],
        "rpm": limits["rpm"],
        "tpm": limits["tpm"],
        "duration": limits["duration"],
        "ref": domain.ref(rid),
        "unsure": row["unsure"],
        "budget_duration": limits["budget_duration"],
        "requester_email": row["requester_email"],
        "attempt": attempt,
    }
    dedupe = f"request.approved:{rid}:{stage}" + (f":retry{attempt}" if attempt > 1 else "")
    return _publish(conn, "request.approved", payload, dedupe)
