"""What publishes without a person, what waits and what is refused - the rules that came out of the third review.

Automatic publishing is for what a program can hold against a quote: something new (a record, a note, a citation) and
a date, a number or a version that changed.  Everything else about a record that exists - what kind of thing it is,
what it is called, how it is labelled, the words somebody wrote, the order of a list - is replaced only by a person.
The publisher decides this from the difference between the live data and the replayed result, never from how an
operation is spelled.

The proposals here are the fixture run (a real run record) with other operations.
"""

from __future__ import annotations

import json

import pytest
from conftest import PROPOSAL_ID, T1, T2, accepted_run, snapshot_tree
from test_publish import EVENT, edit, event_date, live, model, op, run_with

from publisher import publish as P
from publisher import releases as R
from research.portal import checks as C

LC, CAT = "data/lifecycle.json", "data/catalog.json"
OTHER = "bedrock-claude-3-haiku-eol"
M = "claude-sonnet-5-5"
REVIEW = P.Policy(mode="review")


def publish(plane, record, **kw) -> P.Outcome:
    return P.publish_proposal(plane.data, record, control=plane.control, now=kw.pop("now", T1), **kw)


def doc(plane, rel: str) -> dict:
    return json.loads(live(plane).files[rel])


def event(plane, event_id: str = EVENT) -> dict:
    return next(e for e in doc(plane, LC)["events"] if e["id"] == event_id)


# ---------------------------------------------------------------------------------------------- what goes live by itself
@pytest.mark.parametrize("build, read", [
    (lambda: op("replace", LC, f"/events/{EVENT}/date", "2026-11-30"), lambda p: event(p)["date"] == "2026-11-30"),
    (lambda: op("add", LC, f"/events/{EVENT}/notes/-", "Foundry 表現在列 2026-11-30。"), lambda p: event(p)["notes"][-1] == "Foundry 表現在列 2026-11-30。"),
    (lambda: op("add", LC, f"/events/{OTHER}/sources/-", {"id": "anthropic-deprecations"}), lambda p: len(event(p, OTHER)["sources"]) == 2),
    (lambda: op("replace", CAT, f"/models/{M}/contextWindow", 2_000_000), lambda p: doc(p, CAT)["models"][0]["contextWindow"] == 2_000_000),
    (lambda: op("replace", CAT, f"/models/{M}/litellm/recommendedVersion", "v1.103.0"), lambda p: doc(p, CAT)["models"][0]["litellm"]["recommendedVersion"] == "v1.103.0"),
    (lambda: op("replace", CAT, f"/models/{M}/offerings/{M}@anthropic/pricing/standard/input", 2.5),
     lambda p: doc(p, CAT)["models"][0]["offerings"][0]["pricing"][0]["input"] == 2.5),
    (lambda: op("add", CAT, f"/models/{M}/litellm/minVersionParams", "v1.101.0"), lambda p: doc(p, CAT)["models"][0]["litellm"]["minVersionParams"] == "v1.101.0"),
    # a release as LiteLLM tags it: the tag is part of the version, not a remark behind one
    (lambda: op("replace", CAT, f"/models/{M}/litellm/recommendedVersion", "v1.103.2-stable"),
     lambda p: doc(p, CAT)["models"][0]["litellm"]["recommendedVersion"] == "v1.103.2-stable"),
    (lambda: op("replace", CAT, f"/models/{M}/litellm/recommendedVersion", "v1.104.0-rc.1"),
     lambda p: doc(p, CAT)["models"][0]["litellm"]["recommendedVersion"] == "v1.104.0-rc.1"),
], ids=["a date", "an appended note", "an added citation", "a number", "a version", "a price", "a version field that was not there",
        "a version tagged -stable", "a release candidate"])
def test_what_is_new_and_dates_numbers_versions_that_changed_go_live(plane, build, read):
    plane.bootstrap()
    outcome = publish(plane, run_with(build()))
    assert outcome.status == "published", outcome.reasons
    assert read(plane)


