"""One research run's proposals in one batch (research.proposal.ready): replayed in role order, each on top of the
ones that go live before it; what needs a person goes into one held release built on that; what conflicts is refused."""

from __future__ import annotations

import json

from conftest import T1, T2, accepted_run
from test_publish import EVENT, event_date, live, op, run_with

from publisher import publish as P
from publisher import releases as R

LC = "data/lifecycle.json"
OTHER = "bedrock-claude-3-haiku-eol"


def proposals(plane):
    """Four results of one daily run, saved where the crawler saves them; returns [(key, record), ...] in role order."""
    first = accepted_run()                                                             # the Foundry date: goes live
    note = run_with(op("add", LC, f"/events/{EVENT}/notes/-", "補充：Foundry 表 10-02 讀取。"), id="prop-20261002T030000Z-0000000b")
    removal = run_with(op("remove", LC, f"/events/{OTHER}/scopeNote"), id="prop-20261002T030000Z-0000000c")  # waits for a person
    clash = run_with(op("replace", LC, f"/events/{EVENT}/date", "2027-01-01"), id="prop-20261002T030000Z-0000000d")  # made against the old date
    out = []
    for record in (first, note, removal, clash):
        out.append((plane.save_proposal(record["proposal"]), record["proposal"]))
    return out


def test_a_run_s_proposals_go_live_together_and_what_waits_is_held_on_top(plane):
    base = plane.bootstrap()
    items = proposals(plane)
    keys = [k for k, _ in items]
    out = P.publish_batch(plane.data, items, control=plane.control, now=T1, trigger="event:1", run={"run_id": "r1", "run_date": "2026-10-02"})
    assert [i.status for i in out.items] == ["published", "published", "held", "rejected"]
    assert out.status == "published+held"
    assert any("原值" in r or "re-run" in r for r in out.items[3].reasons), out.items[3].reasons  # it was made against the old date

    # the live release: the two that passed every gate, one after the other
    current = live(plane)
    assert current.release_id == out.published.release_id and out.published.previous_release_id == base.release_id
    doc = json.loads(current.files[LC])
    event = next(e for e in doc["events"] if e["id"] == EVENT)
    assert event["date"] == "2026-11-30" and event["notes"][-1] == "補充：Foundry 表 10-02 讀取。"
    assert next(e for e in doc["events"] if e["id"] == OTHER).get("scopeNote")  # the removal is not live
    meta = current.meta
    assert meta["held"] is False and meta["source_proposals"] == keys[:2] and meta["source"]["run_id"] == "r1"
    assert [a["status"] for a in meta["proposals"]] == ["published", "published", "held", "rejected"]  # the whole run, on record

    # the held release: what goes live with it, plus the removal - complete, and listed in the pointer for a person
    assert out.held.held is True and out.held.previous_release_id == current.release_id
    held_meta = R.read_meta(plane.data, out.held.release_id)
    assert held_meta["held"] is True and held_meta["parent"] == current.release_id and held_meta["source_proposals"] == [keys[2]]
    assert out.held.release_id > current.release_id  # (ids are moments: never the same one)
    held_doc = json.loads(R.read_release(plane.data, out.held.release_id)[1][LC])
    assert "scopeNote" not in next(e for e in held_doc["events"] if e["id"] == OTHER)
    entry = R.held_of(current.manifest)[0]
    assert (entry["release_id"], entry["base"], entry["trigger"]) == (out.held.release_id, current.release_id, "event:1")
    assert [p["id"] for p in entry["proposals"]] == ["prop-20261002T030000Z-0000000c"] and "刪除" in entry["proposals"][0]["reasons"][0]
    # the refusal is filed for people to read
    assert json.loads(plane.control.get("rejected/prop-20261002T030000Z-0000000d.json").data)["reasons"]

    # approving the held release replays the removal on what is live then
    approved = P.approve(plane.data, plane.control, out.held.release_id, actor="eunice", now=T2)
    assert approved.status == "published" and live(plane).manifest["source"]["approved_from"] == out.held.release_id
    assert "scopeNote" not in next(e for e in json.loads(live(plane).files[LC])["events"] if e["id"] == OTHER)
    assert R.held_of(live(plane).manifest) == [] and event_date(live(plane).files) == "2026-11-30"


