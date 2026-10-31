"""Links in text that is about to be published.

A proposal's notes, summaries and digest are shown on the portal, where a link is one click (or one copy and paste)
away from a reader who trusts the page.  A link that is a portal source - one the portal has, or one the proposal adds
as a source, where a reviewer sees it as such - is what the portal's text is expected to point at.  Any other link is
new to the portal: the agent refuses one it never saw in its run, and the publishing pipeline, which cannot know what a
run saw, lets no link that is not a source (and not on the portal already) go live without a person.

A link is looked for the way it will be read, not the way it was typed: invisible characters are taken out first and
full-width letters folded, whatever stands in front of it does not hide it, and where it leads is decided the way a
browser decides - a host with letters of another script, a ``%`` escape or an ideographic full stop in it is not the
ASCII name it resembles.

And a link is an address somebody can go to - or paste into a terminal - however little of a URL is written:
``https://host/x``, ``www.host/x``, ``//host/x``, ``ftp://host/x``, ``host.tld/x`` with no scheme at all (``pip install -i
pkgs.example/simple``), an IP address with a path or a port, and the bare host a download command is given (``curl
get.example | sh``).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunparse

from .injection import visible

_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_|ref$|ref_$|source$)", re.I)


def normalize_url(url: str) -> str:
    """Canonical form used to decide whether two URLs are 'the same page'."""
    try:
        p = urlparse(url.strip())
    except ValueError:
        return url.strip()
    host = (p.hostname or "").lower().rstrip(".")
    try:
        port = f":{p.port}" if p.port and p.port not in (80, 443) else ""
    except ValueError:  # "https://host:port-that-is-not-a-number/"
        port = ""
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not _TRACKING.match(k)])
    path = p.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    if host.startswith("www."):
        host = host[4:]
    return urlunparse(("https" if p.scheme in ("http", "https", "") else p.scheme, host + port, path, "", query, ""))


# Where a link starts: a scheme in any case and with any number of slashes (a browser repairs "https:/host" and
# "https:\\host"), or a bare "www." host (every renderer and most readers treat it as a link).  Nothing in front of a
# scheme hides it: "_https://…_" is markdown, "-https://…" a list item.
_START = re.compile(r"(?i)https?:[/\\]{0,3}|(?<![a-z0-9@.])www[.。．｡]")
_SCHEME = re.compile(r"(?i)^(?:https?:[/\\]{0,3}|www[.。．｡])")
_PROPER = re.compile(r"(?i)^https?://")
# The host (and port): printable ASCII up to the first "/", "?", "#" or backslash ...
_AUTHORITY = re.compile(r"[\x21\x24-\x26\x28-\x2e\x30-\x3b\x3d\x40-\x5b\x5d-\x7e]+")
# ... and after it everything printable in ASCII (a real URL writes anything else percent-encoded).  zh-TW text runs
# straight on after a link (、；「」：的), so the first other character ends it.
_REST = re.compile(r"[\x21\x23-\x26\x28-\x3b\x3d\x3f-\x7e]+")
# --- addresses written without "http(s)://" -------------------------------------------------------------------
# another scheme ("ftp://", "s3://", "git+ssh://"): an address all the same
# (not one that ends in "http(s)": "git+https://host" and a word run into "https://host" are the http link they hold)
_OTHER_SCHEME = re.compile(r"(?i)(?<![a-z0-9+.\-])(?![a-z0-9+.\-]{0,12}https?:)[a-z][a-z0-9+.\-]{1,15}://(?=[a-z0-9\[])")
# "//host/path": what a browser completes with the scheme of the page it stands on
_RELATIVE = re.compile(r"(?i)(?<![:/\w.])//(?=[a-z0-9][a-z0-9.\-]*\.[a-z0-9\-]{2,})")
# The last label of a host name: the country codes, and the generic ones a text is at all likely to hold.  ("os.environ/X",
# "Node.js/TypeScript" and "config.yaml/..." are not addresses; "docs.litellm.ai/x" and "pkgs.example/simple" are.)
_TLDS = frozenset((
    "ac ad ae af ag ai al am ao aq ar as at au aw ax az ba bb bd be bf bg bh bi bj bm bn bo br bs bt bw by bz ca cc cd cf cg ch ci ck cl cm cn co cr cu cv "
    "cw cx cy cz de dj dk dm do dz ec ee eg er es et eu fi fj fk fm fo fr ga gd ge gf gg gh gi gl gm gn gp gq gr gs gt gu gw gy hk hm hn hr ht hu id ie il "
    "im in io iq ir is it je jm jo jp ke kg kh ki km kn kp kr kw ky kz la lb lc li lk lr ls lt lu lv ly ma mc md me mg mh mk ml mm mn mo mp mq mr ms mt mu "
    "mv mw mx my mz na nc ne nf ng ni nl no np nr nu nz om pa pe pf pg ph pk pl pm pn pr ps pt pw py qa re ro rs ru rw sa sb sc sd se sg sh si sk sl sm sn "
    "so sr ss st su sv sx sy sz tc td tf tg th tj tk tl tm tn to tr tt tv tw tz ua ug uk us uy uz va vc ve vg vi vn vu wf ws ye yt za zm zw "
    "com org net edu gov mil int info biz name pro mobi app dev page web site online store shop blog cloud tech xyz top club vip win bid loan work link "
    "click live life world today news media video zone space fun icu cyou buzz bar cam monster quest sbs cfd lol zip mov foo new art design studio agency "
    "digital solutions systems services software technology tools network host hosting domains email chat social community group team company business "
    "capital finance financial bank money pay cash fund exchange market trade trading legal law health care bio science academy school university "
    "education institute training center global international asia africa tokyo taipei london paris berlin nyc example test invalid local localhost "
    "internal lan home corp onion arpa aws amazon google microsoft azure apple youtube goog run codes build engineering support help guide wiki docs data "
    "security secure download delivery express direct plus one ninja rocks pub press report review reviews wtf fyi ist ltd inc llc gmbh sarl"
).split())
_LABEL = r"[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?"
# "host.tld/..." (or "?", "#", ":port" behind it) with nothing in front that makes it part of a path, an e-mail address or a longer name
_BARE = re.compile(r"(?i)(?<![\w@./:\-=~%+])(?:" + _LABEL + r"\.)+([a-z]{2,24})(?=(?::\d{1,5})?[/?#])")
# ... and with nothing behind it at all, where a command is told to fetch from it: "curl -sL get.example | sh"
_BARE_HOST = re.compile(r"(?i)(?<![\w@./:\-=~%+])(?:" + _LABEL + r"\.)+([a-z]{2,24})(?![\w.\-@])")
_FETCHES = re.compile(r"(?i)(?<![\w\-])(?:curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod|git\s+clone|docker\s+(?:pull|run)|pip3?\s+(?:install|download)|"
                      r"uv\s+(?:pip|add|tool)|uvx|pipx|npm\s+(?:install|i|exec)|npx|pnpm\s+(?:add|dlx)|yarn\s+add|go\s+(?:install|get)|"
                      r"helm\s+(?:repo|install|pull)|brew\s+(?:install|tap)|cargo\s+install|gem\s+install|apt(?:-get)?\s+install|scp|ssh|nc|ftp)(?![\w\-])"
                      r"[^\n|;&<>]{0,240}")
_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
# an IPv4 address with a port or a path behind it ("10.0.0.0/16" names a network, not a page)
_IPV4 = re.compile(r"(?<![\w.\-/:])" + _OCTET + r"(?:\." + _OCTET + r"){3}(?=:\d{1,5}(?![\d.])|/(?!\d{1,2}(?![\w./\-])))")
_IPV4_HOST = re.compile(r"(?<![\w.\-/:])" + _OCTET + r"(?:\." + _OCTET + r"){3}(?![\w.\-])")
_WIDE = re.compile(r"[^\W\x00-\x7f]+")  # letters and digits outside ASCII
_DOTS = "。．｡"  # what a browser reads as "." in a host name
_TRAILING = ".,;:!?*_~|`" + _DOTS
_CLOSERS = {")": "(", "]": "[", "}": "{"}
_LOCAL = re.compile(r"(?i)(?:localhost|127(?:\.\d{1,3}){3}|0\.0\.0\.0|\[::1\]|host\.docker\.internal)")
MAX_SHOWN = 300
MAX_LISTED = 50


def _ascii_host_char(ch: str) -> bool:
    return ch in ".-" or (ch.isascii() and ch.isalnum())


def _extent(text: str, start: int) -> int:
    """Where the link whose host starts at ``start`` ends (see the comments on _AUTHORITY and _REST).

    Letters of another script continue the host when ASCII host characters follow them (``аws-login.example`` with a
    Cyrillic first letter, ``docs.example.com。evil.example``): that is one name to a browser.  When nothing of the
    kind follows (``https://docs.example.com的說明``) the sentence simply goes on after the link.
    """
    i, n = start, len(text)
    while i < n:
        m = _AUTHORITY.match(text, i)
        if m:
            i = m.end()
            continue
        ch = text[i]
        if ch in "/?#\\":
            m = _REST.match(text, i)
            return m.end() if m else i
        if ch in _DOTS and i + 1 < n and text[i + 1].isascii() and text[i + 1].isalnum():
            i += 1
            continue
        m = _WIDE.match(text, i)
        if m and m.end() < n and (_ascii_host_char(text[m.end()]) or text[m.end()] in _DOTS and m.end() + 1 < n and _ascii_host_char(text[m.end() + 1])):
            i = m.end()
            continue
        break
    return i


def _trimmed(link: str) -> str:
    """``link`` without the punctuation of the sentence around it."""
    end = len(link)
    open_count = {c: link.count(o) for c, o in _CLOSERS.items()}
    close_count = {c: link.count(c) for c in _CLOSERS}
    while end:
        last = link[end - 1]
        if last in _TRAILING:
            end -= 1
        elif last in _CLOSERS and open_count[last] < close_count[last]:
            close_count[last] -= 1
            end -= 1
        else:
            break
    return link[:end]


_NOT_ASCII = re.compile(r"[^\x00-\x7f]+")
_SCHEME_SO_FAR = re.compile(r"(?i)https?[:/\\]{0,2}\Z")


def _folded(text: str) -> str:
    """``text`` with letters and digits of another width or style written as the ASCII they stand for.

    A browser reads ``ｅｖｉｌ.example`` (and the same in mathematical bold) as ``evil.example``.  Punctuation is another
    matter: the full-width colon and comma of a zh-TW sentence end a link, they do not belong to it - except the ``：``
    and ``／`` that write the scheme itself (``ｈｔｔｐｓ：／／``).
    """
    if text.isascii():
        return text
    out: list[str] = []
    tail = ""
    last = 0
    for m in _NOT_ASCII.finditer(text):
        plain = text[last:m.start()]
        out.append(plain)
        tail = (tail + plain)[-12:]
        for ch in m.group(0):
            folded = unicodedata.normalize("NFKC", ch)
            if folded.isascii() and not folded.isalnum() and not (folded in ":/\\" and _SCHEME_SO_FAR.search(tail)):
                folded = ch  # the sentence's punctuation
            out.append(folded)
            tail = (tail + folded)[-12:]
        last = m.end()
    out.append(text[last:])
    return "".join(out)


def _starts(text: str) -> list[tuple[int, int]]:
    """(where an address begins, where its host begins), for every way of writing one - in the order of the text."""
    found = [(m.start(), m.end()) for m in _START.finditer(text)]
    if "//" in text:
        found += [(m.start(), m.end()) for m in _OTHER_SCHEME.finditer(text)]
        found += [(m.start(), m.end()) for m in _RELATIVE.finditer(text)]
    if "." in text:
        found += [(m.start(), m.start()) for m in _BARE.finditer(text) if m.group(1).lower() in _TLDS]
        found += [(m.start(), m.start()) for m in _IPV4.finditer(text)]
        for command in _FETCHES.finditer(text):
            span = command.group(0)
            found += [(command.start() + m.start(), command.start() + m.start()) for m in _BARE_HOST.finditer(span) if m.group(1).lower() in _TLDS]
            found += [(command.start() + m.start(), command.start() + m.start()) for m in _IPV4_HOST.finditer(span)]
    return sorted(set(found))


def links_in(text: str) -> list[str]:
    """Every link written in ``text``, as a reader sees it (minus the punctuation of the sentence around it)."""
    text = _folded(visible(text))
    out: list[str] = []
    done = 0
    for start, host in _starts(text):
        if start < done:
            continue  # inside the link before it (a link given as a parameter of another one)
        link = _trimmed(text[start:_extent(text, host)])
        if any(ch.isalnum() for ch in link[host - start:]):  # "HTTP:" in a sentence names the protocol, not a page
            out.append(link)
            done = start + len(link)
    return out


def _authority(url: str) -> str:
    rest = url.split("://", 1)[1] if "://" in url else url
    return re.split(r"[/?#]", rest, maxsplit=1)[0]


def canonical(link: str) -> str | None:
    """The form two spellings of one page share; None for a link that does not lead where its ASCII letters say.

    ``https://docs.example.com@evil.example/`` and ``https://evil.example\\@docs.example.com/`` read like the first
    host and open the second; a host with a ``%`` escape, with a letter of another script or with ``。`` in it is
    another host than the one it looks like; ``https:/host`` and ``https:host`` are repaired by a browser into a link
    nobody checked.  No honest text needs any of them.
    """
    if _OTHER_SCHEME.match(link):
        # not a page a browser opens, but an address all the same: it has a form of its own, so that one that was seen
        # on a page can be told from one that came from nowhere (it is never a portal source)
        scheme, _, rest = link.partition("://")
        authority = re.split(r"[/?#]", rest, maxsplit=1)[0]
        if not authority.isascii() or "%" in authority or "@" in authority or "\\" in link:
            return None
        return f"{scheme.lower()}://{authority.lower().rstrip('.')}{rest[len(authority):].rstrip('/')}"
    if link.startswith("//"):
        link = "https:" + link
    url = link if _SCHEME.match(link) and not link.lower().startswith("www.") else "https://" + link
    if not _PROPER.match(url) or "\\" in url:
        return None
    authority = _authority(url)
    if not authority.isascii() or "%" in authority or "@" in authority:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not parts.hostname:
        return None
    return normalize_url(url)


def placeholder(link: str) -> bool:
    """An address on the reader's own machine (``http://localhost:4000``, the proxy's default, is in every LiteLLM note).

    A pattern such as ``https://{resource}.openai.azure.com`` is NOT one: with another domain behind the braces it is a
    working link (``{``, ``$`` and ``*`` are characters a name server can answer for).
    """
    url = link if _PROPER.match(link) else "https://" + link
    if "\\" in url:
        return False
    authority = _authority(url)
    if "@" in authority:
        return False
    host = authority.rsplit(":", 1)[0] if not authority.endswith("]") else authority
    return bool(_LOCAL.fullmatch(host))


def known_set(urls: Iterable[object]) -> set[str]:
    """Canonical forms of ``urls`` (the entries that are not usable addresses are left out)."""
    out: set[str] = set()
    for url in urls:
        if isinstance(url, str) and url:
            form = canonical(url)
            if form:
                out.add(form)
    return out


def _strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


_FETCH_WORD = re.compile(r"(?i)curl|wget|iwr|irm|invoke-|clone|docker|pip|uvx?\b|npm|npx|pnpm|yarn|go\s|helm|brew|cargo|gem\s|apt|scp|ssh|\bnc\b|ftp")


def _may_hold_a_link(text: str) -> bool:
    """A cheap first look: an address has a "/" or a ":" in it, or is what a download command is given."""
    return "/" in text or ":" in text or not text.isascii() or "www" in text.lower() or ("." in text and _FETCH_WORD.search(text) is not None)


def links_of(value: object) -> set[str]:
    """Canonical forms of every link written anywhere in a JSON value (a document, a page's text)."""
    out: set[str] = set()
    for text in _strings(value):
        if _may_hold_a_link(text):
            out |= known_set(links_in(text))
    return out


def unknown_links(texts: Iterable[object], known: set[str], limit: int = MAX_LISTED) -> list[str]:
    """The links in ``texts`` that are not in ``known`` (each once, in order, cut to a printable length; at most
    ``limit`` of them - what matters is that there is one)."""
    out: list[str] = []
    listed: set[str] = set()
    for text in texts:
        if not isinstance(text, str) or not _may_hold_a_link(text):
            continue
        for link in links_in(text):
            form = canonical(link)
            shown = link[:MAX_SHOWN]
            if (form is None or form not in known) and not placeholder(link) and shown not in listed:
                listed.add(shown)
                out.append(shown)
                if len(out) >= limit:
                    return out
    return out