# ---------------------------------------------------------------------------------------------- what waits
@pytest.mark.parametrize("build, expect", [
    # a category: nothing in a quote says "deprecation" the way a date says a date
    (lambda: op("replace", LC, f"/events/{EVENT}/event", "deprecation"), f"提案會修改既有資料的狀態、分類或識別欄位（1 處；這類值沒有引文可以逐字對照）：{LC}/events/{EVENT}/event"),
    (lambda: op("replace", CAT, f"/models/{M}/reasoning/alwaysOn", False), f"狀態、分類或識別欄位（1 處；這類值沒有引文可以逐字對照）：{CAT}/models/{M}/reasoning/alwaysOn"),
    # which offering of which provider the event is about
    (lambda: op("replace", LC, f"/events/{EVENT}/providerModelId", "claude-sonnet-4-6"), f"狀態、分類或識別欄位（1 處；這類值沒有引文可以逐字對照）：{LC}/events/{EVENT}/providerModelId"),
    # words somebody wrote, replaced by other words (an appended note goes live; see above)
    (lambda: op("replace", LC, f"/events/{OTHER}/scopeNote", "所有區域"), f"提案會改寫 portal 上原有的文字（1 處；新增的文字會直接發布，改寫的需要人看）：{LC}/events/{OTHER}/scopeNote"),
    (lambda: op("replace", LC, f"/events/{EVENT}/notes/0", "改寫過的說明。"), f"改寫 portal 上原有的文字（1 處；新增的文字會直接發布，改寫的需要人看）：{LC}/events/{EVENT}/notes/0"),
    (lambda: op("replace", LC, f"/events/{EVENT}/replacement", "claude-opus-5-5"), f"改寫 portal 上原有的文字（1 處；新增的文字會直接發布，改寫的需要人看）：{LC}/events/{EVENT}/replacement"),
    # "" and " " are deletions that are not spelled `remove`
    (lambda: op("replace", LC, f"/events/{OTHER}/scopeNote", ""), f"提案會把原有的文字清空（1 處），等同刪除：{LC}/events/{OTHER}/scopeNote"),
    (lambda: op("replace", LC, f"/events/{OTHER}/scopeNote", " 　"), f"提案會把原有的文字清空（1 處），等同刪除：{LC}/events/{OTHER}/scopeNote"),
    # a label, in either direction
    (lambda: op("replace", LC, f"/events/{EVENT}/verification", "community"), f"提案會改變資料的可信度標籤（verification）：{LC}/events/{EVENT}/verification（official → community）"),
    # the same records in another order: the page shows the first price sheet
    (lambda: op("replace", CAT, f"/models/{M}/offerings", list(reversed(model()["offerings"]))), f"提案會改變清單裡紀錄的順序（頁面顯示的是第一筆，例如第一張價格表）：{CAT}/models/{M}/offerings"),
    # one of two citations taken away, or swapped for another
    (lambda: op("replace", LC, f"/events/{EVENT}/sources", [{"id": "ms-foundry-retirement-schedule"}]), f"提案會拿掉資料原有的部分引用來源：{LC}/events/{EVENT}/sources"),
    (lambda: op("replace", LC, f"/events/{EVENT}/sources", [{"id": "ms-foundry-retirement-schedule"}, {"id": "aws-bedrock-lifecycle-legacy"}]),
     f"提案會拿掉資料原有的部分引用來源：{LC}/events/{EVENT}/sources"),
    # A date is compared with a quote - where a date stood.  Written over a paragraph, a name, an identifier or a
    # category it is that text, name or category replaced (the fourth review published every one of these in auto mode).
    (lambda: op("replace", CAT, f"/models/{M}/summary", "2026-10-02"), f"改寫 portal 上原有的文字（1 處；新增的文字會直接發布，改寫的需要人看）：{CAT}/models/{M}/summary"),
    (lambda: op("replace", LC, f"/events/{EVENT}/notes/0", "2026-10-02"), f"改寫 portal 上原有的文字（1 處；新增的文字會直接發布，改寫的需要人看）：{LC}/events/{EVENT}/notes/0"),
    (lambda: op("replace", LC, f"/events/{EVENT}/modelName", "2026-10-02"), f"狀態、分類或識別欄位（1 處；這類值沒有引文可以逐字對照）：{LC}/events/{EVENT}/modelName"),
    (lambda: op("replace", CAT, f"/models/{M}/offerings/{M}@anthropic/providerModelId", "2026-10-02"),
     f"狀態、分類或識別欄位（1 處；這類值沒有引文可以逐字對照）：{CAT}/models/{M}/offerings/{M}@anthropic/providerModelId"),
    (lambda: op("replace", CAT, f"/models/{M}/reasoning/defaultEffort", "2026-10-02"), f"狀態、分類或識別欄位（1 處；這類值沒有引文可以逐字對照）：{CAT}/models/{M}/reasoning/defaultEffort"),
    # ... and a version field holds a version: a sentence that mentions one is not "the version changed"
    (lambda: op("replace", CAT, f"/models/{M}/litellm/recommendedVersion", "不要用 v1.102.1；請改裝 0.0.1"),
     f"狀態、分類或識別欄位（1 處；這類值沒有引文可以逐字對照）：{CAT}/models/{M}/litellm/recommendedVersion"),
    # ... also when the field was not there before: what is added to it is a version only when it is nothing but versions
    (lambda: op("add", CAT, f"/models/{M}/litellm/minVersionParams", "之後補上（見 v1.102.1 的說明）"),
     f"狀態、分類或識別欄位（1 處；這類值沒有引文可以逐字對照）：{CAT}/models/{M}/litellm/minVersionParams"),
    # Something new put FIRST: no record changed places, and the page shows another first note, another first price sheet
    (lambda: op("add", LC, f"/events/{EVENT}/notes/0", "新的說明。"),
     f"提案把新的項目放在清單裡既有項目的前面（頁面顯示的是第一筆；新增的內容加在清單最後才會自動發布）：{LC}/events/{EVENT}/notes"),
    (lambda: op("add", CAT, f"/models/{M}/offerings/{M}@anthropic/pricing/0", dict(model()["offerings"][0]["pricing"][0], scope="batch")),
     f"提案把新的項目放在清單裡既有項目的前面（頁面顯示的是第一筆；新增的內容加在清單最後才會自動發布）：{CAT}/models/{M}/offerings/{M}@anthropic/pricing"),
], ids=["a category", "a boolean", "an identifier", "a text replaced", "a note replaced", "the replacement", "emptied", "blanked", "relabelled", "reordered",
        "a citation dropped", "a citation swapped", "a summary -> a date", "a note -> a date", "a name -> a date", "an identifier -> a date",
        "a category -> a date", "a version -> a sentence", "a sentence in a new version field", "a note put first", "a price sheet put first"])
