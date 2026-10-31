"""Turn fetched bodies (HTML / JSON / feeds / text) into plain text the agent can quote.

Tables matter most: lifecycle and pricing pages are tables, so every row becomes one line
``cell | cell | cell`` -- a contiguous, quotable unit - and the line of column headings of its table is remembered with
it (``ParsedPage.row_heads``): which column a value stands in is part of what the row says.

The text says what the page says to a reader, not what its markup happens to concatenate to:

* a footnote mark is not part of the value it hangs on (``$3<sup>1</sup>`` is "$3[1]", never "$31");
* what the page crossed out (``<del>``, ``<s>``) is not there any more;
* the label of an accordion or of a tab is the heading of what stands under it, although it is a ``<button>``;
* a cell that spans several rows belongs to each of them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import warnings

from bs4 import BeautifulSoup, Comment, FeatureNotFound, NavigableString, Tag, XMLParsedAsHTMLWarning

from ..validate.grounding import whole

SKIP_TAGS = {
    "script", "style", "noscript", "template", "svg", "iframe", "canvas", "nav", "footer", "aside",
    "form", "button", "select", "option", "dialog", "head", "meta", "link", "object", "embed",
    "del", "s", "strike",  # what the page struck out: an old date next to the new one, a price that no longer holds
}
#: an <aside> inside the article is a call-out (Google's documentation writes its deprecation notices that way); one that
#: says by its name what it is for is the site's furniture
_CHROME = re.compile(r"(?i)sidebar|toc\b|table-of-contents|\bnav|menu|breadcrumb|related|share|social|newsletter|cookie|banner|advert|promo|feedback")
BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "header", "li", "ul", "ol", "blockquote", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6", "dl", "dt", "dd", "details", "summary", "figure",
    "figcaption", "address", "fieldset", "body", "html", "caption",
}
HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_WS = re.compile(r"\s+")
_MAX_DEPTH = 120


@dataclass
class ParsedPage:
    title: str  # the page's own title ("" when it has none: the address is not a title)
    text: str
    kind: str = "html"  # html | json | xml | text
    headings: list[tuple[int, str, int]] = field(default_factory=list)  # (level, text, char offset)
    links: list[tuple[str, str]] = field(default_factory=list)  # (anchor text, absolute url)
    #: line number (0-based) of a table row -> the line of column headings of its table
    row_heads: dict[int, str] = field(default_factory=dict)


_PRIVATE_USE = re.compile("[\ue000-\uf8ff]")  # icon-font glyphs (anchor links etc.) that sites put in headings


def _clean(s: str) -> str:
    return _WS.sub(" ", _PRIVATE_USE.sub("", s)).strip()


_FOOTNOTE = re.compile(r"[\[(]?\s*(\d{1,3}(?:\s*[,;]\s*\d{1,3})*|[*†‡§¶#]{1,3}|[a-z])\s*[\])]?")
_ORDINAL = re.compile(r"(?i)st|nd|rd|th|er|re|e|ème|º|ª")
_TRADEMARK = re.compile(r"(?i)tm|sm|®|™|℠|\(r\)|\(tm\)|\(sm\)")


def _script_text(node: Tag) -> str:
    """What a ``<sup>`` / ``<sub>`` says, kept apart from what it hangs on: a footnote mark becomes "[1]", an ordinal
    stays joined ("5th"), a trademark sign goes, anything else is a word of its own.  Glued on as it is in the markup,
    the mark of footnote 1 behind "$3" reads as thirty-one dollars."""
    text = _clean(node.get_text(" "))
    if not text or _TRADEMARK.fullmatch(text):
        return ""
    if _ORDINAL.fullmatch(text):
        return text
    mark = _FOOTNOTE.fullmatch(text)
    return f"[{mark.group(1)}]" if mark else f" {text} "


def _inline_text(node: Tag, keep: frozenset = frozenset(), depth: int = 0) -> str:
    """Flatten a node to one line (table cells, headings).  ``keep``: tags of SKIP_TAGS whose text counts here."""
    parts: list[str] = []

    def visit(el: Tag, level: int) -> None:
        for child in el.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                parts.append(str(child))
                continue
            if not isinstance(child, Tag):
                continue
            name = child.name
            if (name in SKIP_TAGS and name not in keep) or child.has_attr("hidden"):
                continue
            if name in ("sup", "sub"):
                parts.append(_script_text(child))
                continue
            if name in ("br", "li", "p", "div"):
                parts.append(" ")
            if level > _MAX_DEPTH:
                parts.append(" " + child.get_text(" ") + " ")
            else:
                visit(child, level + 1)

    visit(node, depth)
    return _clean("".join(parts))


def _span(cell: Tag, name: str) -> int:
    try:
        return max(1, min(int(str(cell.get(name, "1")).strip() or "1"), 500))
    except ValueError:
        return 1


_LABELLED = frozenset({"button"})  # inside a heading or a <summary> a button's text is the heading (accordions)


class _Renderer:
    def __init__(self, base_url: str, asides: bool = False):
        self.base = base_url
        self.lines: list[str] = []
        self.cur: list[str] = []
        self.heading_lines: list[tuple[int, str]] = []
        self.links: list[tuple[str, str]] = []
        self.list_depth = 0
        self.asides = asides  # the container is the article itself: an <aside> in it is a call-out, not a sidebar
        self.level = 1  # of the heading that was written last
        self.heads: dict[int, str] = {}  # index in self.lines of a table row -> the heading row of its table
        self.tabs: dict[int, str] = {}  # id() of a tab panel -> the label of its tab
        #: (level, index of the line that follows) where the reach of a tab's label ends: behind its panel
        self.closers: list[tuple[int, int]] = []

    def heading(self, level: int, text: str, real: bool = True) -> None:
        self.blank()
        if text:
            self.lines.append("#" * level + " " + text)
            self.heading_lines.append((level, text))
            if real:
                self.level = level
        self.blank()

    def tablist(self, node: Tag) -> None:
        """The labels of a set of tabs, given to the panels they open (``aria-controls`` / ``aria-labelledby``, or their
        order).  The label is all that says which of three otherwise identical price tables is the batch one - so
        where a label cannot be given to its panel, what follows is marked as "one of" them: a reader, and the check
        that asks whether a price is the standard one, must not take it for any particular one."""
        tabs = [t for t in node.find_all(attrs={"role": "tab"}) if isinstance(t, Tag)]
        labels = [_inline_text(t, _LABELLED) for t in tabs]
        if not any(labels):
            return
        scope = node.parent if isinstance(node.parent, Tag) else node
        # this set's panels: the ones that follow it, up to the next set of tabs
        marks = [m for m in scope.find_all(attrs={"role": ["tablist", "tabpanel"]}) if isinstance(m, Tag)]
        start = next((i for i, m in enumerate(marks) if m is node), -1)
        panels: list[Tag] = []
        for m in marks[start + 1:]:
            if m.get("role") == "tablist":
                break
            panels.append(m)
        everywhere = {p.get("id"): p for p in scope.find_all(attrs={"role": "tabpanel"}) if isinstance(p, Tag) and p.get("id")}
        by_label = {tab.get("id"): label for tab, label in zip(tabs, labels) if tab.get("id")}
        named: dict[int, str] = {}
        for tab, label in zip(tabs, labels):
            panel = everywhere.get(tab.get("aria-controls"))
            if panel is not None and label:
                named[id(panel)] = label
        for panel in panels:
            label = by_label.get(panel.get("aria-labelledby"))
            if label:
                named.setdefault(id(panel), label)
        if not named and len(panels) == len(tabs):
            named = {id(panel): label for panel, label in zip(panels, labels) if label}
        self.tabs.update(named)
        some = "one of: " + " · ".join(x for x in labels if x)
        left = [p for p in panels if id(p) not in named]
        for panel in left:
            self.tabs[id(panel)] = some
        if not named and not left:
            # no panel is marked at all (the page shows one view and swaps it by script): whatever follows is one of them
            self.heading(min(self.level + 1, 6), f"{some} [tab]", real=False)

    # -- line buffer ------------------------------------------------------
    def flush(self) -> None:
        text = _clean("".join(self.cur))
        self.cur = []
        if text:
            self.lines.append(text)

    def blank(self) -> None:
        self.flush()
        if self.lines and self.lines[-1] != "":
            self.lines.append("")

    # -- walkers ----------------------------------------------------------
    def walk(self, node, depth: int = 0) -> None:
        if depth > _MAX_DEPTH:
            self.cur.append(" " + _clean(node.get_text(" ")) + " ")
            return
        for child in node.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                self.cur.append(_WS.sub(" ", str(child)))
                continue
            if not isinstance(child, Tag):
                continue
            name = child.name
            if child.get("role") == "tablist":
                self.tablist(child)
                continue
            if name == "aside" and self.asides and not _CHROME.search(" ".join([*(child.get("class") or []), str(child.get("id") or ""), str(child.get("role") or "")])):
                self.flush()
                self.walk(child, depth + 1)
                self.flush()
                continue
            if name in SKIP_TAGS or child.has_attr("hidden"):
                continue
            if name in ("sup", "sub"):
                self.cur.append(_script_text(child))
                continue
            under_tab = None
            if id(child) in self.tabs:
                # a level below the heading the tabs stand under: what the panel holds is under its label - and what
                # follows the panel is not (see the end of this loop)
                under_tab = min(self.level + 1, 6)
                self.heading(under_tab, f"{self.tabs[id(child)]} [tab]", real=False)
            if name == "table":
                self.table(child)
            elif name == "pre":
                self.blank()
                for ln in child.get_text().splitlines():
                    self.lines.append(ln.rstrip())
                self.blank()
            elif name in HEADINGS:
                self.heading(HEADINGS[name], _inline_text(child, _LABELLED))
            elif name == "summary":
                self.flush()
                self.cur.append(_inline_text(child, _LABELLED))
                self.flush()
            elif name == "br":
                self.flush()
            elif name in ("ul", "ol"):
                self.flush()
                self.list_depth += 1
                self.walk(child, depth + 1)
                self.list_depth -= 1
                self.flush()
            elif name == "li":
                self.flush()
                self.cur.append("  " * max(self.list_depth - 1, 0) + "- ")
                self.walk(child, depth + 1)
                self.flush()
            elif name == "a":
                href = child.get("href", "")
                text = _inline_text(child)
                if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    absolute = urljoin(self.base, href)
                    if urlparse(absolute).scheme in ("http", "https") and text:
                        self.links.append((text[:160], absolute.split("#")[0]))
                self.walk(child, depth + 1)
            elif name in BLOCK_TAGS:
                self.flush()
                self.walk(child, depth + 1)
                self.flush()
            else:
                self.walk(child, depth + 1)
            if under_tab is not None:
                self.blank()
                self.closers.append((under_tab, len(self.lines)))

    def table(self, tbl: Tag) -> None:
        self.blank()
        caption = tbl.find("caption")
        if caption is not None:
            text = _inline_text(caption)
            if text:
                self.lines.append(text)
        head: str | None = None  # the line of column headings: the last all-<th> row before the first row of values
        body = False
        spanning: dict[int, list] = {}  # column -> [text, rows it still covers]: a cell with rowspan
        for tr in tbl.find_all("tr"):
            # skip rows of tables nested inside a cell: those are flattened by _inline_text
            if tr.find_parent("table") is not tbl:
                continue
            tags = tr.find_all(["th", "td"], recursive=False)
            cells: list[str] = []
            column = 0
            for tag in tags:
                while column in spanning:  # a cell of an earlier row reaches down into this one
                    cells.append(spanning[column][0])
                    spanning[column][1] -= 1
                    if spanning[column][1] <= 0:
                        del spanning[column]
                    column += 1
                text = _inline_text(tag)
                cells.append(text)
                if _span(tag, "rowspan") > 1 and _span(tag, "colspan") == 1:
                    spanning[column] = [text, _span(tag, "rowspan") - 1]
                column += _span(tag, "colspan")
            while column in spanning:
                cells.append(spanning[column][0])
                spanning[column][1] -= 1
                if spanning[column][1] <= 0:
                    del spanning[column]
                column += 1
            if not any(cells):
                continue
            line = " | ".join(cells)
            heading_row = bool(tags) and all(t.name == "th" for t in tags)
            self.lines.append(line)
            if heading_row and len(tags) > 1 and (not body or (head is not None and len(tags) == head.count(" | ") + 1)):
                head, body = line, False
            elif not heading_row or len(tags) > 1:
                body = True
                if head is not None:
                    self.heads[len(self.lines) - 1] = head
        self.blank()


def _pick_container(soup: BeautifulSoup) -> Tag:
    body = soup.body or soup
    body_len = len(body.get_text(" ", strip=True))
    for selector in ("main", "[role=main]", "article", "#main-col-body", "#content", ".content"):
        found = soup.select_one(selector)
        if found is not None:
            if len(found.get_text(" ", strip=True)) >= max(200, body_len * 0.25):
                return found
    return body


def _omits_end_tags(html: str) -> bool:
    """Minified pages (Docusaurus and friends) leave out </td></tr></li>, which HTML allows.

    ``html.parser`` does not know the optional-end-tag rules: it nests the cells and the whole table collapses into one line.
    """
    low = html.lower()
    for tag in ("td", "th", "tr", "li"):
        opens = len(re.findall(rf"<{tag}[\s>/]", low))
        if opens >= 4 and low.count(f"</{tag}>") < opens * 0.5:
            return True
    return False


_TAG = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)")
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
#: elements whose end tag HTML lets an author leave out (the next sibling closes them): they do not nest by being unclosed
_SELF_CLOSING = {"li", "td", "th", "tr", "p", "dt", "dd", "option", "optgroup", "thead", "tbody", "tfoot", "colgroup", "caption", "rb", "rt", "rp"}
MAX_HTML5_DEPTH = 250


def _too_deep(html: str, limit: int = MAX_HTML5_DEPTH) -> bool:
    """Does the markup open more than ``limit`` elements inside one another (counting what is never closed)?

    The HTML5 tree builder is written in Python and its cost grows with the square of the nesting - thousands of
    unclosed ``<b>`` or ``<ul>`` keep it busy for minutes.  Real pages nest a few dozen levels.
    """
    depth = 0
    for m in _TAG.finditer(html):
        name = m.group(2).lower()
        if name in _VOID or name in _SELF_CLOSING:
            continue
        if m.group(1):
            depth = max(depth - 1, 0)
        else:
            depth += 1
            if depth > limit:
                return True
    return False


def _make_soup(html: str) -> BeautifulSoup:
    """Fast parser for ordinary pages; the HTML5 algorithm (pure Python ``html5lib``) only for pages that omit end tags -
    and not for markup nested so deeply that building its tree would take minutes (``_too_deep``)."""
    if _omits_end_tags(html) and not _too_deep(html):
        try:
            return BeautifulSoup(html, "html5lib")
        except FeatureNotFound:  # optional dependency missing: degrade to the fast parser instead of failing the fetch
            pass
    return BeautifulSoup(html, "html.parser")


def parse_html(html: str, base_url: str) -> ParsedPage:
    soup = _make_soup(html)
    title = ""
    if soup.title and soup.title.string:
        title = _clean(soup.title.string)
    container = _pick_container(soup)
    r = _Renderer(base_url, asides=container.name in ("main", "article") or container.get("role") == "main")
    r.walk(container)
    r.flush()
    lines: list[str] = []
    row_heads: dict[int, str] = {}
    kept_at: list[int] = []  # for each line the renderer wrote: the number of the line it became (or of the next one that was kept)
    blank = 0
    for i, ln in enumerate(r.lines):
        kept_at.append(len(lines))
        if ln == "":
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        if i in r.heads:
            row_heads[len(lines)] = r.heads[i]
        lines.append(ln)
    while lines and lines[-1] == "":
        lines.pop()
    text = "\n".join(lines)
    stripped = len(text) - len(text.lstrip(" \t"))
    if stripped:  # (a <pre> that begins with blanks, as the first thing on the page)
        text = text.lstrip(" \t")
    if not title:
        h1 = soup.find("h1")
        title = _clean(h1.get_text(" ")) if h1 else ""
    headings: list[tuple[int, str, int]] = []
    pos = 0
    for level, htext in r.heading_lines:
        needle = "#" * level + " " + htext
        idx = text.find(needle, pos)
        if idx >= 0:
            headings.append((level, htext, idx))
            pos = idx + len(needle)
    if r.closers:
        # where the label of a tab stops applying: a heading without words, at the first line behind the panel (it takes
        # the label off the stack of headings a quote stands under, and is itself nothing to read)
        starts = [0]
        for ln in lines:
            starts.append(starts[-1] + len(ln) + 1)
        for level, index in r.closers:
            number = kept_at[index] if index < len(kept_at) else len(lines)
            at = max(0, (starts[number] if number < len(starts) else len(text) + 1) - stripped)
            headings.append((level, "", at))
        headings.sort(key=lambda h: (h[2], h[1] != ""))
    seen: set[str] = set()
    links = []
    for t, u in r.links:
        if u not in seen:
            seen.add(u)
            links.append((t, u))
    return ParsedPage(title=title, text=text, kind="html", headings=headings, links=links, row_heads=row_heads)


def parse_feed(xml: str, base_url: str) -> ParsedPage:
    with warnings.catch_warnings():  # html.parser on purpose: pure Python, no lxml needed in the runtime image
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(xml, "html.parser")
    entries = soup.find_all(["entry", "item"])
    lines: list[str] = []
    links: list[tuple[str, str]] = []
    title_tag = soup.find(["title"])
    title = _clean(title_tag.get_text(" ")) if title_tag else "feed"
    for e in entries[:200]:
        t = e.find("title")
        d = e.find(["updated", "published", "pubdate", "dc:date"])
        lk = e.find("link")
        href = ""
        if lk is not None:
            href = lk.get("href") or _clean(lk.get_text(" "))
        s = e.find(["summary", "description", "content"])
        snippet = _clean(BeautifulSoup(s.get_text(" "), "html.parser").get_text(" "))[:300] if s else ""
        row = f"- {_clean(d.get_text(' ')) if d else ''} | {_clean(t.get_text(' ')) if t else ''} | {href}"
        lines.append(row)
        if snippet:
            lines.append("  " + snippet)
        if href:
            links.append(((_clean(t.get_text(" ")) if t else href)[:160], urljoin(base_url, href)))
    return ParsedPage(title=title, text="\n".join(lines), kind="xml", links=links)


def decode(body: bytes, content_type: str) -> str:
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    for enc in ([m.group(1)] if m else []) + ["utf-8"]:
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def parse_body(body: bytes, content_type: str, url: str) -> ParsedPage:
    """The page as text the agent can quote (no half surrogate pairs in any of it: see ``grounding.whole``)."""
    page = _parse_body(body, content_type, url)
    return ParsedPage(title=whole(page.title), text=whole(page.text), kind=page.kind,
                      headings=[(level, whole(text), offset) for level, text, offset in page.headings],
                      links=[(whole(text), whole(href)) for text, href in page.links],
                      row_heads={n: whole(head) for n, head in page.row_heads.items()})


_ATX = re.compile(r"(#{1,6})[ \t]+(\S.*?)[ \t]*#*[ \t]*")
_FENCE = re.compile(r"\s{0,3}(```|~~~)")
_MD_RULE = re.compile(r"\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*")


def parse_text(text: str) -> ParsedPage:
    """A plain-text or Markdown body: the text as it is, with the headings a Markdown copy of a documentation page
    is written with ("## Claude Sonnet 4.5") and the heading rows of its tables.  The title is the first top-level
    heading or nothing - never the address, which says what was asked for and not what came back."""
    headings: list[tuple[int, str, int]] = []
    row_heads: dict[int, str] = {}
    lines = text.split("\n")
    offset, fenced, head = 0, False, None
    for n, line in enumerate(lines):
        if _FENCE.match(line):
            fenced, head = not fenced, None
        elif not fenced:
            m = _ATX.fullmatch(line.rstrip("\r"))
            if m:
                headings.append((len(m.group(1)), _clean(m.group(2)), offset))
                head = None
            elif "|" in line and n + 1 < len(lines) and "-" in lines[n + 1] and _MD_RULE.fullmatch(lines[n + 1].rstrip("\r")):
                head = line.strip()  # the row above a |---|---| rule
            elif head is not None and "|" in line:
                if not _MD_RULE.fullmatch(line.rstrip("\r")):
                    row_heads[n] = head
            else:
                head = None
        offset += len(line) + 1
    title = next((text for level, text, _at in headings if level == 1), "")
    return ParsedPage(title=title, text=text, kind="text", headings=headings, row_heads=row_heads)


def _parse_body(body: bytes, content_type: str, url: str) -> ParsedPage:
    ctype = (content_type or "").split(";")[0].strip().lower()
    text = decode(body, content_type)
    if ctype in ("application/json", "application/ld+json") or ctype.endswith("+json"):
        try:
            pretty = json.dumps(json.loads(text), indent=1, ensure_ascii=False)
            return ParsedPage(title="", text=pretty, kind="json")
        except json.JSONDecodeError:
            return ParsedPage(title="", text=text, kind="text")
    if ctype in ("application/atom+xml", "application/rss+xml", "application/xml", "text/xml") or ctype.endswith("+xml") and "html" not in ctype:
        return parse_feed(text, url)
    if ctype in ("text/plain", "text/markdown", "text/x-markdown", "text/csv"):
        return parse_text(text) if ctype != "text/csv" else ParsedPage(title="", text=text, kind="text")
    return parse_html(text, url)
