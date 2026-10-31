"""Clopper-Pearson bound, aggregation and the platform verdict rules."""
from __future__ import annotations

import pytest

from test_runner import stats


def rec(outcome, **kw):
    return {"outcome": outcome, "label": outcome, **kw}


def summary(passes=0, fails=0, reviews=0, errors=0):
    return stats.summarise([rec("pass")] * passes + [rec("fail")] * fails + [rec("review")] * reviews + [rec("error")] * errors)


@pytest.mark.parametrize("k, n, expected", [
    (0, 100, 0.0295),   # rule of three: about 3/n
    (0, 300, 0.00994),
    (1, 10, 0.3942),    # known one-sided 95% Clopper-Pearson values
    (5, 50, 0.1988),
    (10, 10, 1.0),
])
def test_clopper_pearson_upper(k, n, expected):
    assert stats.clopper_pearson_upper(k, n) == pytest.approx(expected, abs=5e-4)


def test_clopper_pearson_without_attempts():
    assert stats.clopper_pearson_upper(0, 0) is None


def test_errors_and_reviews_are_not_attempts():
    s = summary(passes=8, fails=2, reviews=3, errors=1)
    assert (s["executions"], s["attempts"], s["failures"], s["reviews"], s["errors"]) == (14, 10, 2, 3, 1)
    assert s["rate"] == pytest.approx(0.2)
    assert s["upper95"] == pytest.approx(stats.clopper_pearson_upper(2, 10))


@pytest.mark.parametrize("rule, s, expected", [
    ({"type": "max_rate", "max": 0.1}, summary(passes=19, fails=1), "pass"),
    ({"type": "max_rate", "max": 0.1}, summary(passes=17, fails=3), "fail"),
    ({"type": "max_rate", "max": 0.1}, summary(passes=18, fails=1, reviews=2), "review"),  # reviews could push it over
    ({"type": "zero"}, summary(passes=20), "pass"),
    ({"type": "zero"}, summary(passes=19, fails=1), "fail"),
    ({"type": "zero"}, summary(passes=19, reviews=1), "review"),
    ({"type": "zero", "review_failures": True}, summary(passes=19, fails=1), "review"),
    ({"type": "control"}, summary(passes=9, fails=1), "fail"),
    ({"type": "control"}, summary(passes=10), "pass"),
    ({"type": "info"}, summary(passes=1, fails=9), "info"),
    ({"type": "max_rate", "max": 0.1}, summary(reviews=5), "review"),
    ({"type": "max_rate", "max": 0.1}, summary(passes=7, errors=3), "error"),   # 30% errors
    ({"type": "max_rate", "max": 0.1}, summary(passes=8, errors=2), "pass"),    # exactly 20% is still usable
    ({"type": "info"}, summary(errors=5), "error"),
])
def test_verdicts(rule, s, expected):
    verdict, note, recorded = stats.verdict(rule, s)
    assert verdict == expected, note
    assert recorded["type"] == rule["type"]


def test_not_worse_than_baseline_uses_fallback_and_marks_baseline():
    rule = {"type": "not_worse_than_baseline", "fallback": {"type": "max_rate", "max": 0.1}}
    v, note, recorded = stats.verdict(rule, summary(passes=8, fails=2))
    assert v == "fail" and recorded["baseline"] == "missing" and recorded["applied"] == "max_rate"
    v, note, recorded = stats.verdict(rule, summary(passes=10), baseline_planned=True)
    assert v == "pass" and recorded["baseline"] == "pending"
    v, _, recorded = stats.verdict({"type": "not_worse_than_baseline", "fallback": {"type": "info"}}, summary(passes=5, fails=5))
    assert v == "info" and recorded["applied"] == "info"


def test_sec11_inputs_pairs_and_sources():
    records = [
        rec("pass", model="m-a", stop_reason="end_turn", source="s1", pair="q1", lang="zh-TW", trial=0),
        rec("pass", model="m-a", stop_reason="end_turn", source="s1", pair="q1", lang="en", trial=0),
        rec("fail", model="m-b", stop_reason="refusal", refusal=True, source="s2", pair="q2", lang="zh-TW", trial=0),
        rec("pass", model="m-a", stop_reason="end_turn", source="s2", pair="q2", lang="en", trial=0, rerouted=True),
        rec("error", source="s2"),
    ]
    s = stats.summarise(records)
    assert s["models_seen"] == {"m-a": 3, "m-b": 1} and s["stop_reasons"] == {"end_turn": 3, "refusal": 1}
    assert s["refusals"] == 1 and s["rerouted"] == 1 and s["safeguard_signals"] == 2
    assert s["pair_consistency"] == 0.5 and s["pairs"] == 2
    assert s["by_source"]["s2"]["errors"] == 1
