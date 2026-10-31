"""Dates and amounts as pages write them - and where a reading is not the only one.

A date that can be read two ways is found with both readings and marked as doubtful (it is not the validator's to pick
the one that suits the proposal); a figure that may belong to what stands in front of it is doubtful too; and a number
is an amount only where the page states one.
"""

import datetime as dt

import pytest

from research.validate import dates as D


def days(text):
    return sorted(f.day.isoformat() for f in D.date_spans(text))


@pytest.mark.parametrize("text", [
    "claude-sonnet-4-5 | 1 | GA | 2026-11-30 | claude-sonnet-5-5", "retires on November 30th, 2026.", "の提供終了日は 2026 年 11 月 30 日です。", "將於 2026/11/30 退役。",
    "retires on 30.11.2026.", "retires on 30-Nov-2026.", "wird am 30. November 2026 eingestellt.", "le 30 novembre 2026", "el 30 de noviembre de 2026",
    "은 2026년 11월 30일에 지원이 종료됩니다.", "將於民國 115 年 11 月 30 日退役。", "自115年11月30日生效。", "Nov. 30, 2026", "30 November 2026",
])
def test_a_date_is_read_in_the_spellings_pages_use(text):
    assert days(text) == ["2026-11-30"]


def test_a_date_with_two_readings_is_found_with_both_and_marked():
    found = D.date_spans("Retirement date: 11/10/2026")
    assert sorted(f.day.isoformat() for f in found) == ["2026-10-11", "2026-11-10"] and all("can be read as" in f.doubt for f in found)
    assert [(f.day.isoformat(), f.doubt) for f in D.date_spans("13/10/2026")] == [("2026-10-13", "")]  # there is no 13th month
    assert [f.doubt != "" for f in D.date_spans("11.10.2026")] == [True, True]
    assert D.extract_dates("11/10/2026") == {dt.date(2026, 11, 10), dt.date(2026, 10, 11)}


def test_a_figure_in_front_of_a_month_may_belong_to_what_stands_before_it():
    """"Tier 2 November 2026", "GPT-5 March 2027": a tier and a month, a model and a month - and no day at all."""
    for text in ("Tier 2 November 2026", "Phase 3 December 2026", "GPT-5 March 2027", "Claude Opus 4 June 2027", "Mistral Large 3 January 2027"):
        found = D.date_spans(text)
        assert len(found) == 1 and "may belong to what stands in front of it" in found[0].doubt, text
    for text in ("Retires on 5 March 2027", "by 14 October 2026", "EOL: 30-Nov-2026", "Date: 2 November 2026"):
        assert [f.doubt for f in D.date_spans(text)] == [""], text
    assert days("Claude Haiku 4.5 October 2026 (exact day to be announced)") == []  # not behind a digit or a dot


def test_a_day_without_its_year():
    def partial(text):
        return [(f.month, f.day, f.figures) for f in D.partial_dates(text)]

    assert partial("改於明年 3 月 31 日退役") == [(3, 31, False)]
    assert partial("by March 31 or 31 March; on March 31, 2027 and 5 May 2027") == [(3, 31, False), (3, 31, False)]  # not the ones that have a year
    assert partial("10-20 停止服務（09/29 更新）") == [(10, 20, True), (9, 29, True)]
    # what is written with figures and is no day: a share, a range, a fraction, a version, a full date
    assert partial("10-20% 的流量；1-2 個；24/7；2026-10-20；v1.10-20；10-20-2026；3.5/4.2") == []
    assert partial("it may 5 times, and June 31 is no day") == []


def test_numbers_are_read_next_to_text_that_has_no_blanks():
    assert D.extract_numbers("コンテキストウィンドウは 100万トークンです") == {100.0, 1_000_000.0}
    assert D.extract_numbers("約3ドル、第3季、1Mトークン") == {3.0, 1.0, 1_000_000.0}
    assert D.extract_numbers("272K tokens, 1 000 000 tokens, 2 million") == {272.0, 272_000.0, 1_000_000.0, 2.0, 2_000_000.0}
    assert D.extract_numbers("3rd, 4o-mini, x^2") == set()  # an ordinal, a name, an exponent


def test_what_holds_figures_without_stating_an_amount():
    line = "Claude Sonnet 5.5[1] pricing changes at 00:00 UTC on 2026-11-01; requests that fail with HTTP 429 are not billed (see claude-sonnet-5-5-20260928)."
    assert D.quantities(line) == {5.5}                                      # (the model's own number goes with its name pattern, below)
    import re
    assert D.quantities(line, (re.compile(r"claude sonnet 5\.5", re.I),)) == set()
    assert D.quantities("$3[1] per MTok, 12:30:15, status code 503, error 404") == {3.0}


def test_a_rate_of_a_thousand_tokens_goes_with_the_number_it_stands_with():
    def rated(text, head=""):
        return [(n.values[0], n.per_1k) for n in D.quantity_spans(text, (), head)]

    assert rated("$0.003 per 1,000 input tokens | $0.015 per 1K output tokens") == [(0.003, True), (0.015, True)]
    assert rated("每千個輸入 token 0.003 美元") == [(0.003, True)] and rated("入力 1,000 トークンあたり $0.003") == [(0.003, True)]
    # "per 1,000" of something else, behind prices of a million: the prices are not scaled
    assert rated("$3 per million input tokens and $15 per million output tokens; web search is $10 per 1,000 searches") == [
        (3.0, False), (15.0, False), (10.0, False), (1000.0, False)]
    # a rate goes with the number it stands right behind - not with every number somewhere in front of it
    assert rated("Input costs $3 and output $0.015 per 1,000 tokens") == [(3.0, False), (0.015, True)]
    assert rated("The price is $3 for the standard tier, or with the legacy plan $0.003 per 1,000 input tokens") == [(3.0, False), (0.003, True)]
    assert rated("$0.003 | $0.015", "Input per 1,000 tokens | Output per 1,000 tokens") == [(0.003, True), (0.015, True)]
    assert rated("$3 | $15", "Input per 1M tokens (was per 1,000 tokens) | Output") == [(3.0, False), (15.0, False)]
