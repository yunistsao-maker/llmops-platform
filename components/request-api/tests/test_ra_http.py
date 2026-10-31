import uuid

from ra_support import ADMIN, G2, G8, OTHER, REQUESTER, Api, events, jobs, valid_request

from llmops_common.auth import Principal


def _submit(api, user=REQUESTER, **over):
    r = api.post(user, "/api/requests", json=valid_request(**over))
    assert r.status_code == 201, r.text
    return r.json()


def _decide(api, user, rid, gate, decision="approve", comment=None):
    return api.post(user, f"/api/requests/{rid}/decisions", json={"gate": gate, "decision": decision, "comment": comment})


def test_submit_review_flow_publishes_events_in_order(api, db):
    created = _submit(api, data_class="sec-b", external=True)
    rid = created["id"]
    assert created["status"] == "in_review" and created["next_gate"] == "G2"
    assert [g["gate"] for g in created["gates"]] == ["G0", "G2", "G8", "G9"]
    sub = events(db, "request.submitted")[0]
    assert sub["dedupe_key"] == f"request.submitted:{rid}:1"
    assert {k: sub["payload"][k] for k in ("request_id", "team", "alias", "gates", "next_gate", "requester_email")} == {
        "request_id": rid, "team": "智慧製造處", "alias": "amy", "gates": ["G0", "G2", "G8", "G9"], "next_gate": "G2",
        "requester_email": "amy@example.com"}
    assert [j["payload"]["event"] for j in jobs(db, "notifier")] == ["request.submitted"]

    assert _decide(api, G8, rid, "G8").status_code == 409            # G2 comes first
    assert _decide(api, OTHER, rid, "G2").status_code == 404         # not a reviewer, not the owner
    assert _decide(api, REQUESTER, rid, "G2").status_code == 403     # owner without the role
    r = _decide(api, G2, rid, "G2", comment="資料留在日本區可以")
    assert r.status_code == 200 and r.json()["next_gate"] == "G8" and r.json()["status"] == "in_review"
    r = _decide(api, G8, rid, "G8")
    assert r.json()["status"] == "provisioning" and r.json()["stage"] == "trial"

    decided = events(db, "request.decided")
    assert [(e["payload"]["gate"], e["payload"]["next_gate"], e["payload"]["status"]) for e in decided] == [
        ("G2", "G8", "in_review"), ("G8", None, "provisioning")]
    assert decided[0]["payload"]["actor"] == "legal@example.com" and decided[0]["payload"]["comment"] == "資料留在日本區可以"
    approved = events(db, "request.approved")
    assert len(approved) == 1 and approved[0]["dedupe_key"] == f"request.approved:{rid}:trial"
    p = approved[0]["payload"]
    assert (p["stage"], p["budget_usd"], p["rpm"], p["tpm"], p["duration"], p["models"]) == (
        "trial", 1733.0, 35, 122500, "14d", ["claude-sonnet-5-5"])
    assert [j["payload"]["event"] for j in jobs(db, "key-provisioner")] == ["request.approved"]
    with db.connection() as c:
        audit = [r["action"] for r in c.execute("SELECT action FROM requests.audit WHERE request_id = %s ORDER BY id",
                                                (rid,)).fetchall()]
        decisions = c.execute("SELECT gate, decision, actor_sub FROM requests.decisions ORDER BY id").fetchall()
    assert audit == ["submit", "decision", "decision", "launch"]
    assert [(d["gate"], d["actor_sub"]) for d in decisions] == [("G2", "u-legal"), ("G8", "u-beta")]


def test_no_review_needed_launches_at_once(api, db):
    created = _submit(api, data_class="sec-c", rpd=500)
    assert created["status"] == "provisioning" and created["stage"] == "full" and created["next_gate"] is None
    assert created["gates"][-1] == {"gate": "G9", "status": "approved"}
    p = events(db, "request.approved")[0]["payload"]
    assert p["stage"] == "full" and p["duration"] is None and p["budget_duration"] == "30d"
    assert events(db, "request.submitted")[0]["payload"]["next_gate"] is None


