"""The digest is the portal's front page, and it is all prose.

What a program holds it to: it has entries, every entry cites a page the run read, and a date or an amount in it is one
the run can point at - for the model the sentence says it of.  A sentence of the digest is read the way a sentence of a
page is: "X retires on <date> (Y on the same day)" says the date of X, and naming Y lends nothing.  (Round 4: a
headline and a P0 action item with a date, no page fetched and no quote recorded, went live as the newest digest.)
"""

import pytest

from helpers import add_quote, make_session, proposal
from research.proposal import validate_proposal
from test_grounding_rules import H3, LC, ROW_H3, ROW_H35, aws, digest, errors, html, op, page, said

NEW = "digest/lifecycleChanges/0"
CARD = "aws-bedrock-sonnet55-card"
SAID_OF_ANOTHER = "AWS 宣布 Claude Sonnet 5.5 將於 2026-10-20 自 Bedrock 下架（gemini-2.5-pro 同日進入 legacy）。"


@pytest.fixture
def s():
    return make_session()


def flags(rep):
    return rep.proposal["validation"]["flags"] if rep.proposal else None


def card(s):
    return page(s, CARD, html("Claude Sonnet 5.5 - Amazon Bedrock", "Claude Sonnet 5.5 is available in Amazon Bedrock through global and geographic inference profiles."))


def entry(title, detail="", sources=(CARD,), **more):
    return {"title": title, "detail": detail, "impact": "high", "sources": [{"id": x} for x in sources], **more}


def test_a_digest_without_an_entry_rests_on_nothing_the_run_read(s):
    quiet = digest(headline="本期沒有重大變更。", actionItems=[{"priority": "P2", "action": "持續追蹤 Bedrock 生命週期頁。", "due": "2026-10-09"}])
    rep = validate_proposal(s, proposal([], digest=quiet))
    assert rep.ok and list(flags(rep)) == ["digestUnsourced"] and "it has no entry in any section" in flags(rep)["digestUnsourced"][0], rep.errors
    assert any("will wait for a person" in w for w in rep.warnings)
    # ... and a date in it is one nothing establishes, whichever model is named next to it or linked to it
    assert any(e["date"] == "2026-10-20" and e["id"] == "vertex-gemini-2-5-pro-legacy" for e in s.portal.docs[LC]["events"])
    urgent = digest(headline="緊急：AWS 宣布 Claude Sonnet 5.5 將於 2026-10-20 自 Bedrock 下架（與 gemini-2.5-pro 同日），所有路由請立即改用 GPT-6 Sol。",
                    actionItems=[{"priority": "P0", "action": "Claude Sonnet 5.5 將於 2026-10-20 下架：本週內把所有 Bedrock 路由切走。", "due": "2026-10-05",
                                  "relatedModelIds": ["claude-sonnet-5-5"]}])
    rep = validate_proposal(s, proposal([], digest=urgent))
    assert not rep.ok and "digest/headline: the date 2026-10-20 is not one that an entry of this digest establishes" in errors(rep)
    assert "digest/actionItems/0/action: the date 2026-10-20 is not one" in errors(rep)


def test_an_action_item_is_linked_to_models_of_the_catalog(s):
    """relatedModelIds took the id of any record - a lifecycle EVENT included - and whatever that record held then backed
    the dates of the action item."""
    card(s)
    item = entry("Bedrock 模型卡複查", "Claude Sonnet 5.5 的模型卡沒有變動。")
    for related, ok in ((["claude-sonnet-5-5"], True), (["vertex-gemini-2-5-pro-legacy"], False), (["claude-sonnet-5-5", "no-such-model"], False), ([7], False)):
        action = {"priority": "P1", "action": "確認路由設定。", "due": "2026-10-09", "relatedModelIds": related}
        rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[item], actionItems=[action])))
        assert rep.ok == ok, (related, rep.errors)
        if not ok:
            assert "digest/actionItems/0/relatedModelIds" in errors(rep) and "is not the id of a model in the catalog" in errors(rep)
    # a linked model lends nothing: the date of the action is still one nobody established
    action = {"priority": "P0", "action": "2026-10-20 前完成切換。", "due": "2026-10-09", "relatedModelIds": ["claude-sonnet-5-5"]}
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[item], actionItems=[action])))
    assert not rep.ok and "digest/actionItems/0/action: the date 2026-10-20" in errors(rep)


