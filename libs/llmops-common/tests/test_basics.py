import json
import logging

import httpx
import pytest

from llmops_common import config, gates, ids, log
from llmops_common.llm import GatewayClient, GatewayError, extract_json
from llmops_common.storage import MemoryStore, NotFound, bucket


def test_env_file_and_defaults(tmp_path, monkeypatch):
    secret = tmp_path / "pw"
    secret.write_text("s3cret\n")
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    monkeypatch.setenv("DB_PASSWORD_FILE", str(secret))
    assert config.env("DB_PASSWORD") == "s3cret"
    assert config.env("NOPE_X", "d") == "d"
    with pytest.raises(config.ConfigError):
        config.env("NOPE_Y")
    monkeypatch.setenv("FLAG", "yes")
    assert config.env_bool("FLAG") is True
    monkeypatch.setenv("FLAG", "maybe")
    with pytest.raises(config.ConfigError):
        config.env_bool("FLAG")
    monkeypatch.setenv("LIST", " a, b ,,c ")
    assert config.env_list("LIST") == ("a", "b", "c")


def test_json_logging(capsys):
    log.setup_logging("unit", "INFO")
    log.get_logger("t").info("hello", extra=log.kv(job_id=7, msg="ignored-reserved"))
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["component"] == "unit" and line["msg"] == "hello" and line["job_id"] == 7
    assert log.redact("Authorization: Bearer abcdefghijklmnop") == "Authorization: Bearer ***"
    logging.getLogger().handlers.clear()


def test_memory_store_roundtrip():
    s = MemoryStore()
    s.put_json("b", "portal/manifest.json", {"current": "r1", "名": "中文"})
    assert s.get_json("b", "portal/manifest.json")["名"] == "中文"
    s.put_jsonl("b", "runs/x.jsonl", [{"a": 1}, {"a": 2}])
    assert [r["a"] for r in s.get_jsonl("b", "runs/x.jsonl")] == [1, 2]
    assert [o.key for o in s.list("b", "portal/")] == ["portal/manifest.json"]
    assert s.get_json("b", "missing.json", None) is None
    with pytest.raises(NotFound):
        s.get_bytes("b", "missing")
    assert bucket("curated") == "llmops-curated"


def test_gates():
    assert gates.required_gates({"data_class": "sec-c", "rpd": 100}) == ["G0", "G9"]
    assert gates.required_gates({"data_class": "sec-b"}) == ["G0", "G2", "G9"]
    assert gates.required_gates({"data_class": "property", "external": True}) == ["G0", "G2", "G8", "G9"]
    assert gates.required_gates({"data_class": "sec-c", "rpd": "10000"}) == ["G0", "G8", "G9"]
    assert gates.next_gate(["G0", "G2", "G8", "G9"], {"G2"}) == "G8"
    assert gates.next_gate(["G0", "G9"], set()) is None


def test_ids():
    rid = ids.new_run_id()
    assert len(rid) == 22 and rid[8] == "T"
    assert ids.new_release_id().endswith("Z")


def _client(handler) -> GatewayClient:
    return GatewayClient("http://gw", "sk-test", "portal-quick", transport=httpx.MockTransport(handler), max_retries=2)


def test_gateway_chat_retry_and_json(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request: httpx.Request):
        calls["n"] += 1
        assert request.headers["authorization"] == "Bearer sk-test"
        body = json.loads(request.content)
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        if body.get("response_format") and calls["n"] == 2:
            return httpx.Response(200, json={"model": "claude-haiku-5-5", "choices": [{"message": {"content": "Sure! ```json\n{\"ok\": true}\n```"}, "finish_reason": "stop"}]})
        return httpx.Response(200, json={"model": "m", "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]})

    c = _client(handler)
    assert c.chat_json([{"role": "user", "content": "x"}]) == {"ok": True}
    assert calls["n"] == 2
    r = c.chat([{"role": "user", "content": "x"}])
    assert r.text == "hi" and r.model == "m"


def test_gateway_error_not_retried():
    def handler(request):
        return httpx.Response(400, json={"error": {"message": "bad model", "code": "invalid_model"}})

    with pytest.raises(GatewayError) as exc:
        _client(handler).chat([{"role": "user", "content": "x"}])
    assert exc.value.status == 400 and exc.value.code == "invalid_model"


def test_extract_json():
    assert extract_json('prefix {"a": [1, 2]} suffix') == {"a": [1, 2]}
