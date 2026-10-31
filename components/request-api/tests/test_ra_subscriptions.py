"""GET/PUT/DELETE /api/subscriptions ("notify me when this model can be requested") and the path to the notifier."""
import json
import logging
import sys

from ra_support import OTHER, REQUESTER, ROOT, Api

from llmops_common.auth import Principal


def _put(api, user, model_id):
    return api.call(user, "PUT", f"/api/subscriptions/{model_id}")


def _models(api, user):
    return [i["model_id"] for i in api.get(user, "/api/subscriptions").json()["items"]]


def test_follow_list_unfollow_own_rows_only(api, db):
    r = _put(api, REQUESTER, "gpt-6-luna")
    assert r.status_code == 201 and r.headers["location"] == "/api/subscriptions/gpt-6-luna"
    first = r.json()
    assert first["model_id"] == "gpt-6-luna" and first["created_at"]
    again = _put(api, REQUESTER, "gpt-6-luna")
    assert again.status_code == 200 and again.json() == first             # idempotent, created_at kept
    assert _put(api, REQUESTER, "claude-opus-5-5").status_code == 201
    listed = api.get(REQUESTER, "/api/subscriptions").json()
    assert [i["model_id"] for i in listed["items"]] == ["claude-opus-5-5", "gpt-6-luna"]
    assert set(listed["items"][0]) == {"model_id", "created_at"}
    assert api.get(OTHER, "/api/subscriptions").json() == {"items": []}
    assert api.call(OTHER, "DELETE", "/api/subscriptions/gpt-6-luna").status_code == 204   # not theirs: no effect
    assert _models(api, REQUESTER) == ["claude-opus-5-5", "gpt-6-luna"]
    assert api.call(REQUESTER, "DELETE", "/api/subscriptions/gpt-6-luna").status_code == 204
    assert api.call(REQUESTER, "DELETE", "/api/subscriptions/gpt-6-luna").status_code == 204  # already gone
    assert _models(api, REQUESTER) == ["claude-opus-5-5"]
    with db.connection() as c:
        rows = c.execute("SELECT subscriber_sub, subscriber_email, model_id FROM requests.subscriptions").fetchall()
    assert rows == [{"subscriber_sub": "u-amy", "subscriber_email": "amy@example.com", "model_id": "claude-opus-5-5"}]


def test_model_id_must_match_the_catalog_pattern(api, db):
    for bad in ("Claude-Opus", "a", "x" * 82, "gpt_6", "-gpt"):
        assert _put(api, REQUESTER, bad).status_code == 422, bad
        assert api.call(REQUESTER, "DELETE", f"/api/subscriptions/{bad}").status_code == 422, bad
    assert _put(api, REQUESTER, "gpt-6.1-sol").status_code == 201
    assert _put(api, REQUESTER, "x" * 81).status_code == 201


def test_cap_per_user(db, role_env, monkeypatch):
    from request_api.app import create_app

    monkeypatch.setenv("MAX_SUBSCRIPTIONS_PER_USER", "2")
    api = Api(create_app())
    assert [_put(api, REQUESTER, m).status_code for m in ("m-1", "m-2", "m-3")] == [201, 201, 429]
    assert _put(api, REQUESTER, "m-1").status_code == 200                  # already followed: fine at the cap
    assert _put(api, OTHER, "m-3").status_code == 201                       # the cap is per user
    assert api.call(REQUESTER, "DELETE", "/api/subscriptions/m-1").status_code == 204
    assert _put(api, REQUESTER, "m-3").status_code == 201
    assert _models(api, REQUESTER) == ["m-2", "m-3"]


def test_email_is_kept_current(api, db):
    sub = "u-chen"
    assert _put(api, Principal(sub=sub, email="old@example.com"), "gpt-6-luna").status_code == 201
    assert _put(api, Principal(sub=sub, email="new@example.com"), "gpt-6-luna").status_code == 200
    assert _put(api, Principal(sub=sub), "gpt-6-luna").status_code == 200            # no email claim: keep the old
    assert _put(api, Principal(sub="svc-1"), "gpt-6-luna").status_code == 201        # stored, cannot be mailed
    with db.connection() as c:
        emails = {r["subscriber_sub"]: r["subscriber_email"] for r in
                  c.execute("SELECT subscriber_sub, subscriber_email FROM requests.subscriptions").fetchall()}
    assert emails == {"u-chen": "new@example.com", "svc-1": None}


def test_followers_hear_when_the_model_opens(api, db, capsys):
    """portal follow -> publisher's model.status_changed -> notifier: one mail per follower with an email."""
    for path in ("components/notifier/src",):
        if str(ROOT / path) not in sys.path:
            sys.path.insert(0, str(ROOT / path))
    from llmops_common import queue
    from llmops_common.log import setup_logging
    from notifier.handler import Notifier
    from notifier.settings import Settings as NotifySettings

    for user in (REQUESTER, OTHER, Principal(sub="svc-1")):
        assert _put(api, user, "gpt-6-luna").status_code == 201
    _put(api, REQUESTER, "claude-opus-5-5")
    with db.connection() as c:
        for to in ("trial", "paused"):
            queue.publish(c, "model.status_changed", {"model_id": "gpt-6-luna", "model_name": "GPT-6 Luna",
                                                      "from": "evaluating", "to": to},
                          source="publisher", dedupe_key=f"model.status_changed:gpt-6-luna:{to}:20261101T000000Z")
    setup_logging("e2e", "INFO")
    notifier = Notifier(NotifySettings(dry_run=True, model_url="https://llmops.example/#/model/{model_id}"))
    worker = queue.Worker("notifier", notifier.handle, poll_seconds=0.01)
    while worker.run_once():
        pass
    out = capsys.readouterr().out
    logging.getLogger().handlers.clear()
    sent = [json.loads(line) for line in out.splitlines() if '"dry-run notification"' in line]
    assert sorted(m["to"][0] for m in sent) == ["a***@example.com", "b***@example.com"]
    assert all(m["subject"].startswith("【LLMOps】GPT-6 Luna 開放試用申請") for m in sent)
    assert all("https://llmops.example/#/model/gpt-6-luna" in m["body"] for m in sent)
    with db.connection() as c:
        states = {r["status"] for r in c.execute("SELECT status FROM platform.jobs WHERE queue = 'notifier'").fetchall()}
    assert states == {"done"}