def test_naming_a_second_model_does_not_lend_its_date(s):
    card(s)
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[entry("Bedrock：Claude Sonnet 5.5 將於 2026-10-20 下架", SAID_OF_ANOTHER, date="2026-10-20")])))
    assert not rep.ok
    for where in ("title", "detail", "date"):
        assert f"{NEW}/{where}: the date 2026-10-20 is neither in a quote recorded from a page this entry cites" in errors(rep), where
    # the entry's own `date` is read under its title: a date for the model the title names
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[
        entry("Bedrock：Claude Sonnet 5.5 即將下架", "gemini-2.5-pro 於 2026-10-20 進入 legacy。", sources=(CARD, "gcp-gemini25-notice-1763"), date="2026-10-20")])))
    assert not rep.ok and errors(rep).count("the date 2026-10-20") == 1 and f"{NEW}/date" in errors(rep), rep.errors


def test_what_the_portal_has_for_the_model_the_sentence_is_about_may_be_repeated(s):
    """A reminder of a date the portal already has - for that model - needs no new quote, but it does need a page the
    run read: an entry that only repeats the portal waits for a person."""
    reminder = entry("Agent Platform：gemini-2.5-pro 於 2026-10-20 進入 legacy（提醒）", "複查無變動。", sources=("gcp-gemini25-notice-1763",), date="2026-10-20")
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[reminder])))
    assert rep.ok and list(flags(rep)) == ["digestNotReread"], rep.errors
    page(s, "gcp-gemini25-notice-1763", html("Gemini 2.5 notice", "This page was read and says nothing new."))
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[reminder], headline="提醒：gemini-2.5-pro 於 2026-10-20 進入 legacy。")))
    assert rep.ok and flags(rep) == {}, (rep.errors, flags(rep))
    # two models in front of one date: the portal has it for one of them, and a program cannot tell which is meant
    both = entry("Agent Platform 提醒", "gemini-2.5-pro 的後繼 Claude Sonnet 5.5 於 2026-10-20 生效。", sources=("gcp-gemini25-notice-1763",))
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[both])))
    assert rep.ok and list(flags(rep)) == ["uncertainSubject"] and "names more than one model" in flags(rep)["uncertainSubject"][0], (rep.errors, flags(rep))


def test_the_quote_behind_an_entry_states_the_date_for_the_model_the_entry_says_it_of(s):
    p = aws(s)
    add_quote(s, p, ROW_H35)  # Claude 3.5 Haiku | ... | 2026-12-01
    wrong = entry("Claude 3 Haiku 將於 2026-12-01 退役", sources=("aws-bedrock-lifecycle-legacy",))
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[wrong])))
    assert rep.ok and list(flags(rep)) == ["uncertainSubject"], rep.errors
    assert "the sentence says it of Claude 3 Haiku" in flags(rep)["uncertainSubject"][0] and "without naming that model" in flags(rep)["uncertainSubject"][0]
    # (the same for a model the portal has no record of: it is known by the name the entry gives it)
    unknown = entry("Claude 3 Opus 將於 2026-12-01 退役", sources=("aws-bedrock-lifecycle-legacy",))
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[unknown])))
    assert rep.ok and list(flags(rep)) == ["uncertainSubject"] and "the sentence says it of claude 3 opus" in flags(rep)["uncertainSubject"][0], (rep.errors, flags(rep))
    right = entry("Claude 3.5 Haiku 將於 2026-12-01 退役", sources=("aws-bedrock-lifecycle-legacy",))
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[right], headline="Claude 3.5 Haiku：2026-12-01 退役")))
    assert rep.ok and flags(rep) == {}, (rep.errors, flags(rep))
    # the headline sums up the entries - for the same model
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[right], headline="Claude Sonnet 5.5 將於 2026-12-01 退役")))
    assert rep.ok and list(flags(rep)) == ["uncertainSubject"] and "digest/headline" in flags(rep)["uncertainSubject"][0], (rep.errors, flags(rep))
    # ... and a headline that names nobody may give the day
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[right], headline="本期重點：一個模型將於 2026-12-01 退役")))
    assert rep.ok and flags(rep) == {}, (rep.errors, flags(rep))