def test_what_no_quote_can_be_held_against_waits_for_a_person(plane, build, expect):
    base = plane.bootstrap()
    record = run_with(build())
    outcome = publish(plane, record)
    assert outcome.status == "held" and any(expect in r for r in outcome.reasons), outcome.reasons
    assert live(plane).release_id == base.release_id
    # ... and a person can let it through: approval publishes exactly this
    approved = P.approve(plane.data, plane.control, PROPOSAL_ID, actor="eunice", now=T2)
    assert approved.status == "published", approved.reasons


def test_a_list_takes_no_second_record_with_the_key_of_one_it_holds(plane):
    """A page looks a record up by its key and shows the first it finds.  A "new" price sheet for the scope that has
    one, a second `tools` feature, a second release v1.103.2: nothing that exists was changed, so nothing held it - and
    the page showed the newcomer.  Refused, also for a person who approves: the record that is there is the one to change."""
    base = plane.bootstrap()
    offering = f"/models/{M}/offerings/{M}@anthropic"
    sheet, feature = model()["offerings"][0]["pricing"][0], model()["offerings"][0]["features"][0]
    release = json.loads(snapshot_tree()["data/litellm.json"])["releases"][0]
    for build, key in (
        (lambda: op("add", CAT, f"{offering}/pricing/-", dict(sheet, input=0.01)), f"scope = '{sheet['scope']}'"),
        (lambda: op("add", CAT, f"{offering}/pricing/0", dict(sheet, input=0.01)), f"scope = '{sheet['scope']}'"),
        (lambda: op("add", CAT, f"{offering}/features/-", dict(feature, note="其實不支援。")), f"feature = '{feature['feature']}'"),
        (lambda: op("add", "data/litellm.json", "/releases/0", dict(release, channel="rc")), f"version = '{release['version']}'"),
        (lambda: op("add", LC, "/events/-", dict(json.loads(snapshot_tree()[LC])["events"][0], date="2031-01-01")), "id = "),
    ):
        for policy in (None, REVIEW):
            outcome = publish(plane, run_with(build()), policy=policy)
            assert outcome.status == "rejected" and any("this list already has a record with " + key in r for r in outcome.reasons), (key, outcome.reasons)
    assert live(plane).release_id == base.release_id and P.waiting(plane.data) == []
    # another scope at the end of the list is a new record like any other
    assert publish(plane, run_with(op("add", CAT, f"{offering}/pricing/-", dict(sheet, scope="batch")))).status == "published"


def test_an_order_that_changed_is_counted_and_shown_not_folded_into_nothing(plane):
    """Two records swapped is no added, removed or modified value - the difference used to be empty, and empty publishes."""
    plane.bootstrap()
    outcome = publish(plane, run_with(op("replace", CAT, f"/models/{M}/offerings", list(reversed(model()["offerings"])))))
    counts = outcome.review.to_doc()["counts"]
    assert counts["reordered"] == 1 and counts["modified"] == counts["added"] == counts["removed"] == 0, counts
    change = next(c for c in outcome.review.to_doc()["changes"] if c["kind"] == "reordered")
    assert change["before"][0] == f"{M}@anthropic" and change["after"][-1] == f"{M}@anthropic", change
    assert [o["id"] for o in doc(plane, CAT)["models"][0]["offerings"]][0] == f"{M}@anthropic"


def test_the_holds_of_this_kind_are_read_from_the_difference_not_from_the_operation(plane):
    """One coarse replace of a whole record carries a category change and a blanked text inside it."""
    plane.bootstrap()
    record_before = next(e for e in json.loads(snapshot_tree()[LC])["events"] if e["id"] == OTHER)
    whole = dict(record_before, event="deprecation", scopeNote="", date="2026-09-12")
    outcome = publish(plane, run_with(op("replace", LC, f"/events/{OTHER}", whole)))
    assert outcome.status == "held"
    reasons = "\n".join(outcome.reasons)
    assert f"狀態、分類或識別欄位（1 處；這類值沒有引文可以逐字對照）：{LC}/events/{OTHER}/event" in reasons
    assert f"把原有的文字清空（1 處），等同刪除：{LC}/events/{OTHER}/scopeNote" in reasons
    assert "/date" not in reasons  # the date by itself would have gone live


