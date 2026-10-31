"""The task brief the model receives: the reporting period and whether it should write a weekly digest.

The portal no longer gets exactly one digest per week (manual runs add extra ones), so a run can start on a day that already has a digest.
A digest's id is its periodEnd, so a second digest for that day could never be applied - the brief must tell the model not to write one.
"""

from __future__ import annotations

import datetime as dt

from research.prompts import build_task_prompt, default_period

MONDAY = dt.date(2026, 10, 5)
LIMITS = {"searches": 10, "fetches": 20, "toolCalls": 60}
SUMMARY = {"manifest": {"kind": "portal"}}
DIGEST_LINE = "Finish with a weekly digest"


def portal_docs(*period_ends: str) -> dict:
    docs: dict = {"data/lifecycle.json": {"events": []}}
    for end in period_ends:
        docs[f"data/digests/{end}.json"] = {"id": end, "periodEnd": end}
    return docs


def brief(task: dict, docs: dict, today: dt.date = MONDAY) -> str:
    return build_task_prompt(task, SUMMARY, docs, today, LIMITS)


# --- the default reporting period ------------------------------------------------------------------------------------------------------
def test_the_period_starts_the_day_after_the_newest_digest_ended():
    assert default_period(portal_docs("2026-09-28", "2026-10-02"), MONDAY) == ("2026-10-03", "2026-10-05")


def test_without_any_digest_the_period_is_the_last_seven_days():
    assert default_period({}, MONDAY) == ("2026-09-28", "2026-10-05")


# --- the weekly digest instruction -----------------------------------------------------------------------------------------------------
def test_a_weekly_brief_asks_for_a_digest_covering_the_new_period():
    text = brief({"kind": "weekly"}, portal_docs("2026-09-28", "2026-10-02"))
    assert f"{DIGEST_LINE} in the proposal: id and periodEnd = 2026-10-05, periodStart = 2026-10-03" in text


def test_no_second_digest_is_requested_for_a_day_that_already_has_one():
    text = brief({"kind": "weekly"}, portal_docs("2026-10-02", "2026-10-05"))
    assert DIGEST_LINE not in text
    assert 'set "digest" to null' in text
    assert "2026-10-05" in text.split('set "digest" to null')[0]  # it names the digest that is already there


def test_a_digest_dated_after_the_period_end_also_blocks_a_new_one():
    text = brief({"kind": "weekly"}, portal_docs("2026-10-06"))
    assert DIGEST_LINE not in text and 'set "digest" to null' in text


def test_an_explicit_period_is_checked_the_same_way():
    docs = portal_docs("2026-10-02")
    covered = brief({"kind": "weekly", "periodStart": "2026-10-01", "periodEnd": "2026-10-02"}, docs)
    assert DIGEST_LINE not in covered and 'set "digest" to null' in covered
    free = brief({"kind": "weekly", "periodStart": "2026-10-03", "periodEnd": "2026-10-04"}, docs)
    assert f"{DIGEST_LINE} in the proposal: id and periodEnd = 2026-10-04, periodStart = 2026-10-03" in free


def test_include_digest_false_never_asks_for_one():
    text = brief({"kind": "weekly", "includeDigest": False}, portal_docs("2026-10-02"))
    assert DIGEST_LINE not in text and 'set "digest" to null' not in text


def test_other_kinds_do_not_mention_the_digest_rule():
    for kind in ("focus", "verify", "prompt"):
        text = brief({"kind": kind, "focus": ["lifecycle"], "claims": ["x"]}, portal_docs("2026-10-05"))
        assert DIGEST_LINE not in text and 'set "digest" to null' not in text
