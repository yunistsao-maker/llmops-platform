import datetime as dt
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from assistant_api.app import create_app
from assistant_api.content import ContentIndex
from assistant_api.prompts import sanitize_form, taiwan_note
from llmops_common.auth import Authenticator
from llmops_common.llm import GatewayClient
from llmops_common.storage import MemoryStore

B = "llmops-curated"
V1 = "20261031T060000Z-aaaaaaaaaaaa"
V2 = "20261031T070000Z-bbbbbbbbbbbb"


class Gateway:
    """httpx.MockTransport handler that records requests and replays queued responses."""

    def __init__(self):
        self.requests, self.replies = [], []

    def reply(self, content="", status=200, finish="stop", error=None):
        if status >= 400:
            self.replies.append(httpx.Response(status, json={"error": error or {"message": "x"}}))
        else:
            self.replies.append(httpx.Response(200, json={"model": "claude-haiku-5-5", "usage": {"total_tokens": 10},
                                                          "choices": [{"message": {"content": content}, "finish_reason": finish}]}))

    def __call__(self, request: httpx.Request):
        self.requests.append(json.loads(request.content))
        if isinstance(self.replies[0], Exception):
            raise self.replies.pop(0)
        return self.replies.pop(0)


@pytest.fixture()
def setup(view, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "5")
    monkeypatch.setenv("MAX_BODY_BYTES", "20000")
    store = MemoryStore()
    store.put_json(B, f"portal/views/{V1}/portal.json", view)
    store.put_json(B, "portal/views/current.json", {"current": V1})
    gw = Gateway()
    client_gw = GatewayClient("http://gw", "sk-test", "portal-quick", transport=httpx.MockTransport(gw), max_retries=0)
    content = ContentIndex(store, B, poll_seconds=3600)
    app = create_app(content=content, authenticator=Authenticator(mode="dev"), gateway=client_gw,
                     today=lambda: dt.date(2026, 10, 31), start_polling=False)
    with TestClient(app) as client:
        yield client, gw, store, content


