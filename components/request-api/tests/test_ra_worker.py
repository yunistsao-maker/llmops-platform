import uuid

import pytest
from ra_support import G2, REQUESTER, valid_request

from llmops_common import queue
from llmops_common.queue import Job, PermanentError
from request_api.worker import KeyEventHandler


def _job(event, payload, n=1):
    return Job(n, "request-api", {"event": event, "event_id": n, "payload": payload}, 1, 5)


def _status(db, rid):
    with db.connection() as c:
        return c.execute("SELECT status, key_alias, secret_ref FROM requests.requests WHERE id = %s", (rid,)).fetchone()


def test_provisioned_and_stale_events(api, db):
    rid = api.post(REQUESTER, "/api/requests", json=valid_request(data_class="sec-c", rpd=100)).json()["id"]  # full
    h = KeyEventHandler()
    h(_job("key.provisioned", {"request_id": rid, "key_alias": "a-trial", "secret_ref": "x", "stage": "trial"}))
    assert _status(db, rid)["status"] == "provisioning"                         # wrong stage: ignored
    h(_job("key.provisioned", {"request_id": rid, "key_alias": f"req-{rid[:8]}-full",
                               "secret_ref": "vault:secret/llmops/keys/k", "stage": "full"}, 2))
    row = _status(db, rid)
    assert (row["status"], row["key_alias"], row["secret_ref"]) == ("active", f"req-{rid[:8]}-full", "vault:secret/llmops/keys/k")
    h(_job("key.failed", {"request_id": rid, "stage": "full", "error": "late"}, 3))   # stale: still active
    assert _status(db, rid)["status"] == "active"
    h(_job("key.provisioned", {"request_id": str(uuid.uuid4()), "key_alias": "k", "secret_ref": "s"}, 4))  # unknown
    h(_job("request.submitted", {"request_id": rid}, 5))                              # not ours: acknowledged


def test_bad_payloads_are_parked(api, db):
    rid = api.post(REQUESTER, "/api/requests", json=valid_request(data_class="sec-b")).json()["id"]
    api.post(G2, f"/api/requests/{rid}/decisions", json={"gate": "G2", "decision": "approve"})
    h = KeyEventHandler()
    with pytest.raises(PermanentError):
        h(_job("key.provisioned", {"request_id": "not-a-uuid", "key_alias": "k", "secret_ref": "s"}))
    with pytest.raises(PermanentError):
        h(_job("key.provisioned", {"request_id": rid, "stage": "full"}))              # no key_alias / secret_ref
    assert _status(db, rid)["status"] == "provisioning"


def test_worker_thread_consumes_the_queue(db, role_env, monkeypatch):
    """The API process runs the key.* consumer as a background thread (lifespan) and stops it on shutdown."""
    import time

    from fastapi.testclient import TestClient

    from request_api.app import create_app

    monkeypatch.setenv("REQUEST_API_WORKER", "true")
    monkeypatch.setenv("WORKER_POLL_SECONDS", "0.05")
    app = create_app()
    with TestClient(app) as client:
        app.dependency_overrides[app.state.current_user] = lambda: REQUESTER
        rid = client.post("/api/requests", json=valid_request(data_class="sec-c", rpd=100)).json()["id"]
        with db.connection() as c:
            queue.publish(c, "key.provisioned", {"request_id": rid, "key_alias": f"req-{rid[:8]}-full",
                                                 "secret_ref": "vault:secret/x", "stage": "full"}, source="test")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and _status(db, rid)["status"] != "active":
            time.sleep(0.05)
        assert _status(db, rid)["status"] == "active"
    with db.connection() as c:
        assert c.execute("SELECT status FROM platform.jobs WHERE queue = 'request-api'").fetchone()["status"] == "done"