def test_a_quote_about_a_model_the_entry_does_not_name_leaves_a_person_to_tell_what_is_meant(s):
    """The sentence that holds the date says it of nobody.  Then the quote says whose date it is - and when that is a
    model the entry never names, a reader of the digest cannot know what the date is the date of."""
    add_quote(s, aws(s), ROW_H3)  # Claude 3 Haiku | ... | 2025-12-01 | 2026-09-12
    src = ("aws-bedrock-lifecycle-legacy",)
    for detail in ("表列的退役日期是 2026-09-12。", "表列的退役日期是 2026-09-12。另見 Claude Sonnet 5.5 的模型卡。"):
        rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[entry("Bedrock 生命週期頁更新", detail, sources=src)])))
        assert rep.ok and list(flags(rep)) == ["uncertainSubject"], (detail, rep.errors, flags(rep))
        assert "states it for Claude 3 Haiku" in flags(rep)["uncertainSubject"][0] and "which the entry does not name" in flags(rep)["uncertainSubject"][0]
    # the entry names the model the quote is about - in another sentence, but it is what the entry is about
    named = entry("Bedrock 生命週期頁更新", "表列的退役日期是 2026-09-12。受影響的模型：Claude 3 Haiku。", sources=src)
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[named])))
    assert rep.ok and flags(rep) == {}, (rep.errors, flags(rep))


def test_an_entry_about_no_model_is_held_to_its_quote_and_to_nothing_else(s):
    """A regulator's notice, dated in the calendar of the Republic - the second date without the word 民國."""
    notice = "金融監督管理委員會於民國115年9月30日發布「金融業運用人工智慧（AI）指引」修正規定，自115年10月1日生效。"
    said(s, "https://www.fsc.gov.tw/ch/home.jsp?id=96&parentpath=0,2&mcustomize=news_view.jsp&dataserno=202609300001", "金管會新聞稿", notice)
    news = [{"id": "fsc-ai-guideline-2026", "url": "https://www.fsc.gov.tw/ch/home.jsp?id=96&parentpath=0,2&mcustomize=news_view.jsp&dataserno=202609300001"}]
    item = {"title": "金管會修正金融業 AI 指引", "date": "2026-09-30", "detail": "2026-09-30 發布修正規定，2026-10-01 生效。", "impact": "medium", "sources": [{"id": "fsc-ai-guideline-2026"}]}
    rep = validate_proposal(s, proposal([], digest=digest(regulatory=[item]), newSources=news))
    assert rep.ok and flags(rep) == {}, (rep.errors, flags(rep))
    rep = validate_proposal(s, proposal([], digest=digest(regulatory=[dict(item, detail="2026-09-30 發布，2026-11-01 生效。")]), newSources=news))
    assert not rep.ok and "digest/regulatory/0/detail: the date 2026-11-01" in errors(rep)


def test_a_day_written_short_is_a_day_all_the_same(s):
    add_quote(s, aws(s), ROW_H3)  # Claude 3 Haiku | ... | 2025-12-01 | 2026-09-12
    src = ("aws-bedrock-lifecycle-legacy",)
    # the day the quote states, without its year: backed like the full date
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[entry("Claude 3 Haiku：09-12 EOL（表列）", "Bedrock 頁面列為 9 月 12 日。", sources=src)])))
    assert rep.ok and flags(rep) == {}, (rep.errors, flags(rep))
    # "10-20": a day nothing here falls on - or a range; a person tells which
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[entry("Claude 3 Haiku：10-20 前完成遷移", sources=src)])))
    assert rep.ok and list(flags(rep)) == ["uncheckedValue"] and "Write dates in full" in flags(rep)["uncheckedValue"][0], (rep.errors, flags(rep))
    # written out, a day nothing states is refused like a date
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[entry("Claude 3 Haiku 將於 10 月 20 日退役", sources=src)])))
    assert not rep.ok and "the day 10-20 (month-day, no year)" in errors(rep)