def test_chat_answer_links_and_prompt(setup):
    client, gw, _, _ = setup
    gw.reply("可以，用 global 跨區。詳見 [Sonnet 5.5](#m-claude-sonnet-5-5)、[資安](#m-claude-sonnet-5-5/security) 與 [假頁](#m-nope)，[外部](https://x.example)。")
    r = client.post("/api/chat", json={"question": "Sonnet 5.5 在台北能用嗎？", "lang": "zh",
                                       "history": [{"role": "assistant", "content": "orphan"},
                                                   {"role": "user", "content": "之前的問題"},
                                                   {"role": "assistant", "content": "之前的回答"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"] == "可以，用 global 跨區。詳見 [Sonnet 5.5](#m-claude-sonnet-5-5)、[資安](#m-claude-sonnet-5-5/security) 與 假頁，外部。"
    assert body["links"][0] == {"route": "#m-claude-sonnet-5-5", "title": "Claude Sonnet 5.5"} and not body["truncated"]
    sent = gw.requests[0]
    assert sent["model"] == "portal-quick" and sent["max_tokens"] == 1024 and "temperature" not in sent
    roles = [m["role"] for m in sent["messages"]]
    assert roles == ["system", "user", "assistant", "user"]  # leading assistant turn dropped
    system, last = sent["messages"][0]["content"], sent["messages"][-1]["content"]
    assert "今天是 2026-10-31；portal 資料日期是 2026-10-24" in system and "現在介面語言：繁體中文" in system
    assert "#m-<模型 id>/rollout 模型的導入細節" in system and "6. 問到模型什麼時候可以申請或上線" in system
    assert last.startswith("Sonnet 5.5 在台北能用嗎？\n\nportal 資料（只用這些回答）：\n- [Claude Sonnet 5.5](#m-claude-sonnet-5-5)：")


def test_chat_english_and_truncation(setup):
    client, gw, _, _ = setup
    gw.reply("Partial answer", finish="length")
    body = client.post("/api/chat", json={"question": "What is the cheapest model?", "lang": "en"}).json()
    assert body["truncated"] and body["answer"] == "Partial answer"
    assert "現在介面語言：English" in gw.requests[0]["messages"][0]["content"]
    assert all(x["title"] for x in body["links"])


@pytest.mark.parametrize("status,error,code,expect", [
    (429, {"message": "slow"}, "rate_limited", 429),
    (503, {"message": "down"}, "gateway_unavailable", 503),
    (401, {"message": "bad key"}, "gateway_auth", 502),
    (500, {"message": "boom"}, "gateway_error", 502),
    (400, {"message": "blocked", "code": "content_policy_violation"}, "refused", 422),
])
def test_chat_gateway_errors(setup, status, error, code, expect):
    client, gw, _, _ = setup
    gw.reply(status=status, error=error)
    r = client.post("/api/chat", json={"question": "LiteLLM 1.107.0 怎麼了？"})
    assert r.status_code == expect and r.json()["error"]["code"] == code
    assert r.json()["links"]  # related pages are still offered
    if expect in (429, 503):
        assert int(r.headers["retry-after"]) > 0


def test_chat_transport_failure_and_content_filter(setup):
    client, gw, _, _ = setup
    gw.replies.append(httpx.ConnectError("refused"))
    r = client.post("/api/chat", json={"question": "退役"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "gateway_unavailable"
    gw.reply("", finish="content_filter")
    assert client.post("/api/chat", json={"question": "退役"}).json()["error"]["code"] == "refused"


def test_chat_limits(setup):
    client, gw, _, _ = setup
    assert client.post("/api/chat", json={"question": "  "}).json()["error"]["code"] == "invalid_request"
    r = client.post("/api/chat", json={"question": "長" * 1001})
    assert r.status_code == 422 and r.json()["error"]["code"] == "question_too_long"
    r = client.post("/api/chat", content=b"x" * 20001, headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_large"
    r = client.post("/api/chat", json={"question": "hi", "history": [{"role": "system", "content": "x"}]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    for _ in range(5):
        gw.reply("ok")
    codes = [client.post("/api/chat", json={"question": "price"}).status_code for _ in range(6)]
    assert codes[-1] == 429 and codes.count(200) <= 5  # 5 per minute per user (empty question etc. counted earlier)


def test_form_helper(setup):
    client, gw, _, _ = setup
    suggestion = {"reply": "建議 Haiku 5.5，便宜又快。", "questions": ["誰會用？", "資料多敏感？", "第三個"],
                  "form": {"models": ["claude-haiku-5-5", "not-a-model", "claude-haiku-5-5", "gpt-6-luna"], "use": "客服機器人",
                           "dataClass": "other", "dataClassOther": "匿名摘要", "cloud": "aws", "rpd": "30000", "tin": 2000.4,
                           "tout": -5, "external": True, "taiwan": None, "date": "2026-02-30", "note": " "}}
    gw.reply("```json\n" + json.dumps(suggestion, ensure_ascii=False) + "\n```")
    form = {"team": "數位通路部", "use_case": "客服", "models": ["claude-sonnet-5-5", "BAD ID"], "data_class": "sec-c",
            "cloud": "aws", "rpd": 5000, "tokens_in": "3000", "external": True, "unexpected": "ignored"}
    r = client.post("/api/assist/form", json={"messages": [{"role": "user", "content": "給客戶用的客服機器人，一天 3 萬次，只能用 AWS"}],
                                              "form": form, "lang": "zh"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply"] == "建議 Haiku 5.5，便宜又快。" and body["questions"] == ["誰會用？", "資料多敏感？"]
    assert body["form"] == {"models": ["claude-haiku-5-5", "gpt-6-luna"], "use_case": "客服機器人", "data_class": "other",
                            "data_class_other": "匿名摘要", "cloud": "aws", "rpd": 30000, "tokens_in": 2000, "external": True}
    sent = gw.requests[0]
    assert sent["response_format"] == {"type": "json_object"}
    system = sent["messages"][0]["content"]
    assert ("- claude-opus-5-5｜Claude Opus 5.5｜旗艦｜能力分數 58｜每百萬 tokens $4 / $20｜尚未開放，預計 2026 年 11 月下旬可以申請"
            "（依導入關卡估算；下一關：法遵審查）｜AWS 已上架（台北可經跨區 global 呼叫）") in system
    assert '"已選模型":["claude-sonnet-5-5"]' in system and '"每次輸入tokens":3000' in system
    assert "目錄裡沒有任何模型在台灣境內處理（最接近的是 Bedrock 的日本區：" in system and "今天是 2026-10-31" in system


def test_form_helper_bad_replies(setup):
    client, gw, _, _ = setup
    gw.reply("not json")
    gw.reply("still not json")
    r = client.post("/api/assist/form", json={"messages": [{"role": "user", "content": "幫我選"}], "form": {}})
    assert r.status_code == 502 and r.json()["error"]["code"] == "invalid_reply"
    gw.reply(json.dumps({"reply": "", "form": {"models": ["nope"]}}))
    assert client.post("/api/assist/form", json={"messages": [{"role": "user", "content": "幫我選"}]}).status_code == 502
    r = client.post("/api/assist/form", json={"messages": [{"role": "assistant", "content": "x"}]})
    assert r.status_code == 422


def test_index_follows_pointer(setup, view):
    client, gw, store, content = setup
    assert content.index.view_id == V1
    smaller = dict(view, models=[m for m in view["models"] if m["id"] == "gpt-6-luna"])
    store.put_json(B, f"portal/views/{V2}/portal.json", smaller)
    store.put_json(B, "portal/views/current.json", {"current": V2})
    assert content.refresh() and content.index.view_id == V2 and not content.refresh()
    store.put_json(B, "portal/views/current.json", {"current": "../../oops"})
    content.safe_refresh()
    assert content.index.view_id == V2  # a bad pointer never replaces a good index


def test_no_content_and_auth(view, monkeypatch):
    store = MemoryStore()
    gw = GatewayClient("http://gw", None, "portal-quick", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    app = create_app(content=ContentIndex(store, B), authenticator=Authenticator(mode="dev"), gateway=gw, start_polling=False)
    with TestClient(app) as client:
        r = client.post("/api/chat", json={"question": "hi"})
        assert r.status_code == 503 and r.json()["error"]["code"] == "no_content"
        assert client.get("/readyz").status_code == 200
    auth = Authenticator(mode="oidc", issuer="https://idp.example", audience="aud", jwks_url="http://127.0.0.1:9/jwks")
    app = create_app(content=ContentIndex(store, B), authenticator=auth, gateway=gw, start_polling=False)
    with TestClient(app) as client:
        assert client.post("/api/chat", json={"question": "hi"}).status_code == 401


def test_sanitize_and_taiwan_note(view):
    ids = {m["id"] for m in view["models"]}
    assert sanitize_form(None, ids) == {} and sanitize_form({"rpd": True, "tin": "abc", "tout": 1e12}, ids) == {}
    assert sanitize_form({"date": "2026-12-15", "cloud": "oci", "dataClass": "secret"}, ids) == {"target_date": "2026-12-15"}
    note = taiwan_note(view)
    assert note.startswith("目錄裡沒有任何模型在台灣境內處理（最接近的是 Bedrock 的日本區：") and "Claude Opus 5.5" in note