def test_validation_alias_and_limits(api, db, monkeypatch):
    r = api.post(REQUESTER, "/api/requests", json=valid_request(models=[], data_class="other", extra="x"))
    assert r.status_code == 422
    locs = {tuple(e["loc"]) for e in r.json()["detail"]}
    assert ("body",) in locs and ("body", "models") in locs
    assert api.post(REQUESTER, "/api/requests", content=b"{not json", headers={"content-type": "application/json"}).status_code == 422
    assert api.post(REQUESTER, "/api/requests", json=[1, 2]).status_code == 422
    custom = _submit(api, alias="j.chen")
    no_email = Principal(sub="svc-1")
    own = api.post(no_email, "/api/requests", json=valid_request()).json()
    with db.connection() as c:
        aliases = {r["id"]: r["alias"] for r in c.execute("SELECT id::text AS id, alias FROM requests.requests").fetchall()}
    assert aliases[custom["id"]] == "j.chen" and aliases[own["id"]] == "svc-1"
    assert api.post(REQUESTER, "/api/requests", content=b"x" * 70000,
                    headers={"content-type": "application/json"}).status_code == 413
    chunked = api.post(REQUESTER, "/api/requests", content=iter([b"[" + b"1," * 20000, b"1," * 20000 + b"1]"]),
                       headers={"content-type": "application/json"})          # no Content-Length: counted
    assert chunked.status_code == 413
    assert events(db, "request.submitted") and len(events(db, "request.submitted")) == 2


def test_open_request_limit(db, role_env, monkeypatch):
    from request_api.app import create_app

    monkeypatch.setenv("MAX_OPEN_REQUESTS_PER_USER", "2")
    api = Api(create_app())
    _submit(api, data_class="sec-b")
    _submit(api, data_class="sec-b")
    assert api.post(REQUESTER, "/api/requests", json=valid_request(data_class="sec-b")).status_code == 429
    assert api.post(OTHER, "/api/requests", json=valid_request(data_class="sec-b")).status_code == 201


def test_listing_permissions_filters_and_pagination(api, db):
    mine = [_submit(api, data_class="sec-b")["id"] for _ in range(3)]
    other = _submit(api, OTHER, data_class="sec-a", rpd=20000)["id"]
    _decide(api, G2, other, "G2")
    assert api.get(REQUESTER, "/api/requests").status_code == 403
    r = api.get(REQUESTER, "/api/requests?mine=1&limit=2")
    page1 = r.json()
    assert [i["id"] for i in page1["items"]] == mine[::-1][:2] and page1["next_cursor"]
    page2 = api.get(REQUESTER, f"/api/requests?mine=1&limit=2&cursor={page1['next_cursor']}").json()
    assert [i["id"] for i in page2["items"]] == [mine[0]] and page2["next_cursor"] is None
    assert len(api.get(G2, "/api/requests").json()["items"]) == 4
    assert [i["id"] for i in api.get(G8, "/api/requests?next_gate=G8").json()["items"]] == [other]
    assert len(api.get(G2, "/api/requests?next_gate=G2&status=in_review").json()["items"]) == 3
    assert api.get(ADMIN, "/api/requests?status=in_review,provisioning").status_code == 200
    assert api.get(ADMIN, "/api/requests?status=bogus").status_code == 422
    assert api.get(ADMIN, "/api/requests?cursor=zzz").status_code == 400
    assert api.get(ADMIN, "/api/requests?limit=1000").status_code == 422
    assert api.get(ADMIN, "/api/requests?next_gate=G5").status_code == 422


