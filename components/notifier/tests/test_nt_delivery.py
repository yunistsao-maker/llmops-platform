import email
import email.policy
import json
import logging

import httpx
import pytest

from llmops_common.queue import Job, PermanentError, RetryLater, Worker
from notifier.handler import Notifier, mask, url_id
from notifier.settings import Settings

HOOK = "https://chat.googleapis.com/v1/spaces/AAA/messages?key=k&token=secret-token"
SETTINGS = dict(reviewers={"G2": ("legal@example.com", "legal2@example.com"), "G8": (), "G9": ()},
                admins=("ops@example.com",), webhook_urls=(HOOK,))


def _event_job(event, data, event_id=101, job_id=1):
    return Job(id=job_id, queue="notifier", payload={"event": event, "event_id": event_id, "payload": data},
               attempts=1, max_attempts=5)


def _jobs(db):
    with db.connection() as c:
        return c.execute("SELECT payload, dedupe_key, max_attempts FROM platform.jobs WHERE queue = 'notifier' "
                         "ORDER BY id").fetchall()


def test_event_fans_out_once_per_channel(db):
    n = Notifier(Settings(dry_run=False, smtp_host="smtp.example", smtp_from="llmops@example.com", **SETTINGS))
    job = _event_job("request.submitted", {"request_id": "r1", "team": "T", "next_gate": "G2", "gates": ["G0", "G2", "G9"]})
    n.handle(job)
    n.handle(job)                                           # redelivered: deduped
    rows = _jobs(db)
    assert [r["payload"]["delivery"] for r in rows] == ["email", "webhook"]
    assert rows[0]["payload"]["to"] == ["legal@example.com", "legal2@example.com"] and rows[0]["max_attempts"] == 8
    assert rows[1]["payload"]["webhook"] == url_id(HOOK) and HOOK not in json.dumps(rows[1]["payload"])
    assert "event" not in rows[0]["payload"]
    # personal messages stay off the shared chat; unknown events are acknowledged without sending
    n.handle(_event_job("request.decided", {"request_id": "r1", "gate": "G2", "decision": "reject", "comment": "no",
                                            "requester_email": "amy@example.com", "status": "rejected"}, event_id=102))
    n.handle(_event_job("research.proposal.ready", {"run_id": "x"}, event_id=103))
    rows = _jobs(db)
    assert [r["payload"]["delivery"] for r in rows] == ["email", "webhook", "email"]
    assert rows[2]["payload"]["to"] == ["amy@example.com"]


def test_webhook_events_filter_and_no_smtp(db):
    n = Notifier(Settings(dry_run=False, webhook_events=frozenset({"key.failed"}), **SETTINGS))   # no SMTP_HOST
    n.handle(_event_job("release.published", {"release_id": "r", "changes": {}}))
    n.handle(_event_job("key.failed", {"request_id": "r1", "error": "x"}, event_id=2))
    rows = _jobs(db)
    assert [(r["payload"]["delivery"], r["payload"]["source_event"]) for r in rows] == [("webhook", "key.failed")]


def test_dry_run_logs_instead_of_sending(db, capsys):
    from llmops_common.log import setup_logging

    setup_logging("notifier", "INFO")
    n = Notifier(Settings(dry_run=True, **SETTINGS))
    n.handle(_event_job("key.failed", {"request_id": "r1", "team": "T", "stage": "full", "error": "HTTP 503"}))
    w = Worker("notifier", n.handle, poll_seconds=0.01)
    while w.run_once():
        pass
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    dry = [entry for entry in lines if entry["msg"] == "dry-run notification"]
    assert {d["channel"] for d in dry} == {"email", "webhook"}
    assert dry[0]["to"] == ["o***@example.com"] and "金鑰開通失敗" in dry[0]["subject"]
    assert all(r["payload"] for r in _jobs(db))
    logging.getLogger().handlers.clear()