# ---------------------------------------------------------------------------------------------- what is not a proposal the agent wrote
def dated(record: dict, **fields) -> dict:
    record["proposal"].update(fields)
    return record


def with_evidence(record: dict, key: str = "e1", **fields) -> dict:
    entry = record["proposal"]["evidence"].setdefault(key, dict(record["proposal"]["evidence"]["e1"]))
    entry.update(fields)
    return record


DATE = lambda **extra: op("replace", LC, f"/events/{EVENT}/date", "2026-11-30", **extra)  # noqa: E731 - an operation that would publish as it is


@pytest.mark.parametrize("build, expect", [
    # digits that are not ASCII digits: \d matched them, datetime did not, and the rules about time were skipped
    (lambda: dated(run_with(DATE()), createdAt="２０３１-01-01T00:00:00Z"), "提案的格式不正確：createdAt must be a timestamp such as"),
    (lambda: dated(run_with(DATE()), today="２０２６-10-02"), "提案的格式不正確：today must be a date such as"),
    (lambda: dated(run_with(DATE()), today="2026-13-45"), "提案的格式不正確：today must be a date such as"),
    (lambda: dated(run_with(DATE()), createdAt="2026-10-02T03:00:00Z\n"), "提案的格式不正確：createdAt must be a timestamp such as"),
    # text that is printed (notification, portalctl show) must be text
    (lambda: run_with(DATE(rationale=["請到", "https://evil.example/approve"])), "提案的格式不正確：ops[0].rationale must be text"),
    # the quotes: stored with the release and shown to whoever approves
    (lambda: with_evidence(run_with(DATE()), url="http://learn.microsoft.com/x"), "提案的引文紀錄（evidence）格式不正確（1 則，例如 e1）"),
    (lambda: with_evidence(run_with(DATE()), url="javascript:alert(1)"), "提案的引文紀錄（evidence）格式不正確（1 則，例如 e1）"),
    (lambda: with_evidence(run_with(DATE()), quote=" \n"), "提案的引文紀錄（evidence）格式不正確（1 則，例如 e1）"),
    (lambda: with_evidence(run_with(DATE()), tier="gold"), "提案的引文紀錄（evidence）格式不正確（1 則，例如 e1）"),
    (lambda: with_evidence(run_with(DATE()), title=["a", "list"]), "提案的引文紀錄（evidence）格式不正確（1 則，例如 e1）"),
    (lambda: with_evidence(run_with(DATE()), html="<script>"), "提案的引文紀錄（evidence）格式不正確（1 則，例如 e1）"),
    (lambda: with_evidence(run_with(DATE()), rendered="yes"), "提案的引文紀錄（evidence）格式不正確（1 則，例如 e1）"),
    (lambda: with_evidence(run_with(DATE()), key="e 2\x1b[2K"), "提案的引文紀錄（evidence）格式不正確（1 則，例如 e 2"),
    (lambda: (lambda r: (r["proposal"]["evidence"].update(e2="just text"), r)[1])(run_with(DATE())), "提案的引文紀錄（evidence）格式不正確（1 則，例如 e2）"),
    # 61 empty quote records used to count as "quoted from 61 pages"
    (lambda: (lambda r: (r["proposal"]["evidence"].update({f"x{i}": {} for i in range(61)}), r)[1])(run_with(DATE())), "提案的引文紀錄（evidence）格式不正確（61 則，例如 x0、x1、x2）"),
    # every operation rests on a quote that is in the proposal
    (lambda: run_with(DATE(evidence=[])), f"提案的 operation 沒有引用任何引文：第 1 個（{LC}/events/{EVENT}/date）"),
    (lambda: run_with({k: v for k, v in DATE().items() if k != "evidence"}), "提案的 operation 沒有引用任何引文：第 1 個"),
    (lambda: run_with(DATE(evidence="e1")), "提案的 operation 沒有引用任何引文：第 1 個"),
    (lambda: run_with(DATE(), DATE(evidence=["e9"])), "提案的 operation 引用了不存在的引文編號：第 2 個"),
    # what is added has no earlier value: a `before` there is only something to show a reviewer
    (lambda: run_with(op("add", LC, f"/events/{EVENT}/notes/-", "此模型已停用。", before="AWS 表列 EOL 2026-09-12。")),
     f"提案的 add operation 帶有 before（新增的內容沒有原值）：第 1 個（{LC}/events/{EVENT}/notes/-）"),
    # the release the proposal says it was made from is printed and stored
    (lambda: (lambda r: (r["proposal"]["portal"]["manifest"].update(releaseId="20261002T010000Z-1e6fcc45\x1b[2K 已核准"), r)[1])(run_with(DATE())),
     "提案記錄的資料版本（portal.manifest）格式不正確"),
    (lambda: (lambda r: (r["proposal"]["portal"]["manifest"].update(kind="release\n核准指令："), r)[1])(run_with(DATE())), "提案記錄的資料版本（portal.manifest）格式不正確"),
    (lambda: (lambda r: (r["proposal"]["portal"].update(manifest="20261002T010000Z-1e6fcc45"), r)[1])(run_with(DATE())), "提案記錄的資料版本（portal.manifest）格式不正確"),
    # larger than this, a proposal is not a research result
    (lambda: run_with(op("add", LC, f"/events/{OTHER}/notes/-", "補充說明。" * 700_000)), "提案超過 2000000 bytes"),
    # ... and neither is one that holds more values than all of the portal (0.1 MB of JSON is enough for that)
    (lambda: run_with(op("replace", LC, f"/events/{EVENT}/notes", [0] * 30_001)), "提案裡的值（文字、數字、陣列、物件，全部算在內）超過 30,000 個"),
    (lambda: (lambda r: (r["proposal"].update(stats={"x": [[]] * 30_001}), r)[1])(run_with(DATE())), "超過 30,000 個"),
])
def test_a_record_the_agent_would_not_have_written_is_refused(plane, build, expect):
    base = plane.bootstrap()
    for policy in (None, REVIEW):  # approval would not make it one: it does not wait either
        outcome = publish(plane, build(), policy=policy)
        assert outcome.status == "rejected" and any(expect in r for r in outcome.reasons), outcome.reasons
        assert all("\x1b" not in r and "\n" not in r for r in outcome.reasons)
    assert live(plane).release_id == base.release_id and P.waiting(plane.data) == []