def test_detail_visibility_and_permissions(api, db):
    rid = _submit(api, data_class="sec-b", rpd=10000)["id"]
    d = api.get(REQUESTER, f"/api/requests/{rid}").json()
    assert d["use_case"] == "客服摘要" and d["permissions"] == {"withdraw": True, "resubmit": False, "decide": [],
                                                                 "reprovision": False}
    assert d["limits"]["proposed"] and d["limits"]["stage"] == "trial" and d["history"][0]["action"] == "submit"
    assert api.get(OTHER, f"/api/requests/{rid}").status_code == 404
    assert api.get(G2, f"/api/requests/{rid}").json()["permissions"]["decide"] == ["G2"]
    assert api.get(G8, f"/api/requests/{rid}").json()["permissions"]["decide"] == []
    assert api.get(ADMIN, f"/api/requests/{uuid.uuid4()}").status_code == 404
    assert api.get(ADMIN, "/api/requests/not-a-uuid").status_code == 422


def test_changes_requested_resubmit_and_withdraw(api, db):
    rid = _submit(api, data_class="sec-b", external=True)["id"]
    _decide(api, G2, rid, "G2")
    assert _decide(api, G8, rid, "G8", "request_changes").status_code == 422          # comment required
    r = _decide(api, G8, rid, "G8", "request_changes", comment="請降低每日用量並說明試用範圍")
    assert r.json()["status"] == "changes_requested"
    assert api.post(OTHER, f"/api/requests/{rid}/resubmit", json={}).status_code == 404
    assert api.post(G2, f"/api/requests/{rid}/resubmit", json={}).status_code == 403
    assert api.post(REQUESTER, f"/api/requests/{rid}/resubmit", json={"bogus": 1}).status_code == 422
    assert api.post(REQUESTER, f"/api/requests/{rid}/resubmit", json={"rpd": -5}).status_code == 422
    r = api.post(REQUESTER, f"/api/requests/{rid}/resubmit", json={"external": False, "rpd": 2000, "note": "內部試點"})
    body = r.json()
    assert r.status_code == 200 and body["status"] == "in_review" and body["next_gate"] == "G2"
    assert [g["gate"] for g in body["gates"]] == ["G0", "G2", "G9"]                      # G8 no longer required
    assert all(g["status"] == "pending" for g in body["gates"][1:])                      # reviewed again from G2
    subs = events(db, "request.submitted")
    assert [s["payload"]["revision"] for s in subs] == [1, 2] and subs[1]["dedupe_key"].endswith(":2")
    hist = api.get(REQUESTER, f"/api/requests/{rid}").json()["history"]
    assert hist[-1]["action"] == "resubmit" and hist[-1]["detail"]["changed"] == ["external", "note", "rpd"]
    assert api.post(REQUESTER, f"/api/requests/{rid}/resubmit", json={}).status_code == 409
    assert api.post(G2, f"/api/requests/{rid}/withdraw").status_code == 403
    r = api.post(REQUESTER, f"/api/requests/{rid}/withdraw", json={"reason": "改用別的方案"})
    assert r.json()["status"] == "withdrawn"
    assert api.post(REQUESTER, f"/api/requests/{rid}/withdraw").status_code == 409
    assert _decide(api, G2, rid, "G2").status_code == 409


def test_reject_is_final(api, db):
    rid = _submit(api, data_class="sec-a")["id"]
    assert _decide(api, G2, rid, "G2", "reject").status_code == 422
    r = _decide(api, G2, rid, "G2", "reject", comment="Security A 不可送雲端")
    assert r.json()["status"] == "rejected" and r.json()["gates"][1] == {"gate": "G2", "status": "rejected"}
    assert _decide(api, ADMIN, rid, "G2").status_code == 409
    assert events(db, "request.approved") == []


def test_self_approval_and_admin(api, db):
    reviewer_requester = Principal(sub="u-legal", email="legal@example.com", groups=("legal-reviewers",))
    rid = _submit(api, reviewer_requester, data_class="sec-b")["id"]
    r = _decide(api, G2, rid, "G2")                      # same sub as the requester
    assert r.status_code == 403 and "own request" in r.json()["detail"]
    r = _decide(api, ADMIN, rid, "G2")
    assert r.status_code == 200 and r.json()["status"] == "provisioning" and r.json()["stage"] == "full"


