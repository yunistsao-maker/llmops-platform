"""Quote grounding: is this quote really on that page?

Matching is tolerant about typography (smart quotes, dashes, NBSP, case, table pipes, markdown
emphasis) but strict about content: every word of the quote must appear, in order, on the page.
On success the *page's own wording* is returned, so the evidence log never holds a paraphrase.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_TRANSLATE = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ", "…": "...",
    "|": " ", "*": " ", "`": " ",
})
_ZERO_WIDTH = {"​", "‌", "‍", "‎", "‏", "⁠", "﻿", "­"}
#: Marks that hang on a word or a number and are not part of it: superscript and subscript figures, circled and
#: parenthesised numbers - footnote marks, mostly.  Unicode's own folding (NFKC) turns "$3¹" into "$31": here they are
#: blanks, so the price stays 3 and the mark stays apart.
HANGING = frozenset(chr(c) for r in ((0xB2, 0xB4), (0xB9, 0xBA), (0x2070, 0x209D), (0x2460, 0x2500), (0x2776, 0x2794), (0x3251, 0x3260), (0x32B1, 0x32C0))
                    for c in range(*r))

MIN_QUOTE_CHARS = 12
MAX_QUOTE_CHARS = 900
#: what is stored for a quote is the whole rows / sentences it touches (research.evidence): at most this much
MAX_STORED_CHARS = 3000
MAX_OCCURRENCES = 40
_HALF = re.compile("[\ud800-\udfff]")


def whole(text: str) -> str:
    """``text`` with every half of a surrogate pair replaced by U+FFFD.

    A JSON page may say ``"\\ud83d"`` (half an emoji); Python keeps it, and a string that holds one cannot be written
    to a file, sent to the model or hashed - the whole run would end in an error at whatever step meets it first.
    """
    return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace") if _HALF.search(text) else text


def whole_json(value):
    """``value`` (parsed JSON) with ``whole`` applied to every string and property name."""
    if isinstance(value, str):
        return whole(value)
    if isinstance(value, list):
        return [whole_json(v) for v in value]
    if isinstance(value, dict):
        return {(whole(k) if isinstance(k, str) else k): whole_json(v) for k, v in value.items()}
    return value


@dataclass
class NormalizedText:
    """Normalised text plus a map from each normalised char back to the original offset."""

    text: str
    index: list[int]


def normalize_with_map(original: str) -> NormalizedText:
    out: list[str] = []
    idx: list[int] = []
    pending_space = False
    for i, ch in enumerate(original):
        if ch in _ZERO_WIDTH:
            continue
        if ch in HANGING:
            pending_space = True
            continue
        for c in unicodedata.normalize("NFKC", ch).translate(_TRANSLATE):
            if c.isspace():
                pending_space = True
                continue
            if pending_space and out:
                out.append(" ")
                idx.append(i)
            pending_space = False
            for folded in c.casefold():
                out.append(folded)
                idx.append(i)
    return NormalizedText("".join(out), idx)


def normalize(text: str) -> str:
    return normalize_with_map(text).text


@dataclass
class QuoteMatch:
    start: int  # offsets in the ORIGINAL page text
    end: int
    text: str  # page-verbatim wording
    context: str = ""


@dataclass
class QuoteProblem:
    code: str  # too_short | too_long | not_found | ambiguous
    message: str
    suggestions: list[str] = field(default_factory=list)


def find_all(page_text: str, norm: NormalizedText, quote: str, limit: int = MAX_OCCURRENCES) -> list[QuoteMatch] | QuoteProblem:
    """Every place ``quote`` stands on the page (at most ``limit``), in page order - or why it cannot be looked for."""
    nq = normalize(quote)
    if len(nq) < MIN_QUOTE_CHARS:
        return QuoteProblem("too_short", f"quote is too short ({len(nq)} chars; need >= {MIN_QUOTE_CHARS}). Quote the whole row / sentence.")
    if len(quote) > MAX_QUOTE_CHARS:
        return QuoteProblem("too_long", f"quote is too long ({len(quote)} chars; max {MAX_QUOTE_CHARS}). Quote only the sentence or table row that states the fact.")
    found: list[QuoteMatch] = []
    pos = norm.text.find(nq)
    while pos >= 0 and len(found) < limit:
        start = norm.index[pos]
        end = norm.index[pos + len(nq) - 1] + 1
        ctx_start, ctx_end = max(0, start - 160), min(len(page_text), end + 160)
        found.append(QuoteMatch(start, end, page_text[start:end], page_text[ctx_start:ctx_end]))
        pos = norm.text.find(nq, pos + max(len(nq), 1))
    if not found:
        return QuoteProblem(
            "not_found",
            "quote does not appear on that page. Copy the wording exactly (use find_in_page to locate the row/sentence); "
            "quote one contiguous passage, do not join cells from different rows.",
            closest_passages(page_text, quote),
        )
    return found


def find_quote(page_text: str, norm: NormalizedText, quote: str) -> QuoteMatch | QuoteProblem:
    """The first place ``quote`` stands on the page."""
    found = find_all(page_text, norm, quote, limit=1)
    return found if isinstance(found, QuoteProblem) else found[0]


_TOKEN = re.compile(r"[a-z0-9][a-z0-9.\-_:/@]*|[一-鿿]")


def closest_passages(page_text: str, quote: str, k: int = 2) -> list[str]:
    qt = set(_TOKEN.findall(normalize(quote)))
    if not qt:
        return []
    scored: list[tuple[float, str]] = []
    for line in page_text.splitlines():
        if len(line) < 12:
            continue
        lt = set(_TOKEN.findall(normalize(line)))
        if not lt:
            continue
        inter = len(qt & lt)
        if inter < 2:
            continue
        scored.append((inter / min(len(qt), len(lt)) + inter / 100, line.strip()))  # coverage of the shorter side, ties -> more overlap
    scored.sort(key=lambda x: -x[0])
    return [s[:300] for sc, s in scored[:k] if sc >= 0.4]