# ---------------------------------------------------------------------------------------------- topic, check, merges
#: an entry of lifecycleChanges, and an action item, of the 2026-09-24 digest the bundled portal holds
EARLIER = "Azure o 系列統一在 2026-11-19 退役"
EARLIER_ACTION = "盤點仍呼叫 gemini-2.5-pro / flash / flash-lite 的 GCP 專案：10-20 起新專案與 07-22 之後沒有呼叫過的專案（含 DR / fallback 專案）會被封鎖；遷移期限 Flash-Lite 2027-01-28、Pro / Flash 2027-03-31。先在 GCP 帳號通知核對這些日期。"


def test_merges_name_earlier_entries_and_the_dates_in_them_are_not_news(s):
    """`merges` are the titles of earlier entries this one updates - references, not sentences of this digest.  Read as
    sentences, the date in an earlier title (checked when it went live) had to be backed again by a quote of today."""
    card(s)
    item = entry("Azure o 系列退役時程複查", "官方表未變動。", topic="lifecycle:azure-o-series", merges=[EARLIER])
    action = {"priority": "P0", "action": "盤點仍呼叫 Gemini 2.5 系列的 GCP 專案（沿用前期項目）。", "topic": "lifecycle:gemini-2.5-pro", "merges": [EARLIER_ACTION]}
    assert any(a["action"] == EARLIER_ACTION for a in s.portal.docs["data/digests/2026-09-24.json"]["actionItems"])
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[item], actionItems=[action])))
    assert rep.ok and flags(rep) == {}, (rep.errors, flags(rep))
    assert rep.proposal["digest"]["lifecycleChanges"][0]["merges"] == [EARLIER] and rep.proposal["digest"]["actionItems"][0]["merges"] == [EARLIER_ACTION]
    # matched as the portal's Radar matches titles: width, case, punctuation and white space aside - but word for word
    loose = entry("Azure o 系列退役時程複查", "官方表未變動。", merges=["ＡＺＵＲＥ o 系列統一在 2026/11/19 退役。"])
    assert validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[loose]))).ok
    # ... and the dates in the entry's own words are held to a quote as before
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[entry("Azure o 系列將於 2026-11-19 退役", merges=[EARLIER])])))
    assert not rep.ok and f"{NEW}/title: the date 2026-11-19 is neither in a quote" in errors(rep), rep.errors


@pytest.mark.parametrize("where, merges, said", [
    ("lifecycleChanges", ["Azure o 系列統一在 2026-11-20 退役"], f"{NEW}/merges/0: 'Azure o 系列統一在 2026-11-20 退役' is not the title of an entry of lifecycleChanges"),
    ("lifecycleChanges", ["Azure o系列統一在 2026-11-19 退役"], f"{NEW}/merges/0"),
    ("lifecycleChanges", [EARLIER, "Claude Opus 5.5 降價"], f"{NEW}/merges/1: 'Claude Opus 5.5 降價' is not the title of an entry of lifecycleChanges"),
    ("lifecycleChanges", ["Azure o 系列退役時程複查"], f"{NEW}/merges/0"),  # its own title: an entry of THIS digest is not an earlier one
    ("actionItems", [EARLIER], "digest/actionItems/0/merges/0: 'Azure o 系列統一在 2026-11-19 退役' is not the action text of an action item"),
])
def test_merges_that_name_no_earlier_entry_are_errors(s, where, merges, said):
    """A title that is not on the portal would be a sentence nobody checked - and the Radar would fold nothing into the entry."""
    card(s)
    item = entry("Azure o 系列退役時程複查", "官方表未變動。")
    action = {"priority": "P1", "action": "Azure o 系列遷移。"}
    if where == "actionItems":
        action["merges"] = merges
    else:
        item["merges"] = merges
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[item], actionItems=[action])))
    assert not rep.ok and said in errors(rep) and "copy each" in errors(rep), rep.errors
    if merges[0].startswith("Azure o 系列統一在 2026-11-20"):
        assert f"the closest is {EARLIER!r}" in errors(rep)


