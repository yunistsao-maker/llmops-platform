"""The model goes through the platform's LLM gateway (OpenAI-compatible): LLM_GATEWAY_URL, LLM_GATEWAY_KEY, LLM_MODEL.

The gateway is httpx.MockTransport speaking the chat-completions streaming format; Strands' OpenAI provider, the agent's
event loop, tools and validator are real.
"""

from __future__ import annotations

import asyncio

import pytest

from helpers_platform import MockGateway, text_turn, tool_call_turn
from model.load import gateway_client_args, gateway_model, load_model, openai_base_url
from research.agent import run_once
from research.config import DEFAULT_MODEL_ALIAS, Settings
from research.prompts import SYSTEM_PROMPT
from research.web.search_backend import FakeSearchBackend


def test_the_gateway_settings_come_from_the_platform_variables(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("sk-from-a-mounted-secret\n", encoding="utf-8")
    st = Settings.from_env({"LLM_GATEWAY_URL": "http://llm-gateway:4000", "LLM_GATEWAY_KEY_FILE": str(key_file), "LLM_TIMEOUT": "900",
                            "MAX_TOKENS": "16000", "MODEL_EXTRA_FIELDS": '{"reasoning_effort": "high"}'})
    assert st.model_provider == "gateway" and st.model_id == DEFAULT_MODEL_ALIAS == "agent-research"
    assert st.gateway_url == "http://llm-gateway:4000" and st.gateway_key == "sk-from-a-mounted-secret"
    assert st.llm_timeout == 900 and st.max_tokens == 16000 and st.model_extra_fields == {"reasoning_effort": "high"}
    assert Settings.from_env({"LLM_MODEL": "research-b", "LLM_GATEWAY_KEY": "sk-direct"}).model_id == "research-b"
    assert Settings.from_env({"LLM_GATEWAY_KEY": "sk-direct", "LLM_GATEWAY_KEY_FILE": str(key_file)}).gateway_key == "sk-direct"
    assert "sk-from" not in str(st.public()) and st.public()["gateway_key"] == "***"  # what may be logged


@pytest.mark.parametrize("given, expect", [
    ("http://llm-gateway:4000", "http://llm-gateway:4000/v1"),
    ("http://llm-gateway:4000/", "http://llm-gateway:4000/v1"),
    ("https://gw.example/v1", "https://gw.example/v1"),
    ("https://gw.example/v1/", "https://gw.example/v1"),
])
def test_the_openai_base_url_is_the_gateway_s_v1(given, expect):
    assert openai_base_url(given) == expect


@pytest.mark.parametrize("bad", ["", "  ", "llm-gateway:4000", "ftp://x"])
def test_a_missing_or_odd_gateway_url_is_a_configuration_error(bad):
    with pytest.raises(ValueError, match="LLM_GATEWAY_URL"):
        openai_base_url(bad)


def test_load_model_builds_the_openai_compatible_provider():
    from strands.models.openai import OpenAIModel

    st = Settings.from_env({"LLM_GATEWAY_URL": "http://llm-gateway:4000", "LLM_GATEWAY_KEY": "sk-virtual", "MODEL_EXTRA_FIELDS": '{"a": 1}'})
    model = load_model(st)
    assert isinstance(model, OpenAIModel)
    assert model.get_config()["model_id"] == "agent-research"
    assert model.get_config()["params"] == {"max_tokens": 32000, "extra_body": {"a": 1}}  # no temperature, no top_p
    args = model.client_args
    assert args["base_url"] == "http://llm-gateway:4000/v1" and args["api_key"] == "sk-virtual"
    assert args["timeout"] == 600 and args["max_retries"] == 3
    assert gateway_client_args(Settings(gateway_url="http://g:4000"))["api_key"] == "no-key"  # a gateway without keys (dev)
    with pytest.raises(ValueError, match="MODEL_PROVIDER"):
        load_model(Settings(model_provider="bedrock"))


def test_a_research_run_through_the_gateway(monkeypatch):
    proposal = {"summary": "經 gateway 的測試：沒有變更。", "ops": [], "checked": [{"topic": "portal snapshot", "result": "ok"}]}
    gw = MockGateway([tool_call_turn("lookup_portal", {"file": "summary"}, "call_1"),
                      tool_call_turn("submit_proposal", {"proposal": proposal}, "call_2"),
                      text_turn("done")])
    st = Settings(model_provider="gateway", gateway_url="http://llm-gateway:4000", gateway_key="sk-test-virtual-key",
                  model_extra_fields={"reasoning_effort": "medium"})
    model = gateway_model(st, client=gw.client())
    res = asyncio.run(run_once({"kind": "focus", "role": "litellm-researcher"}, settings=st, model=model, search=FakeSearchBackend({})))
    assert res["status"] == "accepted", res.get("error")
    assert res["proposal"]["summary"] == proposal["summary"]
    assert res["usage"].get("totalTokens", 0) >= 240  # the gateway's usage chunks are counted against MAX_TOTAL_TOKENS

    assert len(gw.requests) >= 2
    for request, body in zip(gw.requests, gw.bodies):
        assert str(request.url) == "http://llm-gateway:4000/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer sk-test-virtual-key"
        assert body["model"] == "agent-research" and body["stream"] is True and body["max_tokens"] == 32000
        assert body["reasoning_effort"] == "medium"  # MODEL_EXTRA_FIELDS travel in the request body
        assert "temperature" not in body and "top_p" not in body and "tool_choice" not in body  # Claude on the gateway: 400
        assert body["messages"][0]["role"] == "system" and SYSTEM_PROMPT[:60] in str(body["messages"][0]["content"])
        names = {t["function"]["name"] for t in body["tools"]}
        assert {"web_search", "fetch_page", "record_evidence", "lookup_portal", "submit_proposal"} <= names
    # the second call carries the first tool's result back to the model
    assert any(m.get("role") == "tool" and m.get("tool_call_id") == "call_1" for m in gw.bodies[1]["messages"])


def test_a_gateway_error_ends_the_run_as_an_error_result():
    import httpx

    gw = MockGateway([lambda body: httpx.Response(401, json={"error": {"message": "invalid virtual key", "type": "auth_error"}})])
    st = Settings(model_provider="gateway", gateway_url="http://llm-gateway:4000", gateway_key="sk-wrong")
    res = asyncio.run(run_once({"kind": "focus", "role": "litellm-researcher"}, settings=st, model=gateway_model(st, client=gw.client()),
                               search=FakeSearchBackend({})))
    assert res["status"] == "error" and "401" in res["error"]