def test_the_same_event_delivered_again_gets_the_releases_made_the_first_time(plane):
    plane.bootstrap()
    items = proposals(plane)
    first = P.publish_batch(plane.data, items, control=plane.control, now=T1, trigger="event:7")
    again = P.publish_batch(plane.data, items, control=plane.control, now=T2, trigger="event:7")
    assert again.repeated and again.published.release_id == first.published.release_id and again.held.release_id == first.held.release_id
    assert [i.status for i in again.items] == [i.status for i in first.items]
    assert again.published.catalog_changes == first.published.catalog_changes
    assert len(R.list_release_ids(plane.data)) == 3 and live(plane).manifest["sequence"] == 2  # bootstrap + live + held, once
    # another event with the same proposals is not a repeat: the live data already holds what could go live
    other = P.publish_batch(plane.data, items, control=plane.control, now=T2, trigger="event:8")
    assert not other.repeated and other.published is None and [i.status for i in other.items][:2] == ["already_published"] * 2


def test_in_review_mode_the_whole_run_waits_in_one_held_release(plane):
    base = plane.bootstrap()
    out = P.publish_batch(plane.data, proposals(plane), policy=P.Policy(mode="review"), control=plane.control, now=T1, trigger="event:2")
    assert out.published is None and out.status == "held" and live(plane).release_id == base.release_id
    assert [i.status for i in out.items] == ["held", "held", "held", "rejected"]  # the clash cannot stand next to the first
    assert "先發布的提案衝突" in out.items[3].reasons[0]
    entry = R.held_of(live(plane).manifest)[0]
    assert [p["id"] for p in entry["proposals"]] == ["prop-20261002T030000Z-7dd4e9c6", "prop-20261002T030000Z-0000000b",
                                                     "prop-20261002T030000Z-0000000c"]
    # one proposal can be approved on its own; the rest keeps waiting
    one = P.approve(plane.data, plane.control, "prop-20261002T030000Z-0000000b", actor="eunice", policy=P.Policy(mode="review"), now=T2)
    assert one.status == "published" and [w["proposalId"] for w in P.waiting(plane.data)] == ["prop-20261002T030000Z-7dd4e9c6",
                                                                                                "prop-20261002T030000Z-0000000c"]
    rest = P.approve(plane.data, plane.control, entry["release_id"], actor="eunice", policy=P.Policy(mode="review"), now=T2)
    assert rest.status == "published" and P.waiting(plane.data) == [] and event_date(live(plane).files) == "2026-11-30"


def test_a_proposal_file_that_cannot_be_read_is_refused_and_the_others_go_on(plane):
    plane.bootstrap()
    items = [("research/proposals/prop-missing.json", P.Unreadable("提案檔案讀不到：s3://llmops-curated/research/proposals/prop-missing.json does not exist")),
             proposals(plane)[0]]
    out = P.publish_batch(plane.data, items, control=plane.control, now=T1, trigger="event:3")
    assert [i.status for i in out.items] == ["rejected", "published"] and "讀不到" in out.items[0].reasons[0]
    assert out.summary()["proposals"][0] == {"key": "research/proposals/prop-missing.json", "id": "", "status": "rejected",
                                             "reasons": [out.items[0].reasons[0]]}


def test_nothing_to_publish_writes_nothing(plane):
    base = plane.bootstrap()
    sample = accepted_run()
    sample["proposal"]["portal"] = {"manifest": {"kind": "sample"}, "sample": True}
    out = P.publish_batch(plane.data, [("research/proposals/x.json", sample)], control=plane.control, now=T1, trigger="event:4")
    assert out.status == "rejected" and out.releases == [] and live(plane).release_id == base.release_id
    assert live(plane).manifest["sequence"] == 1 and R.list_release_ids(plane.data) == [base.release_id]
    assert P.publish_batch(plane.data, [], control=plane.control, now=T1, trigger="event:5").status == "unchanged"


def test_the_events_of_a_release_are_computed_from_its_catalog(plane):
    plane.bootstrap()
    out = P.publish_batch(plane.data, proposals(plane)[:1], control=plane.control, now=T1, trigger="event:6")
    # the Foundry date is a lifecycle fact, not a catalog change: nothing for route / price / status events
    assert out.published.catalog_changes == {"routes_added": [], "prices_changed": [], "retirements": [], "status_changes": []}
    assert R.read_meta(plane.data, out.published.release_id)["catalog_changes"] == out.published.catalog_changes