@pytest.mark.parametrize("topic, ok", [
    ("pricing:claude-sonnet-5-5", True), ("lifecycle:gemini-2.5-pro:vertex", True), ("security:cve-2026-12345", True), ("litellm:v1.102.1", True),
    ("Pricing:claude-sonnet-5-5", False), ("pricing", False), ("pricing:", False), ("a:b:c:d:e", False), ("pricing:claude sonnet", False),
    ("pricing:claude-sonnet-5-5\n", False), ("pricing:" + "x" * 100, False), ("pricing_x:y", False), (7, False),
])
def test_a_topic_names_the_subject_the_same_way_every_day(s, topic, ok):
    card(s)
    item = entry("Bedrock 模型卡複查", "Claude Sonnet 5.5 的模型卡沒有變動。", topic=topic, check=True)
    for d in (digest(lifecycleChanges=[item]), digest(lifecycleChanges=[entry("Bedrock 模型卡複查")], actionItems=[{"priority": "P2", "action": "持續追蹤。", "topic": topic}])):
        rep = validate_proposal(s, proposal([], digest=d))
        assert rep.ok == ok, (topic, rep.errors)
        if not ok:
            where = "digest/lifecycleChanges/0" if d["lifecycleChanges"][0].get("topic") is not None else "digest/actionItems/0"
            assert f"{where}/topic: " in errors(rep) and "is not a topic" in errors(rep), rep.errors


def test_a_topic_does_not_lend_its_model_to_a_date(s):
    """A topic names a model ("lifecycle:gemini-2.5-pro"), but it is not a sentence of the entry: read as one, it made the
    entry "name" the model - a date the portal has for it, in a sentence that names nobody, waited for a person instead
    of being refused, and a quote about that model backed a sentence about nothing."""
    page(s, "gcp-gemini25-notice-1763", html("Gemini 2.5 notice", "This page was read and says nothing new."))
    src = ("gcp-gemini25-notice-1763",)
    nobody = entry("Agent Platform 生命週期複查", "2026-10-20 起封鎖新專案。", sources=src, topic="lifecycle:gemini-2.5-pro")
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[nobody])))
    assert not rep.ok and f"{NEW}/detail: the date 2026-10-20 is neither in a quote" in errors(rep), (rep.errors, flags(rep))
    named = entry("Agent Platform 生命週期複查", "gemini-2.5-pro 於 2026-10-20 起封鎖新專案。", sources=src, topic="lifecycle:gemini-2.5-pro")
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[named])))
    assert rep.ok and flags(rep) == {}, (rep.errors, flags(rep))
    # the same for an action item
    action = {"priority": "P0", "action": "2026-10-20 前完成切換。", "topic": "lifecycle:gemini-2.5-pro"}
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[entry("Agent Platform 複查", "沒有變動。", sources=src)], actionItems=[action])))
    assert not rep.ok and "digest/actionItems/0/action: the date 2026-10-20 is not one that an entry of this digest establishes" in errors(rep), rep.errors
    # a quote about a model the entry does not name still leaves a person to tell what the date is the date of
    add_quote(s, aws(s), ROW_H3)  # Claude 3 Haiku | ... | 2025-12-01 | 2026-09-12
    quiet = entry("Bedrock 生命週期頁更新", "表列的退役日期是 2026-09-12。", sources=("aws-bedrock-lifecycle-legacy",), topic="lifecycle:claude-3-haiku")
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[quiet])))
    assert rep.ok and list(flags(rep)) == ["uncertainSubject"] and "which the entry does not name" in flags(rep)["uncertainSubject"][0], (rep.errors, flags(rep))


def test_a_coverage_note_cites_its_sources_like_any_entry(s):
    """`check: true` says "checked, no change" - which is something this run read, and where."""
    card(s)
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[entry("Bedrock 模型卡複查", "沒有變動。", sources=(), check=True)])))
    assert not rep.ok and f"{NEW}: cites no source" in errors(rep), rep.errors
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[entry("Bedrock 模型卡複查", "沒有變動。", check=True, topic="lifecycle:claude-sonnet-5-5")])))
    assert rep.ok and flags(rep) == {} and rep.proposal["digest"]["lifecycleChanges"][0]["check"] is True, (rep.errors, flags(rep))
