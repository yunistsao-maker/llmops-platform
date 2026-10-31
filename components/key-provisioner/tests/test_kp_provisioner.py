import json
import logging

import httpx
import pytest
from kp_fakes import REQUEST_ID, approved_payload

from key_provisioner.litellm import AdminError, LiteLLMAdmin
from key_provisioner.provisioner import Approved, ProvisionError, candidate_aliases
from key_provisioner.settings import ConfigError, Settings, parse_alias_map
from key_provisioner.sinks import SinkError
from llmops_common.queue import Job, PermanentError, RetryLater


def _req(**over) -> Approved:
    return Approved.parse(approved_payload(**over))


def test_fresh_key_is_generated_with_limits_and_stored(make_provisioner, fake_litellm, tmp_path):
    p = make_provisioner(model_alias_map=parse_alias_map('{"claude-sonnet-5-5": ["sonnet-tw", "sonnet-us"]}'))
    result = p.provision(_req())
    assert result.key_alias == "req-3f2a9c41-trial" and not result.reused
    gen = [b for m, path, b in fake_litellm.calls if path == "/key/generate"]
    assert len(gen) == 1
    assert gen[0] == {"key_alias": "req-3f2a9c41-trial", "models": ["sonnet-tw", "sonnet-us"], "duration": "14d",
                      "metadata": {"request_id": REQUEST_ID, "team": "智慧製造處", "requester": "amy", "stage": "trial",
                                   "managed_by": "key-provisioner"},
                      "max_budget": 1733.0, "rpm_limit": 35, "tpm_limit": 122500}
    secret_file = tmp_path / "secrets/llmops/keys/req-3f2a9c41-trial.json"
    assert result.secret_ref == f"file:{secret_file}"
    stored = json.loads(secret_file.read_text())
    token = fake_litellm.by_alias("req-3f2a9c41-trial")["token"]
    assert stored["api_key"] == fake_litellm.issued[token] and stored["base_url"] == "https://llm.example"
    assert oct(secret_file.stat().st_mode & 0o777) == "0o600"
    assert result.expires_at and result.expires_at.endswith("Z")
    assert result.fetch_hint.startswith("（本機開發）cat ")


def test_second_run_reuses_the_stored_key(make_provisioner, fake_litellm):
    p = make_provisioner()
    first = p.provision(_req())
    again = p.provision(_req())
    assert again.reused and again.key_alias == first.key_alias and again.secret_ref == first.secret_ref
    assert sum(1 for _, path, _ in fake_litellm.calls if path == "/key/generate") == 1
    assert len(fake_litellm.keys) == 1


def test_key_that_was_never_stored_is_replaced(make_provisioner, fake_litellm, tmp_path):
    p = make_provisioner()
    p.provision(_req())
    old = fake_litellm.by_alias("req-3f2a9c41-trial")["token"]
    for f in (tmp_path / "secrets/llmops/keys").iterdir():   # simulate a crash before the store write
        f.unlink()
    result = p.provision(_req())
    assert not result.reused and len(fake_litellm.keys) == 1
    assert old not in fake_litellm.keys and fake_litellm.by_alias("req-3f2a9c41-trial")["token"] != old


def test_alias_taken_by_another_request_uses_long_alias(make_provisioner, fake_litellm):
    admin = LiteLLMAdmin("http://litellm.test", fake_litellm.master_key, transport=fake_litellm.transport())
    admin.generate({"key_alias": "req-3f2a9c41-trial", "models": ["x"], "metadata": {"request_id": "someone-else"}})
    result = make_provisioner().provision(_req())
    assert result.key_alias == f"req-{REQUEST_ID.replace('-', '')}-trial"
    assert candidate_aliases("req", REQUEST_ID, "full")[0] == "req-3f2a9c41-full"


def test_models_default_and_empty(make_provisioner, fake_litellm):
    with pytest.raises(ProvisionError) as exc:
        make_provisioner().provision(_req(models=[], unsure=True))
    assert not exc.value.transient and not fake_litellm.keys
    result = make_provisioner(default_models=("portal-quick",)).provision(_req(models=[], unsure=True))
    assert result.models == ("portal-quick",)


def test_store_failure_removes_the_new_key(make_provisioner, fake_litellm):
    class BrokenSink:
        name = "broken"

        def describe(self, path):
            return None

        def put(self, path, secret, meta):
            raise SinkError("vault unreachable: ConnectError")

        def fetch_hint(self, ref, path):
            return ""

    with pytest.raises(SinkError):
        make_provisioner(sink=BrokenSink()).provision(_req())
    assert fake_litellm.keys == {}


def test_payload_validation():
    with pytest.raises(ValueError):
        Approved.parse(approved_payload(stage="beta"))
    with pytest.raises(ValueError):
        Approved.parse(approved_payload(duration="14x"))
    with pytest.raises(ValueError):
        Approved.parse(approved_payload(rpm=-1))
    full = Approved.parse(approved_payload(stage="full", duration=None, budget_duration="30d"))
    assert full.duration is None and full.budget_duration == "30d"


