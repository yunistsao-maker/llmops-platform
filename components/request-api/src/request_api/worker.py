"""Background consumer of queue `request-api`: key.provisioned / key.failed from key-provisioner.

Runs as a thread inside the API process (REQUEST_API_WORKER=true). Each job is one transaction: lock the
request, check it is still `provisioning` for the same stage, update status + key reference, write audit.
Stale or duplicate events (request no longer provisioning, other stage) are acknowledged and logged.
"""
from __future__ import annotations

import uuid

from llmops_common import db
from llmops_common.log import get_logger, kv, redact
from llmops_common.queue import Job, PermanentError

from . import domain, store

QUEUE = "request-api"
log = get_logger("request_api.worker")


class KeyEventHandler:
    def __init__(self, connection_factory=None):
        self.connection = connection_factory or db.connection

    def __call__(self, job: Job) -> None:
        event = job.event
        if event not in ("key.provisioned", "key.failed"):
            log.info("event ignored", extra=kv(job_id=job.id, event=event))
            return
        data = job.data
        try:
            request_id = uuid.UUID(str(data.get("request_id")))
        except ValueError as exc:
            raise PermanentError(f"{event} without a valid request_id") from exc
        if event == "key.provisioned" and not (data.get("key_alias") and data.get("secret_ref")):
            raise PermanentError("key.provisioned without key_alias or secret_ref")

        with self.connection() as conn:
            row = store.get_request(conn, request_id, lock=True)
            if row is None:
                log.warning("event for unknown request", extra=kv(event=event, request_id=str(request_id)))
                return
            expected = domain.stage_of(row["gates"])
            stage = data.get("stage") or expected
            if row["status"] != "provisioning" or stage != expected:
                log.info("stale key event ignored", extra=kv(event=event, request_id=str(request_id),
                                                             status=row["status"], stage=stage, expected=expected))
                return
            if event == "key.provisioned":
                status = "trial" if stage == "trial" else "active"
                store.update_request(conn, request_id, status=status, key_alias=data["key_alias"],
                                     secret_ref=data["secret_ref"])
                store.add_audit(conn, request_id, "key_provisioned", "key-provisioner",
                                {"stage": stage, "key_alias": data["key_alias"], "secret_ref": data["secret_ref"],
                                 "expires_at": data.get("expires_at")})
            else:
                status = "failed"
                error = redact(str(data.get("error") or "unknown error"))[:1000]
                store.update_request(conn, request_id, status=status)
                store.add_audit(conn, request_id, "key_failed", "key-provisioner",
                                {"stage": stage, "error": error, "key_alias": data.get("key_alias")})
        log.info("request status updated", extra=kv(event=event, request_id=str(request_id), status=status, stage=stage))
