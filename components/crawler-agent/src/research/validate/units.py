"""The units a page is quoted in and judged by: a table row, a heading, a sentence.

A quote is evidence for one statement of a page.  What the page states, it states in units a reader takes in as a
whole - "Claude Sonnet 4.5 | 1 | GA | 2026-11-30 | Claude Sonnet 5.5" is one statement, and four of its five cells
without the first are not a shorter statement but another one.  So a quote is widened to the units it touches before
it is stored (``research.evidence``), and whose value a unit states is judged per unit (``research.validate.subjects``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .dates import MONTH_NAME

#: what separates the cells of a table row in the text of a page (``web.html_text``)
CELL = " | "
_MD_ROW = re.compile(r"\s*\|.*\|\s*")
_MD_RULE = re.compile(r"\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*")
_HEADING = re.compile(r"#{1,6}\s+\S")

# A sentence ends at ". " / "! " / "? " when what follows begins like a sentence (a capital letter, CJK text, an
# opening quote or bracket in front of either) and what precedes is not an abbreviation or a single letter ("e.g. X",
# "N. Virginia", "U.S. East").  A CJK full stop always ends one.  When in doubt the text is NOT cut: a unit that is too
# long still holds every name that stands with its value, a unit cut in the wrong place may have lost one.
_ABBREVIATIONS = frozenset((
    "e.g i.e etc vs approx incl excl no nos fig eq ref sec ch vol ver v inc ltd corp co dept est ca cf al esp resp max min avg "
    "jan feb mar apr jun jul aug sep sept oct nov dec mon tue tues wed thu thur thurs fri sat sun mr mrs ms dr prof st mt ft"
).split())
_STOP = re.compile(r"[.!?][\"'”’)\]]*(?=\s+[\"'“‘(\[]?[A-ZÀ-Þ぀-ヿ㐀-鿿가-힣])|[。！？][”’」』）]*")
_LAST_WORD = re.compile(r"([A-Za-z][A-Za-z.]*)\.[\"'”’)\]]*\Z")
#: "am 30. November 2026", "le 1er. octobre": the stop behind the day of a date
_DAY_STOP = re.compile(r"(?<![\d.,])\d{1,2}\.\Z")
_MONTH_NEXT = re.compile(r"\s+(?:" + MONTH_NAME + r")(?![a-z])", re.I)


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of the sentences of ``text`` (one line of prose), blanks around them left out."""
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _STOP.finditer(text):
        end = m.end()
        if text[m.start()] == ".":
            if _DAY_STOP.search(text[start:end]) and _MONTH_NEXT.match(text, end):
                continue
            word = _LAST_WORD.search(text[start:end])
            if word is not None:
                last = word.group(1).lower().rstrip(".")
                if last in _ABBREVIATIONS or len(last) == 1 or "." in last:
                    continue  # "e.g.", "N.", "U.S.": the sentence goes on
        if text[start:end].strip():
            spans.append((start, end))
        start = end
    if text[start:].strip():
        spans.append((start, len(text)))
    out = []
    for a, b in spans:
        piece = text[a:b]
        out.append((a + len(piece) - len(piece.lstrip()), b - (len(piece) - len(piece.rstrip()))))
    return out


def is_row(line: str) -> bool:
    """Is ``line`` a table row: cells joined by `` | `` (an HTML table), or a Markdown row ``| a | b |``?"""
    return CELL in line or (_MD_ROW.fullmatch(line) is not None and line.count("|") >= 2)


def is_heading(line: str) -> bool:
    return _HEADING.match(line) is not None


def is_rule(line: str) -> bool:
    """The ``|---|---|`` line under the heading row of a Markdown table."""
    return "-" in line and "|" in line and _MD_RULE.fullmatch(line) is not None


def cells_of(line: str) -> list[tuple[int, int]]:
    """(start, end) of the cells of a row, blanks and a Markdown row's outer bars left out."""
    markdown = _MD_ROW.fullmatch(line) is not None
    spans: list[tuple[int, int]] = []
    start = 0
    for m in re.finditer(r"\|" if markdown else re.escape(CELL), line):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(line)))
    if markdown and len(spans) >= 3:
        spans = spans[1:-1]
    out = []
    for a, b in spans:
        piece = line[a:b]
        out.append((a + len(piece) - len(piece.lstrip()), b - (len(piece) - len(piece.rstrip()))))
    return out


@dataclass(frozen=True)
class Unit:
    kind: str  # row | heading | prose
    text: str
    line: int  # index of the quote's line it comes from
    head: str = ""  # row: the line of column headings of its table, when the page has one


def units_of(quote: str, heads: tuple = ()) -> list[Unit]:
    """The units of a (stored, i.e. widened) quote, in order.  ``heads``: per line of the quote, the heading row of the
    table the line is a row of ("" for anything else)."""
    out: list[Unit] = []
    for n, line in enumerate(quote.split("\n")):
        if not line.strip() or is_rule(line):
            continue
        if is_heading(line.lstrip()):
            out.append(Unit("heading", line.strip().lstrip("#").strip(), n))
        elif is_row(line):
            out.append(Unit("row", line.strip(), n, heads[n] if n < len(heads) and isinstance(heads[n], str) else ""))
        else:
            out.extend(Unit("prose", line[a:b], n) for a, b in sentence_spans(line))
    return out