@pytest.mark.parametrize("build, status, expect", [
    # one mistake, an error for every element: listed up to a point, then counted
    (lambda: [0] * 29_000, "rejected", "… and 28,800 more schema errors that are not listed one by one"),
    (lambda: [{"n": i} for i in range(14_000)], "rejected", "more schema errors that are not listed one by one"),
    # nothing wrong with it but its size: a difference for every element
    (lambda: [f"補充說明第 {i:05d} 則：這一則和其他每一則都不一樣。" for i in range(29_000)], "held", "超過自動發布的上限"),
])
def test_the_largest_proposal_that_is_worked_through_fits_the_function_it_runs_in(folder_plane, build, status, expect):
    """Refusing a list of 330,000 numbers took 1.3 GB (a difference, a schema error and a line of the refusal for every
    one of them) in a function that has 1 GB: the execution ended red, without the refusal and its reason.  What is
    worked through is bounded now; this is the most a record inside the bounds takes, with a margin of five - against
    the publisher's suggested memory limit (README: 1 GiB)."""
    import tracemalloc

    function = {"MemorySize": 1024}  # MiB: the worker's suggested memory limit
    plane = folder_plane
    plane.bootstrap()
    record = run_with(op("replace", LC, f"/events/{EVENT}/notes", build()))
    assert len(json.dumps(record["proposal"], ensure_ascii=False)) < P.MAX_PROPOSAL_BYTES
    tracemalloc.start()
    try:
        outcome = publish(plane, record)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert outcome.status == status and any(expect in r for r in outcome.reasons), outcome.reasons[:3] + outcome.reasons[-1:]
    assert len(outcome.reasons) <= C.MAX_SCHEMA_PROBLEMS + 5
    assert peak * 5 < function["MemorySize"] * 1024 * 1024, f"{peak / 1e6:.0f} MB"


@pytest.mark.parametrize("value", [10 ** 400, 2 ** 53 + 1, -(2 ** 53) - 1])
def test_an_integer_no_browser_reads_exactly_is_refused(plane, value):
    """Python stores it, JSON.parse turns it into another number (or Infinity): the page would show something nobody wrote."""
    base = plane.bootstrap()
    outcome = publish(plane, run_with(op("replace", CAT, f"/models/{M}/contextWindow", value)))
    assert outcome.status == "rejected" and any("is larger than any number a browser reads exactly" in r for r in outcome.reasons), outcome.reasons
    assert live(plane).release_id == base.release_id
    assert publish(plane, run_with(op("replace", CAT, f"/models/{M}/contextWindow", 2 ** 53 - 1))).status == "published"


def test_a_file_that_would_outgrow_what_a_push_accepts_is_refused(plane, monkeypatch):
    """A push refuses files above releases.MAX_FILE_BYTES; a proposal that adds to a file is held to the same size."""
    base = plane.bootstrap()
    size = len(live(plane).files[LC])
    monkeypatch.setattr(R, "MAX_FILE_BYTES", size + 100)
    small = publish(plane, run_with(op("add", LC, f"/events/{OTHER}/notes/-", "短。")))
    assert small.status == "published", small.reasons
    big = publish(plane, run_with(op("add", LC, f"/events/{OTHER}/notes/-", "這一段比較長。" * 40)))
    assert big.status == "rejected" and any(LC in r and "bytes" in r for r in big.reasons), big.reasons
    assert live(plane).release_id == small.release_id != base.release_id


