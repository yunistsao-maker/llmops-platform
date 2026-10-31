"""Recording evidence: a verbatim quote from a page the agent really fetched.

What is stored is not the characters that were asked for but the units of the page they stand in: the whole table row,
the whole sentence, the whole heading (``validate.units``).  A row without the cell that names its model, or the end of
one row and the beginning of the next, is not something the page says - and judged as if it were, it "says" that the
replacement model retires on the day the old one does.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .session import Evidence, Page, ResearchSession
from .validate.dates import extract_dates, extract_numbers
from .validate.grounding import MAX_STORED_CHARS, QuoteMatch, QuoteProblem, find_all, whole
from .validate.subjects import portal_subjects, without_names
from .validate.units import is_heading, is_row, sentence_spans

_ADDRESS = re.compile(r"(?i)\s*(?:https?:)?//")


def context_parts(page: Page, offset: int) -> tuple[list[str], int]:
    """(the lines of the context of a quote at ``offset``, how many of the first of them are the address and the title).

    What stands above a quote, outermost first: the URL path, the page title, and the headings the quote stands
    *under* - the section it is in and the sections around that one, not the sections before it.  On a page with a
    section per model, the heading of the previous model's section says nothing about a quote from the next one; and a
    heading of the same or a higher level closes the sections before it.

    The path is the path: what an address says after "?" or "#" is what somebody asked for, not what the page is.  For
    the same reason a title that is the address itself (a page without one is listed under its address) is left out.
    """
    stack: list[tuple[int, str]] = []
    for level, text, at in page.headings:
        if at > offset:
            break
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, text))
    try:
        path = urlparse(page.final_url).path.replace("/", " ").strip()
    except ValueError:
        path = ""
    title = page.title if isinstance(page.title, str) and page.title not in (page.url, page.final_url) and not _ADDRESS.match(page.title) else ""
    outer = [" ".join(part.split()) for part in (path, title) if part and part.strip()]
    headings = [" ".join(text.split()) for _level, text in stack if text and text.strip()]
    return [*outer, *headings], len(outer)


def context_for(page: Page, offset: int) -> str:
    """The context of a quote at ``offset`` (``context_parts``), one line per part."""
    return "\n".join(context_parts(page, offset)[0])


def line_of(page: Page, offset: int) -> int:
    """The (1-based) number of the line of ``page.text`` that ``offset`` is on - as find_in_page and read_page show it."""
    return page.text.count("\n", 0, max(0, offset)) + 1


def widen(text: str, start: int, end: int) -> tuple[int, int]:
    """``(start, end)`` grown to the units it touches: whole lines - and, where a line is prose, whole sentences."""
    a = text.rfind("\n", 0, start) + 1
    b = text.find("\n", max(end - 1, start))
    b = len(text) if b < 0 else b
    first_end = text.find("\n", a)
    first_end = len(text) if first_end < 0 else first_end
    last_start = text.rfind("\n", 0, b) + 1
    first, last = text[a:first_end], text[last_start:b]
    new_a, new_b = a, b
    if not is_row(first) and not is_heading(first.lstrip()):
        for s, e in sentence_spans(first):
            if a + e > start:
                new_a = a + s
                break
    if not is_row(last) and not is_heading(last.lstrip()):
        for s, e in reversed(sentence_spans(last)):
            if last_start + s < end:
                new_b = last_start + e
                break
    while new_a < new_b and text[new_a] in " \t\r\n":
        new_a += 1
    while new_b > new_a and text[new_b - 1] in " \t\r\n":
        new_b -= 1
    return new_a, new_b


def _heads(page: Page, start: int, quote: str) -> tuple:
    first = line_of(page, start) - 1
    return tuple(page.row_heads.get(first + k, "") if is_row(line) else "" for k, line in enumerate(quote.split("\n")))


def record_quote(session: ResearchSession, page: Page, quote: str, note: str = "", line: int = 0) -> tuple[Evidence | None, QuoteProblem | None]:
    """Verify ``quote`` against ``page`` and store the rows / sentences it stands in.  Returns (evidence, None) or
    (None, problem).

    ``line``: the number of a line the passage is on (as find_in_page / read_page show it), for a text that stands on
    the page in more than one place - the same price row under two models, the same sentence in two sections.  Which
    place is meant decides what the quote is about, so it is asked, not guessed.
    """
    note = whole(note) if isinstance(note, str) else ""
    matches = find_all(page.text, page.norm(), quote)
    if isinstance(matches, QuoteProblem):
        return None, matches
    places: list[tuple[int, int, str, str, tuple, int]] = []  # (start, end, text, context, heads, outer): the distinct things the quote could be
    for m in matches:
        a, b = widen(page.text, m.start, m.end)
        text = page.text[a:b]
        lines, outer = context_parts(page, a)
        place = (a, b, text, "\n".join(lines), _heads(page, a, text), outer)
        if not any(p[2:] == place[2:] for p in places):
            places.append(place)
    if len(places) > 1 and line:
        on_line = [p for p in places if line_of(page, p[0]) <= line <= line_of(page, p[1])]
        if not on_line:  # the nearest place at or after the line that was named
            later = [p for p in places if line_of(page, p[0]) >= line]
            on_line = later[:1]
        places = on_line or places
    if len(places) > 1:
        where = [f"L{line_of(page, a)}" + (f" (under: {context.splitlines()[-1][:80]})" if context else "") for a, _b, _t, context, _h, _o in places[:8]]
        return None, QuoteProblem(
            "ambiguous",
            f"this text stands in {len(places)} places on the page, under different headings or in different rows ({', '.join(x.split(' ')[0] for x in where)}). "
            "Which one is meant decides what the quote is about: call record_evidence again with line=<the line number of the one you mean>.",
            where)
    a, b, text, context, heads, outer = places[0]
    if len(text) > MAX_STORED_CHARS:
        return None, QuoteProblem(
            "too_long",
            f"the rows / sentences this passage stands in are {len(text)} characters long (max {MAX_STORED_CHARS}). Quote fewer lines: "
            "the one row or sentence that states the fact.")
    for ev in session.evidence.values():  # the same passage twice is the same evidence
        if ev.page_id == page.id and ev.quote == text and ev.start == a:
            if note and not ev.note:
                ev.note = note[:300]
            return ev, None
    ev = Evidence(
        id=session.next_id("e"),
        page_id=page.id,
        url=page.final_url,
        title=page.title,
        tier=page.tier,
        quote=text,
        note=note[:300],
        fetched_at=page.fetched_at,
        page_sha256=page.sha256,
        dates=sorted(d.isoformat() for d in extract_dates(without_names(text, portal_subjects(session.portal.docs)))),
        numbers=sorted(extract_numbers(text)),
        context=context,
        outer=outer,
        start=a,
        heads=heads,
    )
    session.evidence[ev.id] = ev
    return ev, None
