"""Find calendar dates and numbers inside quoted evidence text - and where in the text they stand.

The agent may only write a date or a price into the portal if that exact value appears in a quote taken from a page it
really fetched, in the row or sentence that is about the record.  These helpers do the "does it appear, and where"
part, in the formats vendors actually use (ISO, US slash, ``October 14, 2026``, ``14 October 2026``, ``14.10.2026``,
``14-Oct-2026``, CJK, Korean, ROC years).

A date that can be read two ways (``11/10/2026``) is found with both readings and marked as doubtful: it is not the
validator's to pick the one that suits the proposal.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
#: month names of other languages, read only between a day and a year ("30. November 2026", "30 novembre 2026")
_MONTHS_ELSEWHERE = {
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "mai": 5, "juni": 6, "juli": 7, "oktober": 10, "dezember": 12,
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "juin": 6, "juillet": 7, "août": 8, "aout": 8, "septembre": 9,
    "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9,
    "octubre": 10, "noviembre": 11, "diciembre": 12,
}
_MON = "|".join(sorted(_MONTHS, key=len, reverse=True))
_MON_ANY = "|".join(sorted({**_MONTHS, **_MONTHS_ELSEWHERE}, key=len, reverse=True))
#: a regular expression for the name of a month, in any of the languages dates are read in
MONTH_NAME = _MON_ANY

_ISO = re.compile(r"(?<![\d.])(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?![\d])")
_US = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4})(?![\d/])")
_DOTTED = re.compile(r"(?<![\w.])(\d{1,2})\.(\d{1,2})\.(\d{4})(?![\d.]\d|\d)")
_MDY = re.compile(rf"\b({_MON})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s*(\d{{4}})\b", re.I)
# (not after a digit or a dot: the "5" of "Claude Haiku 4.5 October 2026" is not a day)
_DMY = re.compile(rf"(?<![\d.])(\d{{1,2}})(?:st|nd|rd|th|\.)?(?:\s+(?:of\s+|de\s+)?|-)({_MON_ANY})\.?,?(?:\s+(?:de\s+)?|-)(\d{{4}})\b", re.I)
#: What a number in front of a month may belong to instead of the date: "Tier 2 November 2026" and "Phase 3 December 2026"
#: name a tier and a phase, "GPT-5 March 2027" a model - and then no day is given at all.
_COUNTED = re.compile(r"(?i)(?:(?<![a-z])(?:tier|level|phase|step|stage|part|gen|generation|type|class|option|wave|round|group|zone|item|section|chapter|"
                      r"page|line|row|table|figure|fig|note|footnote|appendix|priority|rank|top|version|ver|release|build|rev|week|quarter|no|number|"
                      r"model|plan|pack|bundle|sku|batch|"
                      # ... and the words model names end in: "Claude Opus 4 June 2027", "Mistral Large 3 January 2027"
                      r"opus|sonnet|haiku|pro|flash|large|small|medium|mini|nano|micro|lite|ultra|max|plus|turbo|premier|instant|preview|sol|luna|"
                      r"astra|omni|codex|scout|maverick|guard|reel|canvas|sonic)\.?[ \t]?|[a-z][\w.]*-)\Z")
_CJK = re.compile(r"(?<![\d民國])(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日號号]")
_ROC = re.compile(r"(?:中華)?民國\s*(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日號号]")
# a year of three figures in front of 年 is a year of the Republic: "自115年10月1日生效" (no calendar a page uses has a year 115 otherwise)
_ROC_BARE = re.compile(r"(?<![\d民國])(1\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日號号]")
_KOREAN = re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
# A day without a year: "3 月 31 日", "March 31", "31 March" - and, in figures, "10-20" / "10/20" (both parts written with
# two figures: that is how a date is abbreviated, and hardly how a range or a fraction is written)
_MON_NOT_MAY = "|".join(sorted((m for m in _MONTHS if m != "may"), key=len, reverse=True))  # "it may 5 ..." is no date
_MD_CJK = re.compile(r"(?<![\d年])(\d{1,2})\s*月\s*(\d{1,2})\s*[日號号]")
_MD_EN = re.compile(rf"(?<![a-z])({_MON_NOT_MAY})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?![\w.]\w|\d)(?!\s*,?\s*\d{{4}})", re.I)
_DM_EN = re.compile(rf"(?<![\w.])(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MON_NOT_MAY})(?![a-z])(?!\.?,?\s+\d{{4}})", re.I)
# (behind a colon as well - "截止：10-20" - but not behind the colon of a time of day)
_MD_FIGURES = re.compile(r"(?<![\w.\-/$])(?<!\d:)(0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])(?![\w\-/:%]|\.\d)")


@dataclass(frozen=True)
class DateAt:
    start: int
    end: int
    day: dt.date
    doubt: str = ""  # why this reading is not the only one


def _mk(y: int, m: int, d: int) -> dt.date | None:
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


def date_spans(text: str) -> list[DateAt]:
    """Every calendar date written in ``text``, with where it stands.  A date with two readings is listed once for
    each, both marked with ``doubt``."""
    found: list[DateAt] = []

    def add(m: re.Match, y: int, mo: int, d: int, doubt: str = "") -> None:
        day = _mk(y, mo, d)
        if day is not None:
            found.append(DateAt(m.start(), m.end(), day, doubt))

    for m in _ISO.finditer(text):
        add(m, int(m[1]), int(m[2]), int(m[3]))
    for m in _CJK.finditer(text):
        add(m, int(m[1]), int(m[2]), int(m[3]))
    republic = [m.span() for m in _ROC.finditer(text)]
    for m in _ROC.finditer(text):
        add(m, int(m[1]) + 1911, int(m[2]), int(m[3]))
    for m in _ROC_BARE.finditer(text):
        if not any(a <= m.start() and m.end() <= b for a, b in republic):  # ("民國 115 年 ..." is read once)
            add(m, int(m[1]) + 1911, int(m[2]), int(m[3]))
    for m in _KOREAN.finditer(text):
        add(m, int(m[1]), int(m[2]), int(m[3]))
    for pattern, order in ((_US, "month/day or day/month"), (_DOTTED, "day.month or month.day")):
        for m in pattern.finditer(text):
            a, b, y = int(m[1]), int(m[2]), int(m[3])
            first = (a, b) if pattern is _US else (b, a)  # the usual reading: US month/day, European day.month
            readings = [r for r in (first, (first[1], first[0])) if _mk(y, *r) is not None]
            both = len(set(readings)) > 1
            for mo, d in dict.fromkeys(readings):
                add(m, y, mo, d, f"{m.group(0)} can be read as {order}" if both else "")
    for m in _MDY.finditer(text):
        add(m, int(m[3]), _MONTHS[m[1].lower()], int(m[2]))
    months = {**_MONTHS_ELSEWHERE, **_MONTHS}
    for m in _DMY.finditer(text):
        lead = _COUNTED.search(text[max(0, m.start() - 24):m.start()])
        add(m, int(m[3]), months[m[2].lower()], int(m[1]),
            f"the {m[1]} in {m.group(0)!r} may belong to what stands in front of it ({(lead.group(0) + m[1]).strip()!r}) and not be a day" if lead else "")
    return sorted(found, key=lambda f: (f.start, f.end, f.day))


@dataclass(frozen=True)
class DayAt:
    """A day of the year written without its year."""

    start: int
    end: int
    month: int
    day: int
    figures: bool = False  # written as "10-20": it may be a range or a fraction instead


def partial_dates(text: str) -> list[DayAt]:
    """The days ``text`` names without a year - not the ones that are part of a full date."""
    taken = [(f.start, f.end) for f in date_spans(text)]
    out: list[DayAt] = []

    def add(m: re.Match, month: int, day: int, figures: bool = False) -> None:
        if _mk(2024, month, day) is not None and not any(s < m.end() and m.start() < e for s, e in taken):
            out.append(DayAt(m.start(), m.end(), month, day, figures))
            taken.append((m.start(), m.end()))

    for m in _MD_CJK.finditer(text):
        add(m, int(m[1]), int(m[2]))
    for m in _MD_EN.finditer(text):
        add(m, _MONTHS[m[1].lower()], int(m[2]))
    for m in _DM_EN.finditer(text):
        add(m, _MONTHS[m[2].lower()], int(m[1]))
    for m in _MD_FIGURES.finditer(text):
        add(m, int(m[1]), int(m[2]), True)
    return sorted(out, key=lambda f: (f.start, f.end))


def extract_dates(text: str) -> set[dt.date]:
    """All calendar dates written in ``text``.  Ambiguous ``a/b/YYYY`` yields both readings."""
    return {f.day for f in date_spans(text)}


def date_support(value: str, quote_dates: set[dt.date]) -> str:
    """'exact' if the ISO date is in the quotes, 'adjacent' if only +/-1 day is, else 'none'."""
    try:
        d = dt.date.fromisoformat(value)
    except ValueError:
        return "none"
    if d in quote_dates:
        return "exact"
    if any(abs((d - q).days) == 1 for q in quote_dates):
        return "adjacent"
    return "none"


# ---------------------------------------------------------------------------------------------
_WORD_SCALE = {"k": 1e3, "m": 1e6, "b": 1e9, "thousand": 1e3, "million": 1e6, "billion": 1e9, "萬": 1e4, "万": 1e4, "億": 1e8, "亿": 1e8}
# 1,250 / 1 000 000 / 1250 / 0.003, with a scale behind it: 272K, 1M, 2 million, 20 萬
# (not behind a letter, a digit, a dot, or "^" / "_" - an exponent or an index is not a quantity of its own)
# ("\w" would take in every CJK character: "100万トークン" and "約3ドル" are written without blanks)
_NUM = re.compile(r"(?<![A-Za-z0-9_.^])\$?\s?(\d{1,3}(?:,\d{3})+|\d{1,3}(?: \d{3}){2,}|\d+)(\.\d+)?"
                  r"(?:\s?([kKmMbB])(?![A-Za-z0-9_])|\s?(thousand|million|billion)\b|\s?([萬万億亿]))?(?![A-Za-z0-9_])", re.I)


@dataclass(frozen=True)
class NumberAt:
    start: int
    end: int
    values: tuple[float, ...]  # the number as written, and scaled when a scale stands behind it (272K: 272 and 272000)
    per_1k: bool = False  # priced per 1,000 tokens (see ``quantity_spans``)


def _numbers(text: str) -> list[NumberAt]:
    out: list[NumberAt] = []
    for m in _NUM.finditer(text):
        raw = m[1].replace(",", "").replace(" ", "") + (m[2] or "")
        try:
            val = float(raw)
        except ValueError:
            continue
        values = [round(val, 10)]
        scale = (m[3] or m[4] or m[5] or "").lower()
        if scale:
            values.append(round(val * _WORD_SCALE[scale], 6))
        start = m.start() + (len(m.group(0)) - len(m.group(0).lstrip("$ ")))
        out.append(NumberAt(start, m.end(), tuple(values)))
    return out


def extract_numbers(text: str) -> set[float]:
    """Numbers in ``text`` ($ and thousands separators stripped; 272K -> 272000, 1M -> 1e6)."""
    return {v for n in _numbers(_FOOTNOTE_REF.sub(_blank, text)) for v in n.values}


def number_support(value: float, quote_numbers: set[float], per_1k: bool = False) -> str:
    """'exact' | 'per-1k' (the quote prices 1,000 tokens, the portal 1M: only when ``per_1k`` says so) | 'none'."""
    v = round(float(value), 10)
    if any(abs(v - q) < 1e-9 for q in quote_numbers):
        return "exact"
    if per_1k and any(abs(v - q * 1000) < 1e-6 for q in quote_numbers):
        return "per-1k"
    return "none"


# ---------------------------------------------------------------------------------------------
# Numbers that are quantities.  A line of a pricing table also holds numbers that are not: the "5.5" of a model's name,
# the digits of a model id ("…-5-5-20260928-v1:0"), the parts of a date, a footnote mark, a time of day, an HTTP status.
# None of them is a price, a limit or a score.
_DATE_SHAPES = (
    _ISO, _US, _DOTTED, _MDY, _DMY, _CJK, _ROC, _KOREAN,
    re.compile(rf"\b(?:{_MON})\.?\s+\d{{4}}\b", re.I),  # "October 2026"
)
#: a name or an id written with letters, digits and joints: claude-sonnet-5-5, gpt-6.1-sol, us-east-1, anthropic.claude-…-v1:0
_IDENT = re.compile(r"(?<![\w$.])[A-Za-z]{2,}[A-Za-z0-9]*(?:[-_.:@/][A-Za-z0-9]+)+")
_FOOTNOTE_REF = re.compile(r"\[\d{1,3}(?:\s?[,;]\s?\d{1,3})*\]")  # how html_text writes a footnote mark: $3[1]
_CLOCK = re.compile(r"(?<![\d:.])\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?![\d:])")
_STATUS_CODE = re.compile(r"(?i)\b(?:http(?:/\d(?:\.\d)?)?|status(?:\s+code)?|error(?:\s+code)?|code)\s*:?\s*[1-5]\d{2}\b")
#: "per 1,000 tokens", "/ 1K tokens", "per thousand input tokens", "每千個 token": a rate that is a thousandth of the portal's
_PER_1K = re.compile(r"(?i)(?:per|/|每)\s*(?:1[,.]?000|1\s?k|thousand|一?千)\s*(?:個|个)?\s*(?:(?:input|output|prompt|completion|cached?|text|輸入|輸出|输入|输出)\s*)?"
                     r"(?:tokens?|toks?\b|字元|字符)"
                     r"|(?:1[,.]?000|1\s?k)\s*(?:トークン|tokens?)\s*(?:あたり|当たり|ごと|につき)")  # 入力 1,000 トークンあたり $0.003
#: "per 1M tokens", "/ MTok", "per million": the portal's own rate
_PER_1M = re.compile(r"(?i)(?:per|/|每)\s*(?:1\s?m\b|m\s?tok|million|一?百萬|一?百万)")
_ANY_PER_1K = re.compile(r"(?i)per\s*1[,.]?000\b|per\s*(?:1\s*)?k\b|/\s*1\s*k\b|per\s+thousand|每\s*(?:1[,.]?000|千)")


def _blank(m: re.Match) -> str:
    return " " * len(m.group(0))


def strip_non_quantities(text: str, names: tuple = ()) -> str:
    """``text`` with everything that holds digits without stating an amount replaced by blanks of the same length (so
    that what is found in the result stands where it stands in ``text``)."""
    # identifiers first: a name pattern would cut "claude-sonnet-5-5" out of the middle of a longer model id and leave
    # the rest of the id behind as loose numbers
    text = _IDENT.sub(lambda m: _blank(m) if any(ch.isdigit() for ch in m.group(0)) else m.group(0), text)
    for shape in (_FOOTNOTE_REF, *_DATE_SHAPES, _CLOCK, _STATUS_CODE):
        text = shape.sub(_blank, text)
    for pattern in names:
        text = pattern.sub(_blank, text)
    return text


def quantity_spans(text: str, names: tuple = (), head: str = "") -> list[NumberAt]:
    """The numbers in ``text`` that state an amount, with where they stand and whether they are priced per 1,000 tokens.

    A number is priced per 1,000 tokens when the phrase that says so stands with it: right behind it ("$0.003 per
    1,000 input tokens"), in front of it with no other rate in between, or in the heading of its column (``head``).
    That a line says "per 1,000" of something somewhere is not enough - "$3 per million tokens (up to 1,000 requests
    per minute)" prices a million tokens at three dollars, not at three thousand.
    """
    clean = strip_non_quantities(text, names)
    found = _numbers(clean)
    if not found:
        return found
    column = _PER_1K.search(head) is not None and _PER_1M.search(head) is None
    rates = sorted([(m.start(), m.end(), True) for m in _PER_1K.finditer(text)] + [(m.start(), m.end(), False) for m in _PER_1M.finditer(text)])
    found = [n for n in found if not any(s <= n.start and n.end <= e for s, e, _k in rates)]  # the "1,000" of "per 1,000 tokens" is no amount
    out = []
    for i, n in enumerate(found):
        nxt = found[i + 1].start if i + 1 < len(found) else len(text)
        behind = next(((s, e, k) for s, e, k in rates if n.end <= s <= min(n.end + 24, nxt)), None)
        before = [(s, e, k) for s, e, k in rates if e <= n.start]
        if behind is not None:
            per_1k = behind[2]
        elif before:
            per_1k = before[-1][2]
        else:
            per_1k = column
        out.append(NumberAt(n.start, n.end, n.values, per_1k))
    return out


def quantities(text: str, names: tuple = ()) -> set[float]:
    """The numbers in ``text`` that state an amount: not those inside a date, a name or an identifier.

    ``names``: compiled patterns of model names to leave out as well ("Claude Sonnet 5.5" holds no 5.5 of anything).
    """
    return {v for n in _numbers(strip_non_quantities(text, names)) for v in n.values}


def priced_per_1k(text: str) -> bool:
    """Does ``text`` say that its prices are per 1,000 (tokens)?"""
    return _ANY_PER_1K.search(text) is not None