# ---------------------------------------------------------------------------------------------- the team's own tests
def test_the_teams_own_test_results_are_not_researchs_to_write(plane):
    """litellm.status tested-* and a benchmark measured internally say "we ran this": only the team can say so."""
    base = plane.bootstrap()
    tested = publish(plane, run_with(op("replace", CAT, f"/models/{M}/litellm/status", "tested-pass")))
    assert tested.status == "rejected" and any("litellm.status" in r and "tested-pass" in r for r in tested.reasons), tested.reasons
    bench = {"name": "內部壓力測試（gateway）", "score": 99.2, "unit": "percent", "measuredBy": "internal", "date": "2026-10-02", "sources": [{"id": "anthropic-sonnet55-overview"}]}
    measured = publish(plane, run_with(op("add", CAT, f"/models/{M}/benchmarks/-", bench)))
    assert measured.status == "rejected" and any("measuredBy" in r and "internal" in r for r in measured.reasons), measured.reasons
    assert live(plane).release_id == base.release_id
    # what research found out about the gateway is still research's to write
    untested = publish(plane, run_with(op("replace", CAT, f"/models/{M}/litellm/status", "untested")))
    assert untested.status == "held" and not any("protected" in r for r in untested.reasons), untested.reasons   # (a status: a person looks, like any other)


def test_a_result_the_team_recorded_cannot_be_taken_back_or_moved_to_another_offering(plane):
    def mark(d):
        d["models"][0]["litellm"]["status"] = "tested-pass"
        d["models"][0]["offerings"][1]["quotas"] = [{"scope": "default", "rpm": 4000, "verification": "internal-test", "sources": []}]

    tree = edit(snapshot_tree(), CAT, mark)
    base = plane.bootstrap(tree)
    bedrock = json.loads(tree[CAT])["models"][0]["offerings"][1]
    where = f"/models/{M}/offerings/{bedrock['id']}"

    def change(path, value, before):
        return {"file": CAT, "op": "replace", "path": path, "value": value, "before": before, "evidence": ["e1"], "rationale": "x"}

    for record in (
        run_with(change(f"/models/{M}/litellm/status", "researched", "tested-pass")),                      # the test result withdrawn
        run_with(change(where + "/quotas/default/rpm", 9000, 4000)),                                         # the measurement itself
        run_with(change(where + "/cloud", "gcp", "aws")),                                                    # ... or what it was measured on
        run_with(change(where + "/service", "agent-platform", "bedrock")),
        run_with(change(where + "/providerModelId", "anthropic.claude-opus-5-5", bedrock["providerModelId"])),
    ):
        outcome = publish(plane, record)
        assert outcome.status == "rejected" and any("protected field" in r for r in outcome.reasons), (record["proposal"]["ops"][0]["path"], outcome.reasons)
    assert live(plane).release_id == base.release_id
    # an offering without a measurement of the team is an ordinary record: its identity waits for a person, it is not refused
    other = json.loads(tree[CAT])["models"][0]["offerings"][0]
    plain = publish(plane, run_with(change(f"/models/{M}/offerings/{other['id']}/service", "agent-platform", other["service"])))
    assert plain.status == "held", plain.reasons


# ---------------------------------------------------------------------------------------------- the waiting list
def test_a_waiting_proposal_survives_another_one_with_its_id(plane):
    """The automatic publish of B used to delete pending/<id> - which held A, a different proposal somebody was looking at."""
    plane.bootstrap()
    a = run_with(op("remove", LC, f"/events/{OTHER}/scopeNote"))
    b = run_with(op("add", LC, f"/events/{EVENT}/notes/-", "補充。"))
    assert a["proposal"]["id"] == b["proposal"]["id"] and P.proposal_sha256(a["proposal"]) != P.proposal_sha256(b["proposal"])
    assert publish(plane, a).status == "held"
    second = publish(plane, b)
    assert second.status == "published", second.reasons
    waiting = P.waiting(plane.data)
    assert [w["proposalSha256"] for w in waiting] == [P.proposal_sha256(a["proposal"])]
    assert P.approve(plane.data, plane.control, PROPOSAL_ID, actor="eunice", expected_sha=P.proposal_sha256(a["proposal"]), now=T2).status == "published"
    assert "scopeNote" not in event(plane, OTHER) and event(plane)["notes"][-1] == "補充。"


def test_a_proposal_that_was_discarded_can_be_submitted_and_approved_again(plane):
    """The decision on record ("discarded") used to answer every later approval of the same content: it could never go live."""
    plane.bootstrap()
    record = accepted_run()
    assert publish(plane, record, policy=REVIEW).status == "held"
    assert P.discard(plane.data, PROPOSAL_ID, actor="amy", reason="日期要再確認", now=T1, control=plane.control).status == "discarded"
    assert publish(plane, record, policy=REVIEW, now=T2).status == "held"                     # the research result is submitted again ...
    approved = P.approve(plane.data, plane.control, PROPOSAL_ID, actor="eunice", policy=REVIEW, now=T2)
    assert approved.status == "published" and event_date(live(plane).files) == "2026-11-30", approved.reasons   # ... and can be approved
    assert P.waiting(plane.data) == []