def test_trial_launch_decision_and_reprovision(api, db):
    from llmops_common.queue import Job
    from request_api.worker import KeyEventHandler

    rid = _submit(api, data_class="sec-c", rpd=12000)["id"]                       # G8 only
    _decide(api, G8, rid, "G8")
    handler = KeyEventHandler()
    handler(Job(1, "request-api", {"event": "key.provisioned", "event_id": 9, "payload": {
        "request_id": rid, "key_alias": f"req-{rid[:8]}-trial", "secret_ref": "vault:secret/x", "stage": "trial",
        "expires_at": "2026-11-14T00:00:00Z"}}, 1, 5))
    d = api.get(REQUESTER, f"/api/requests/{rid}").json()
    assert d["status"] == "trial" and d["next_gate"] == "G9" and d["key"]["expires_at"] == "2026-11-14T00:00:00Z"
    assert _decide(api, G8, rid, "G9", "request_changes", comment="x").status_code == 422
    assert _decide(api, G2, rid, "G9").status_code == 403
    r = _decide(api, G8, rid, "G9", comment="試用順利")
    assert r.json()["status"] == "provisioning" and r.json()["stage"] == "full"
    assert [e["payload"]["stage"] for e in events(db, "request.approved")] == ["trial", "full"]
    handler(Job(2, "request-api", {"event": "key.failed", "event_id": 10, "payload": {
        "request_id": rid, "stage": "full", "error": "gateway 503 Bearer abcdefghijklmnopq"}}, 1, 5))
    d = api.get(ADMIN, f"/api/requests/{rid}").json()
    assert d["status"] == "failed" and d["permissions"]["reprovision"]
    assert "abcdefghijklmnopq" not in str(d["history"])
    assert api.post(REQUESTER, f"/api/requests/{rid}/reprovision").status_code == 403
    r = api.post(ADMIN, f"/api/requests/{rid}/reprovision")
    assert r.json()["status"] == "provisioning"
    assert [e["dedupe_key"].rsplit(":", 1)[-1] for e in events(db, "request.approved")] == ["trial", "full", "retry2"]
    assert api.post(ADMIN, f"/api/requests/{rid}/reprovision").status_code == 409


def test_health_and_openapi(api, db):
    assert api.client.get("/healthz").json() == {"status": "ok"}
    assert api.client.get("/readyz").json() == {"status": "ready"}
    assert "/api/requests/{request_id}/decisions" in api.client.get("/api/requests/openapi.json").json()["paths"]


def test_dev_auth_mode_end_to_end(db, monkeypatch):
    """AUTH_MODE=dev (no dependency override): the dev user holds roles through DEV_USER_GROUPS + ROLE_* env."""
    from fastapi.testclient import TestClient

    from request_api.app import create_app

    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("DEV_USER_EMAIL", "dev@example.local")
    monkeypatch.setenv("DEV_USER_GROUPS", "llmops-admins")
    monkeypatch.setenv("ROLE_ADMIN", "llmops-admins")
    monkeypatch.setenv("REQUEST_API_WORKER", "false")
    monkeypatch.delenv("ALLOW_SELF_APPROVAL", raising=False)        # dev mode: self-approval allowed by default
    client = TestClient(create_app())
    r = client.post("/api/requests", json=valid_request(data_class="sec-b"))
    assert r.status_code == 201 and r.json()["next_gate"] == "G2"
    rid = r.json()["id"]
    assert client.get("/api/requests?mine=1").json()["items"][0]["alias"] == "dev"
    r = client.post(f"/api/requests/{rid}/decisions", json={"gate": "G2", "decision": "approve"})
    assert r.status_code == 200 and r.json()["status"] == "provisioning"