def test_settings_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LITELLM_URL", "http://llm-gateway:4000/")
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    (tmp_path / "mk").write_text("sk-from-file\n")
    monkeypatch.setenv("LITELLM_MASTER_KEY_FILE", str(tmp_path / "mk"))
    monkeypatch.setenv("SECRET_SINK", "vault")
    s = Settings.from_env()
    assert s.litellm_url == "http://llm-gateway:4000" and s.secret_sink == "vault"
    monkeypatch.setenv("SECRET_SINK", "s3")
    with pytest.raises(ConfigError):
        Settings.from_env()
    with pytest.raises(ConfigError):
        parse_alias_map('{"a": []}')


# -- the worker handler (needs the database: events are published) ----------------------------------------------
def _job(data, attempts=1, max_attempts=5, event="request.approved"):
    return Job(id=7, queue="key-provisioner", payload={"event": event, "event_id": 42, "payload": data},
               attempts=attempts, max_attempts=max_attempts)


def _events(db):
    with db.connection() as c:
        return c.execute("SELECT type, payload, dedupe_key FROM platform.events ORDER BY id").fetchall()


def test_handle_publishes_key_provisioned_without_the_key(db, make_provisioner, fake_litellm, capsys):
    from llmops_common.log import setup_logging

    setup_logging("key-provisioner", "DEBUG")
    p = make_provisioner()
    p.handle(_job(approved_payload()))
    p.handle(_job(approved_payload()))                 # redelivered job: no second key, no second event
    evs = _events(db)
    assert [e["type"] for e in evs] == ["key.provisioned"]
    payload = evs[0]["payload"]
    assert payload["key_alias"] == "req-3f2a9c41-trial" and payload["stage"] == "trial"
    assert payload["requester_email"] == "amy@example.com" and payload["secret_ref"].startswith("file:")
    with db.connection() as c:
        queues = [r["queue"] for r in c.execute("SELECT queue FROM platform.jobs ORDER BY queue").fetchall()]
    assert queues == ["notifier", "request-api"]
    raw_key = next(iter(fake_litellm.issued.values()))
    logged = capsys.readouterr().out
    assert raw_key not in logged and raw_key not in json.dumps(payload) and "key provisioned" in logged
    logging.getLogger().handlers.clear()


def test_handle_retries_then_reports_failure(db, make_provisioner, fake_litellm):
    p = make_provisioner()
    fake_litellm.fail.append(("/key/list", 503, {"error": {"message": "db down"}}))
    with pytest.raises(RetryLater) as exc:
        p.handle(_job(approved_payload(), attempts=1))
    assert exc.value.delay == 30.0 and _events(db) == []
    fake_litellm.fail.append(("/key/list", 503, {"error": {"message": "db down"}}))
    with pytest.raises(PermanentError):
        p.handle(_job(approved_payload(), attempts=5, max_attempts=5))
    evs = _events(db)
    assert [e["type"] for e in evs] == ["key.failed"]
    assert "HTTP 503" in evs[0]["payload"]["error"] and evs[0]["payload"]["stage"] == "trial"


def test_handle_permanent_errors_report_at_once(db, make_provisioner, fake_litellm):
    p = make_provisioner()
    fake_litellm.fail.append(("/key/generate", 400, {"error": {"message": "Invalid model name"}}))
    with pytest.raises(PermanentError):
        p.handle(_job(approved_payload(), attempts=1))
    with pytest.raises(PermanentError):
        p.handle(_job(approved_payload(stage="beta"), attempts=1))
    with pytest.raises(PermanentError):
        p.handle(_job({"team": "x"}))
    assert [e["type"] for e in _events(db)] == ["key.failed", "key.failed"]
    p.handle(_job({"request_id": "x"}, event="request.submitted"))      # other events: acknowledged
    assert len(_events(db)) == 2


def test_admin_error_classification(fake_litellm):
    admin = LiteLLMAdmin("http://litellm.test", "sk-wrong", transport=fake_litellm.transport())
    with pytest.raises(AdminError) as exc:
        admin.find_key("req-x-trial")
    assert exc.value.status == 401 and exc.value.transient
    admin = LiteLLMAdmin("http://litellm.test", lambda: fake_litellm.master_key, transport=fake_litellm.transport())
    admin.generate({"key_alias": "a", "models": ["m"]})
    with pytest.raises(AdminError) as exc:
        admin.generate({"key_alias": "a", "models": ["m"]})
    assert exc.value.alias_taken and exc.value.transient
    assert admin.delete_key("0" * 64) is False

    def down(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(AdminError) as exc:
        LiteLLMAdmin("http://litellm.test", "k", transport=httpx.MockTransport(down)).find_key("x")
    assert exc.value.transient and "ConnectError" in str(exc.value)


def test_entrypoint_config_errors(monkeypatch, capsys):
    from key_provisioner.__main__ import main

    monkeypatch.setenv("LITELLM_URL", "http://litellm.test")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-master-test")
    monkeypatch.setenv("SECRET_SINK", "s3")
    assert main(["check-config"]) == 2
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["msg"] == "configuration error" and "SECRET_SINK" in line["error"] and "sk-master" not in line["error"]
    assert main(["bogus"]) == 2
    logging.getLogger().handlers.clear()
