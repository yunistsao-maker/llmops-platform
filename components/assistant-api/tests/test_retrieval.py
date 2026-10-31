import pytest

from assistant_api.retrieval import Index, filter_links, toks


@pytest.fixture(scope="module")
def index(view):
    return Index(view, "v1")


def test_index_matches_prototype(index, golden):
    assert len(index.docs) == golden["docs"] and len(index.idf) == golden["idfSize"]


def test_search_matches_prototype(index, golden):
    for case in golden["results"]:
        hits = index.search(case["q"], 8)
        assert [[h.route, h.tz, h.te] for h in hits] == case["hits"], case["q"]
        assert index.context(hits).split("\n", 1)[-1] == (case["ctx"] or "（沒有找到相關資料）"), case["q"]


def test_tokens():
    assert toks("GPT-6.1 Sol 在台北") == ["gpt-6.1", "gpt", "6", "1", "sol", "在台", "台北"]
    assert toks("退") == ["退"] and toks(None) == []


def test_month_intent_prefers_dated_docs(index):
    hits = index.search("11 月有哪些模型要退役？", 8)
    assert hits and all(h.route == "#retire" for h in hits[:3]) and hits[0].date[5:7] == "11"


def test_links_and_routes(index):
    hits = index.search("Sonnet 5.5 在台北能用嗎？", 8)
    links = index.links(hits, "en")
    assert links[0] == {"route": "#m-claude-sonnet-5-5", "title": "Claude Sonnet 5.5"}
    assert len(links) <= 4 and len({x["route"] for x in links}) == len(links)
    assert index.links(index.search("申請", 8), "en")[0]["title"] == "Request a model"
    assert index.route_allowed("#sec-SEC-32") and not index.route_allowed("#sec-SEC-33")
    assert index.route_allowed("#m-claude-opus-5-5/security") and index.route_allowed("#m-claude-opus-5-5/rollout")
    assert not index.route_allowed("#m-claude-opus-5-5/hack") and not index.route_allowed("#m-nope/security")
    assert not index.route_allowed("#cost-claude-opus-5-5/security")
    assert index.route_allowed("#apply-claude-opus-5-5") and not index.route_allowed("#m-not-a-model")
    assert not index.route_allowed("#news-it-ffffffffff") and index.route_allowed("#news-todo")
    assert not index.route_allowed("javascript:alert(1)") and not index.route_allowed("#evil")


def test_filter_links(index):
    text = ("看 [Sonnet 5.5](#m-claude-sonnet-5-5)、[假的](#m-fake-model)、[外部](https://evil.example/x) "
            "與 [壞路由](#../../x) 以及 [退役日曆](#retire)")
    assert filter_links(text, index) == "看 [Sonnet 5.5](#m-claude-sonnet-5-5)、假的、外部 與 壞路由 以及 [退役日曆](#retire)"
    # a route with characters outside the link pattern is never turned into a link by the page either
    assert filter_links("[x](javascript:alert(1))", index) == "x)"


def test_availability_in_model_docs(index):
    hits = index.search("Opus 5.5 什麼時候可以申請？", 8)
    assert hits[0].route == "#m-claude-opus-5-5"
    assert "申請狀態：尚未開放，預計 2026 年 11 月下旬可以申請（依導入關卡估算；下一關：法遵審查）" in hits[0].snip
    roll = next(d for d in index.docs if d.route == "#m-claude-sonnet-5-5/rollout")
    assert roll.snip.startswith("Claude Sonnet 5.5 導入進度：申請狀態：暫停導入（安全測試沒有通過），還沒有預計時間。")
    sec = next(d for d in index.docs if d.route == "#m-claude-haiku-5-5/security")
    assert "公司 gateway 名稱 claude-haiku-5-5" in sec.snip and "台灣境內處理：沒有" in sec.snip
    links = index.links(index.search("GPT-6 Luna 導入進度", 8), "en")
    assert {"route": "#m-gpt-6-luna/rollout", "title": "GPT-6 Luna rollout details"} in links