def test_smtp_delivery(smtp_server):
    s = Settings(dry_run=False, smtp_host="127.0.0.1", smtp_port=smtp_server.server_address[1], smtp_starttls=False,
                 smtp_from="LLMOps 平台 <llmops@example.com>", **SETTINGS)
    n = Notifier(s)
    n.deliver({"delivery": "email", "to": ["amy@example.com"], "subject": "【LLMOps】req-1 已通過法遵審查 | ok",
               "body": "你的模型申請已通過法遵審查（G2）。"})
    mail_from, rcpts, raw = smtp_server.messages[0]
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert rcpts == ["amy@example.com"] and "llmops@example.com" in mail_from
    assert msg["Subject"] == "【LLMOps】req-1 已通過法遵審查 | ok" and msg["Auto-Submitted"] == "auto-generated"
    assert msg.get_content().strip() == "你的模型申請已通過法遵審查（G2）。"
    smtp_server.data_reply = "550 mailbox unavailable"
    with pytest.raises(PermanentError):
        n.deliver({"delivery": "email", "to": ["x@example.com"], "subject": "s", "body": "b"})
    smtp_server.data_reply = "451 try later"
    with pytest.raises(RetryLater):
        n.deliver({"delivery": "email", "to": ["x@example.com"], "subject": "s", "body": "b"})
    smtp_server.data_reply, smtp_server.rcpt_reply = "250 ok", "550 no such user"
    with pytest.raises(PermanentError):
        n.deliver({"delivery": "email", "to": ["x@example.com"], "subject": "s", "body": "b"})
    down = Notifier(Settings(dry_run=False, smtp_host="127.0.0.1", smtp_port=1, smtp_starttls=False,
                             smtp_from="llmops@example.com", smtp_timeout=2))
    with pytest.raises(RetryLater):
        down.deliver({"delivery": "email", "to": ["x@example.com"], "subject": "s", "body": "b"})


def test_webhook_delivery_formats_and_errors():
    seen = []
    status = {"code": 200}

    def handler(request: httpx.Request):
        seen.append(json.loads(request.content))
        return httpx.Response(status["code"], headers={"retry-after": "7"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    n = Notifier(Settings(dry_run=False, **SETTINGS), http=http)
    d = {"delivery": "webhook", "webhook": url_id(HOOK), "subject": "【LLMOps】主旨", "body": "內容"}
    n.deliver(d)
    assert seen[-1] == {"text": "【LLMOps】主旨\n\n內容"}
    card = Notifier(Settings(dry_run=False, webhook_format="adaptivecard", **SETTINGS), http=http)
    card.deliver(d)
    assert seen[-1]["attachments"][0]["content"]["body"][0]["text"] == "【LLMOps】主旨"
    status["code"] = 503
    with pytest.raises(RetryLater) as exc:
        n.deliver(d)
    assert exc.value.delay == 7.0 and "secret-token" not in str(exc.value)
    status["code"] = 404
    with pytest.raises(PermanentError):
        n.deliver(d)
    n.deliver({**d, "webhook": "0" * 16})                    # URL removed from config: dropped, no error
    assert mask("amy@example.com") == "a***@example.com"


def _subscribe(db, rows):
    with db.connection() as c:
        for sub, email, model in rows:
            c.execute("INSERT INTO requests.subscriptions (subscriber_sub, subscriber_email, model_id) VALUES (%s, %s, %s)",
                      (sub, email, model))


def test_model_status_changed_goes_to_each_subscriber(db):
    _subscribe(db, [("u-amy", "amy@example.com", "gpt-6-luna"), ("u-bob", "bob@example.com", "gpt-6-luna"),
                    ("u-noemail", None, "gpt-6-luna"), ("u-amy", "amy@example.com", "claude-opus-5-5")])
    n = Notifier(Settings(dry_run=False, smtp_host="smtp.example", smtp_from="llmops@example.com",
                          model_url="https://portal.example/#/model/{model_id}", **SETTINGS))
    job = _event_job("model.status_changed", {"model_id": "gpt-6-luna", "model_name": "GPT-6 Luna",
                                              "from": "evaluating", "to": "trial"}, event_id=501)
    n.handle(job)
    n.handle(job)                                               # redelivered: deduped per (event id, subscriber)
    rows = _jobs(db)
    assert [r["payload"]["to"] for r in rows] == [["amy@example.com"], ["bob@example.com"]]   # one mail each
    assert all(r["payload"]["delivery"] == "email" and r["payload"]["audience"] == "subscriber" for r in rows)
    keys = [r["dedupe_key"] for r in rows]
    assert len(set(keys)) == 2 and all(k.startswith("notify:501:sub:") and k.endswith(":email") for k in keys)
    assert "u-amy" not in json.dumps([r["payload"] for r in rows])        # no subject ids in jobs
    assert "GPT-6 Luna 開放試用申請" in rows[0]["payload"]["subject"]
    # another event id for the same change is a new notification; paused / retired send nothing
    n.handle(_event_job("model.status_changed", {"model_id": "gpt-6-luna", "from": "trial", "to": "available"},
                        event_id=502))
    n.handle(_event_job("model.status_changed", {"model_id": "gpt-6-luna", "from": "available", "to": "paused"},
                        event_id=503))
    n.handle(_event_job("model.status_changed", {"model_id": "gpt-6-luna", "from": "paused", "to": "retired"},
                        event_id=504))
    assert len(_jobs(db)) == 4
    n.handle(_event_job("model.status_changed", {"model_id": "nobody-follows", "to": "available"}, event_id=505))
    assert len(_jobs(db)) == 4
    with pytest.raises(PermanentError):
        n.handle(_event_job("model.status_changed", {"to": "available"}, event_id=506))