def test_a_proposal_that_goes_live_another_way_leaves_the_waiting_list(plane):
    """On AWS an approval could die after the release and before tidying up, leaving a live proposal on the waiting
    list.  The list is part of the pointer now: the write that makes the content live takes it off the list."""
    plane.bootstrap()
    record = accepted_run()
    assert publish(plane, record, policy=REVIEW).status == "held"
    released = P.publish_proposal(plane.data, record, control=None, now=T1)
    assert released.status == "published" and P.waiting(plane.data) == []
    with pytest.raises(R.ReleaseError, match="沒有待核准的提案"):  # nothing waits: nothing to discard ("discarded" would be untrue)
        P.discard(plane.data, PROPOSAL_ID, actor="amy", now=T2, control=plane.control)
    assert event_date(live(plane).files) == "2026-11-30"


def test_after_a_rollback_the_proposal_is_no_longer_live_and_may_wait_and_be_discarded(plane):
    base = plane.bootstrap()
    record = accepted_run()
    publish(plane, record, policy=REVIEW)
    P.publish_proposal(plane.data, record, control=None, now=T1)
    assert P.rollback(plane.data, actor="bob", now=T2).release_id == base.release_id
    assert publish(plane, record, policy=REVIEW, now=T2).status == "held"
    assert P.discard(plane.data, PROPOSAL_ID, actor="amy", now=T2, control=plane.control).status == "discarded"
    assert P.waiting(plane.data) == []


# ---------------------------------------------------------------------------------------------- one request, delivered twice
def test_the_same_request_delivered_twice_gets_its_first_answer(plane):
    """Step Functions retries a step whose answer was lost.  The second delivery of a publish that happened must say
    "published" - with the release, the difference and the rollback command - and not "nothing to do"."""
    plane.bootstrap()
    record = accepted_run()
    first = publish(plane, record, request_id="execution-1")
    again = publish(plane, record, request_id="execution-1", now=T2)
    assert first.status == again.status == "published" and again.release_id == first.release_id and again.sequence == first.sequence == 2
    # (the difference is read back from the release's own record: the data it was computed from has moved on)
    assert again.to_state()["review"]["counts"] == first.to_state()["review"]["counts"] and again.to_state()["review"]["changes"] == first.to_state()["review"]["changes"]
    assert again.previous_release_id == first.previous_release_id and again.operations and again.proposal["id"] == PROPOSAL_ID
    assert live(plane).manifest["sequence"] == 2 and len(R.list_release_ids(plane.data)) == 2          # nothing was published twice
    # somebody else's request for the same proposal is told that it is live already
    assert publish(plane, record, request_id="execution-2", now=T2).status == "already_published"
    assert publish(plane, record, now=T2).status == "already_published"


def test_an_approval_repeated_after_it_published_finds_nothing_waiting(plane):
    plane.bootstrap()
    record = accepted_run()
    held = publish(plane, record, policy=REVIEW)
    approved = P.approve(plane.data, plane.control, PROPOSAL_ID, actor="eunice", policy=REVIEW, now=T2)
    assert approved.status == "published"
    with pytest.raises(R.ReleaseError, match="沒有待核准的提案"):
        P.approve(plane.data, plane.control, PROPOSAL_ID, actor="eunice", policy=REVIEW, now=T2)
    with pytest.raises(R.ReleaseError, match="not a held release waiting"):
        P.approve(plane.data, plane.control, held.held_release_id, actor="eunice", policy=REVIEW, now=T2)
    assert live(plane).manifest["sequence"] == 2


# ---------------------------------------------------------------------------------------------- rollback and pause
def test_a_release_that_was_never_live_is_not_a_rollback_target(plane, monkeypatch):
    """A publish that loses the race for the pointer leaves a complete release behind - and its proposal on the waiting
    list.  `rollback --to` that id used to make it live: an unapproved proposal published by the undo command."""
    base = plane.bootstrap()
    real, calls = R.switch_pointer, []

    def racing(store, manifest, etag):
        calls.append(1)
        if len(calls) == 1:  # somebody pauses publishing just before this publisher switches the pointer
            monkeypatch.setattr(R, "switch_pointer", real)
            P.pause(plane.data, actor="amy", reason="先暫停", now=T1)
            monkeypatch.setattr(R, "switch_pointer", racing)
        return real(store, manifest, etag)

    monkeypatch.setattr(R, "switch_pointer", racing)
    outcome = publish(plane, accepted_run())
    monkeypatch.setattr(R, "switch_pointer", real)
    left = [r for r in R.list_release_ids(plane.data) if r not in (base.release_id, outcome.held_release_id)]
    assert outcome.status == "held" and len(left) == 1 and live(plane).release_id == base.release_id
    for never in (left[0], outcome.held_release_id):  # the release that lost the race, and the held one: neither was live
        back = P.rollback(plane.data, never, actor="bob", now=T2)
        assert back.status == "rejected" and any(f"release {never} 不在發布紀錄裡" in r and "--unlisted" in r for r in back.reasons), back.reasons
    assert live(plane).release_id == base.release_id and event_date(live(plane).files) == "2026-11-15"
    assert not R.was_live(live(plane).manifest, left[0]) and R.was_live(live(plane).manifest, base.release_id)
    # the operator who knows what that release is can still go there, by saying so
    forced = P.rollback(plane.data, left[0], actor="bob", now=T2, allow_unlisted=True)
    assert forced.status == "rolled_back" and live(plane).release_id == left[0]


