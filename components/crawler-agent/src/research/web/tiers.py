"""Domain -> trust tier.

The portal records, for every fact, a ``verification`` level and, for every source, a
``type``.  The agent must not be able to label a blog post as an official document, so
both are derived from the *domain the evidence was actually fetched from*, never from
what the model says.

Tiers (highest first): official > independent > community.

A rule names the pages a vendor (or an advisory database, or a newsroom) writes itself.  Wherever anybody can post -
issues and discussions, forums, marketplaces, package pages of other projects - the page is ``community``, also on a
vendor's own domain.  A page the portal already cites keeps the tier of its recorded type, but that says nothing about
the rest of its host.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse

from ..validate.urls import normalize_url  # noqa: F401  (defined with the link checks the publishing pipeline shares; used from here by the agent)

RANK = {"official": 3, "independent": 2, "community": 1}

#: Portal ``Source.type`` values that each verification tier may carry.
ALLOWED_TYPES = {
    "official": {"vendor-doc", "csp-doc", "vendor-blog", "csp-blog", "regulator", "github", "security-advisory"},
    "independent": {"benchmark", "news", "security-advisory", "github"},
    "community": {"community", "github", "news", "benchmark"},
}

#: source types that are *inherently* of a tier (used when learning from sources.json)
_TYPE_TIER = {
    "vendor-doc": "official",
    "csp-doc": "official",
    "vendor-blog": "official",
    "csp-blog": "official",
    "regulator": "official",
    "security-advisory": "independent",
    "benchmark": "independent",
    "news": "independent",
    "community": "community",
    "github": "community",
}


@dataclass(frozen=True)
class Tier:
    verification: str  # official | independent | community
    source_type: str  # portal Source.type
    publisher: str

    @property
    def rank(self) -> int:
        return RANK[self.verification]


# (host, path-regex or None, verification, source_type, publisher).  First match wins, so more
# specific entries (sub-domains, path prefixes) must come before general ones.  A path rule ends at a path boundary:
# "/releases" is not "/releases-of-my-fork"; it is compared without regard to case (a carve-out must not be escaped by
# writing "/Marketplace").
#
# A host is the host that is written (and "www." in front of it) - NOT its sub-domains: test.pypi.org is another index
# than pypi.org, forums.macrumors.com is not MacRumors' newsroom and community.zscaler.com not Zscaler's research blog.
# "*.host" covers the sub-domains as well, for the few domains whose sub-domains are all the owner's own sites (and even
# there not one that is named like a place where anybody posts: see _OPEN_LABELS).
_RULES: list[tuple[str, str | None, str, str, str]] = [
    # --- GitHub: only releases/security pages of the official repo count as official
    ("github.com", r"^/BerriAI/litellm/(releases|security|tags)(/|$)", "official", "github", "GitHub / BerriAI"),
    ("github.com", r"^/BerriAI/litellm(/|$)", "community", "github", "GitHub / BerriAI"),
    ("github.com", r"^/advisories/GHSA-[A-Za-z0-9-]+/?$", "independent", "security-advisory", "GitHub Advisory Database"),
    ("github.com", None, "community", "github", "GitHub"),
    ("raw.githubusercontent.com", None, "community", "github", "GitHub"),
    # --- AWS (the marketplace is written by its sellers)
    ("docs.aws.amazon.com", None, "official", "csp-doc", "AWS"),
    ("aws.amazon.com", r"^/(?:[a-z]{2}(?:-[a-z]{2})?/)?marketplace(/|$)", "community", "community", "AWS Marketplace (seller listing)"),
    ("aws.amazon.com", r"^/(blogs|about-aws/whats-new|new|jp/blogs|tw/blogs)(/|$)", "official", "csp-blog", "AWS"),
    ("aws.amazon.com", None, "official", "csp-doc", "AWS"),
    # --- Microsoft (Q&A and the Tech Community's discussion boards are written by anybody; its blogs by Microsoft)
    ("learn.microsoft.com", r"^/(?:[a-z]{2,3}(?:-[a-z0-9]{2,4})?/)?answers(/|$)", "community", "community", "Microsoft Q&A"),
    ("learn.microsoft.com", None, "official", "csp-doc", "Microsoft"),
    ("azure.microsoft.com", None, "official", "csp-blog", "Microsoft"),
    ("techcommunity.microsoft.com", r"^/(blog/|t5/[^/]+/[^/]+/ba-p/)", "official", "csp-blog", "Microsoft Tech Community"),
    ("techcommunity.microsoft.com", None, "community", "community", "Microsoft Tech Community (discussion)"),
    # --- Google
    ("discuss.ai.google.dev", None, "community", "community", "Google AI Developers Forum"),
    ("ai.google.dev", None, "official", "vendor-doc", "Google"),
    ("docs.cloud.google.com", None, "official", "csp-doc", "Google Cloud"),
    ("cloud.google.com", r"^/blog/", "official", "csp-blog", "Google Cloud"),
    ("cloud.google.com", None, "official", "csp-doc", "Google Cloud"),
    ("blog.google", None, "official", "vendor-blog", "Google"),
    ("deepmind.google", None, "official", "vendor-blog", "Google DeepMind"),
    # --- Anthropic
    ("platform.claude.com", None, "official", "vendor-doc", "Anthropic"),
    ("docs.claude.com", None, "official", "vendor-doc", "Anthropic"),
    ("docs.anthropic.com", None, "official", "vendor-doc", "Anthropic"),
    ("*.anthropic.com", None, "official", "vendor-blog", "Anthropic"),
    ("*.claude.com", None, "official", "vendor-blog", "Anthropic"),
    # --- OpenAI
    ("developers.openai.com", None, "official", "vendor-doc", "OpenAI"),
    ("platform.openai.com", None, "official", "vendor-doc", "OpenAI"),
    ("deploymentsafety.openai.com", None, "official", "vendor-doc", "OpenAI"),
    ("community.openai.com", None, "community", "community", "OpenAI Developer Community"),
    ("chat.openai.com", None, "community", "community", "ChatGPT (shared conversation)"),
    ("*.openai.com", None, "official", "vendor-blog", "OpenAI"),
    # --- LiteLLM / PyPI / NVIDIA (PyPI: the litellm project's own page; any other project is somebody else's)
    ("docs.litellm.ai", r"^/(blog|release_notes)(/|$)", "official", "vendor-blog", "LiteLLM"),
    ("docs.litellm.ai", None, "official", "vendor-doc", "LiteLLM"),
    ("litellm.ai", None, "official", "vendor-blog", "LiteLLM"),
    ("pypi.org", r"^/(project|pypi)/litellm(/|$)", "official", "vendor-doc", "PyPI"),
    ("pypi.org", None, "community", "community", "PyPI"),
    ("docs.nvidia.com", None, "official", "vendor-doc", "NVIDIA"),
    # --- regulators
    ("*.moda.gov.tw", None, "official", "regulator", "數位發展部"),
    ("*.fsc.gov.tw", None, "official", "regulator", "金融監督管理委員會"),
    ("aiec.org.tw", None, "official", "regulator", "AIEC"),
    ("law.moj.gov.tw", None, "official", "regulator", "全國法規資料庫"),
    ("eur-lex.europa.eu", None, "official", "regulator", "EUR-Lex"),
    ("digital-strategy.ec.europa.eu", None, "official", "regulator", "European Commission"),
    # --- independent: benchmarks
    ("artificialanalysis.ai", None, "independent", "benchmark", "Artificial Analysis"),
    ("vals.ai", None, "independent", "benchmark", "Vals AI"),
    ("lmarena.ai", None, "independent", "benchmark", "LMArena"),
    # --- independent: security advisories
    ("advisories.gitlab.com", None, "independent", "security-advisory", "GitLab Advisory Database"),
    ("nvd.nist.gov", None, "independent", "security-advisory", "NVD"),
    ("cve.org", None, "independent", "security-advisory", "CVE"),
    ("osv.dev", None, "independent", "security-advisory", "OSV"),
    ("sysdig.com", None, "independent", "security-advisory", "Sysdig"),
    ("zscaler.com", None, "independent", "security-advisory", "Zscaler"),
    # --- independent: established press
    ("infoq.com", None, "independent", "news", "InfoQ"),
    ("macrumors.com", None, "independent", "news", "MacRumors"),
    ("fortune.com", None, "independent", "news", "Fortune"),
    ("pymnts.com", None, "independent", "news", "PYMNTS"),
    ("ithome.com.tw", None, "independent", "news", "iThome"),
    ("news.pts.org.tw", None, "independent", "news", "公視新聞網"),
    ("reuters.com", None, "independent", "news", "Reuters"),
    ("bloomberg.com", None, "independent", "news", "Bloomberg"),
    ("theverge.com", None, "independent", "news", "The Verge"),
    ("techcrunch.com", None, "independent", "news", "TechCrunch"),
    ("arstechnica.com", r"^/civis(/|$)", "community", "community", "Ars Technica (forum)"),
    ("arstechnica.com", None, "independent", "news", "Ars Technica"),
    ("theregister.com", None, "independent", "news", "The Register"),
    ("zdnet.com", None, "independent", "news", "ZDNET"),
    ("venturebeat.com", None, "independent", "news", "VentureBeat"),
    # --- community
    ("dev.to", None, "community", "community", "DEV Community"),
    ("x.com", None, "community", "community", "X (social post)"),  # social posts are never official
    ("twitter.com", None, "community", "community", "X (social post)"),
    ("reddit.com", None, "community", "community", "Reddit"),
    ("medium.com", None, "community", "community", "Medium"),
    ("news.ycombinator.com", None, "community", "community", "Hacker News"),
    ("stackoverflow.com", None, "community", "community", "Stack Overflow"),
    ("huggingface.co", None, "community", "community", "Hugging Face"),
]
def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


#: what a sub-domain is called where anybody may post: never covered by a "*." rule
_OPEN_LABELS = frozenset((
    "community communities forum forums discuss discussion discussions answers ask ideas feedback talk chat groups group users user "
    "marketplace market store apps partners partner boards board social lists list wiki pastebin share shared sandbox playground"
).split())


def _host_matches(host: str, pattern: str) -> bool:
    """Is ``host`` the host a rule names?  ("example.com": that host or www.example.com.  "*.example.com": its
    sub-domains too, unless one of their labels says that anybody posts there.)"""
    if pattern.startswith("*."):
        base = pattern[2:]
        if host == base:
            return True
        if not host.endswith("." + base):
            return False
        return not any(part in _OPEN_LABELS for label in host[:-len(base) - 1].split(".") for part in re.split(r"[-_]", label))
    return host == pattern or host == "www." + pattern


#: A path that is not the path the server will see: "." and ".." segments (an HTTP client resolves them before it sends
#: the request, so ".../releases/../issues/1" *is* the issue), written plainly or percent-encoded, and backslashes.
#: Such an address is not fetched at all (``safe_http``).
_UNSTABLE_PATH = re.compile(r"(?i)(?:^|/)(?:\.|%2e){1,2}(?:/|$)|\\|%5c|%2f")
#: A path that is sent as it is written, but that a server may read as another one - so it is fetched, and trusted with
#: nothing: an empty segment ("//marketplace"), parameters on a segment ("/marketplace;v=1"), and a letter, a digit or
#: one of "-._~" written as %xx ("/%6darketplace": nothing has to be written that way, and it means the plain character).
_ODD_PATH = re.compile(r"(?i)//|;|%(?:4[1-9a-f]|5[0-9a]|6[1-9a-f]|7[0-9a]|3[0-9]|2d|2e|5f|7e)")


def stable_path(url: str) -> bool:
    """Is the path of ``url`` the path a server will be asked for?  (False for an address that cannot be read at all.)"""
    try:
        return _UNSTABLE_PATH.search(urlparse(url).path) is None
    except ValueError:
        return False


def plain_path(url: str) -> bool:
    """Can the path of ``url`` be judged as it is written: is it stable, and spelled the way paths are spelled?"""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return stable_path(url) and not parsed.params and _ODD_PATH.search(parsed.path) is None


class TierClassifier:
    """Built-in rules, plus what the portal already knows (sources.json) and what the operator declared official.

    ``portal_sources``: a page the portal cites has the tier of its recorded type - that page, not its whole host (one
    article cited from an aggregator does not make the aggregator a newsroom).  For the rest of such a host only the
    publisher's name is remembered.  ``extra_official``: what the operator vouches for (EXTRA_OFFICIAL_DOMAINS):

    * a host ("vendor.example": that host and its sub-domains - not one that is named like a place where anybody
      posts, see _OPEN_LABELS) - for a host the built-in rules say nothing about.  A host they do know keeps their
      answer: listing github.com does not make every repository an official source;
    * a host with a path ("github.com/our-org/notices") - that part of the host, whatever the built-in rules say of
      the rest of it: the operator named the one place they mean.
    """

    def __init__(self, portal_sources: Iterable[dict] | None = None, extra_official: Iterable[str] = ()):
        self._pages: dict[str, tuple[str, str]] = {}
        self._publishers: dict[str, str] = {}
        self._extra_hosts: list[str] = []
        self._extra_places: list[tuple[str, str]] = []
        for entry in extra_official:
            if not isinstance(entry, str) or not entry.strip():
                continue
            entry = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", entry.strip().lower())
            host, _, path = entry.partition("/")
            host, path = host.rstrip("."), "/" + path.strip("/") if path.strip("/") else ""
            if not host or not re.fullmatch(r"(?:\*\.)?[a-z0-9.\-]+", host):
                continue
            if path:
                self._extra_places.append((host, path))
            else:
                self._extra_hosts.append(host if host.startswith("*.") else "*." + host)
        for s in portal_sources or ():
            url = s.get("url") if isinstance(s, dict) else None
            if not isinstance(url, str) or not host_of(url):
                continue
            stype = s.get("type") if isinstance(s.get("type"), str) else "community"
            publisher = s.get("publisher") if isinstance(s.get("publisher"), str) else host_of(url)
            self._pages.setdefault(normalize_url(url), (stype, publisher))
            self._publishers.setdefault(host_of(url), publisher)

    def classify(self, url: str) -> Tier:
        host = host_of(url)
        try:
            path = urlparse(url).path or "/"
        except ValueError:
            return Tier("community", "community", "unknown")
        if not host or not plain_path(url):
            return Tier("community", "community", host or "unknown")
        low = path.lower()
        if any(_host_matches(host, pat) and (low == place or low.startswith(place + "/")) for pat, place in self._extra_places):
            return Tier("official", "vendor-doc", host)
        for pat, path_re, ver, stype, pub in _RULES:
            if _host_matches(host, pat) and (path_re is None or re.search(path_re, path, re.I)):
                return Tier(ver, stype, pub)
        if any(_host_matches(host, pat) for pat in self._extra_hosts):
            return Tier("official", "vendor-doc", host)
        cited = self._pages.get(normalize_url(url))
        if cited:
            stype, pub = cited
            return Tier(_TYPE_TIER.get(stype, "community"), stype, pub)
        return Tier("community", "community", self._publishers.get(host) or host or "unknown")


def best_verification(tiers: Iterable[str]) -> str:
    """Highest tier among the given verification labels (empty -> 'unverified')."""
    best = max((RANK.get(t, 0) for t in tiers), default=0)
    for name, rank in RANK.items():
        if rank == best:
            return name
    return "unverified"