def test_a_reason_is_stored_as_one_clean_line(plane):
    """`portalctl status` and `verify` print the pause reason: it is cleaned when it is written, not only when it is shown."""
    plane.bootstrap()
    dirty = "維護中\x1b[2K\x1b[1A\x1b]0;owned\x07 ‮gnp.exe\n第二行"
    assert P.pause(plane.data, actor="amy\x1b[2K", reason=dirty, now=T1).ok
    raw = plane.data.get("manifest.json").data.decode("utf-8")
    hold = P.get_hold(plane.data)
    for text in (raw, hold["reason"], hold["by"]):
        assert not any(c in text for c in ("\x1b", "\x07", "‮")) and "\\u001b" not in text and "\\u0007" not in text and "\\u202e" not in text
    assert "\n" not in hold["reason"] and hold["reason"].startswith("維護中") and "第二行" in hold["reason"]
    assert P.rollback(plane.data, actor="bob", reason=dirty, now=T2).status == "rejected"  # (nothing earlier to go back to; the reason is not echoed raw)
    P.resume(plane.data, actor="amy", now=T2)
    publish(plane, accepted_run(), now=T2)
    back = P.rollback(plane.data, actor="bob\x07", reason=dirty, now=T2)
    assert back.status == "rolled_back"
    raw = plane.data.get("manifest.json").data.decode("utf-8")
    assert not any(c in raw for c in ("\x1b", "\x07", "‮")) and "\\u001b" not in raw and "\\u0007" not in raw and "\\u202e" not in raw


# ---------------------------------------------------------------------------------------------- pushes from a checkout
@pytest.mark.parametrize("missing", list(P.CORE_FILES))
def test_no_switch_publishes_a_release_without_the_files_every_page_starts_from(plane, folder_plane, missing):
    """A release without the catalog (or the schema ...) is valid as far as the validators can tell - there is nothing in
    it to object to - and shows every visitor an error page."""
    tree = {k: v for k, v in snapshot_tree().items() if k != missing}
    first = P.publish_tree(folder_plane.data, tree, bootstrap=True, actor="eunice", now=T1)
    assert first.status == "rejected" and any(f"缺少 UI 必要的檔案：{missing}" in r for r in first.reasons) and not folder_plane.data.exists("manifest.json")
    base = plane.bootstrap()
    for switches in ({}, {"force": True}, {"allow_invalid": True}, {"force": True, "allow_invalid": True, "delete": True}):
        outcome = P.publish_tree(plane.data, tree, base_release=base.release_id, actor="eunice", now=T1, **switches)
        assert outcome.status == "rejected" and any(f"缺少 UI 必要的檔案：{missing}" in r for r in outcome.reasons), (switches, outcome.reasons)
    assert live(plane).release_id == base.release_id and set(P.CORE_FILES) <= set(live(plane).files)


def test_what_was_not_read_from_a_checkout_is_not_deleted_by_it():
    """A file the push skips (not one the UI reads; a link) is in the checkout, unread.  Merged as "absent", a checkout
    that knows its base would delete it from the release."""
    current = {"data/x.json": b"1", "runs/r1.json": b"1", "runs/r2.json": b"2", "config/notes.md": b"n", "runs-old/x.json": b"o"}
    tree = {"data/x.json": b"2", "runs-old/x.json": b"o"}  # runs/ is a link here, notes.md is skipped
    files, conflicts = P.merge_checkout(current, tree, base=current, keep=["runs/", "config/notes.md"])
    assert files == {**current, "data/x.json": b"2"} and conflicts == []
    # only what was left out is kept: a deletion is still a deletion, also next to a kept folder with a similar name
    del tree["runs-old/x.json"]
    files, _ = P.merge_checkout(current, tree, base=current, keep=["runs/", "config/notes.md"])
    assert "runs-old/x.json" not in files and files["runs/r1.json"] == b"1"
    files, _ = P.merge_checkout(current, tree, base=current)
    assert files == {"data/x.json": b"2"}
    # the same without a base, when --delete asks for removals
    files, _ = P.merge_checkout(current, tree, delete=True, keep=["runs"])
    assert sorted(files) == ["data/x.json", "runs/r1.json", "runs/r2.json"]
