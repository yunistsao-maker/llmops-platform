"""Per-invocation state: fetched pages, recorded evidence, budgets, the accepted proposal.

One ``ResearchSession`` is created for each invocation and shared by the tools through closures --
there is no module-level state, so concurrent invocations cannot see each other's pages.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .clock import iso_now, today
from .config import Settings
from .portal.checks import Problem, validate_docs
from .portal.data import PortalData
from .validate.grounding import NormalizedText, normalize_with_map
from .web.safe_http import SafeFetcher
from .web.search_backend import SearchBackend
from .web.tiers import Tier, TierClassifier, host_of, normalize_url


@dataclass
class Page:
    id: str
    url: str
    final_url: str
    title: str
    text: str
    kind: str
    fetched_at: str
    tier: Tier
    status: int = 200
    truncated: bool = False
    rendered: bool = False
    headings: list[tuple[int, str, int]] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)
    injection_hits: list[str] = field(default_factory=list)
    #: line number (0-based) of a table row -> the line of column headings of its table
    row_heads: dict[int, str] = field(default_factory=dict)
    _norm: NormalizedText | None = None

    def norm(self) -> NormalizedText:
        if self._norm is None:
            self._norm = normalize_with_map(self.text)
        return self._norm

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass
class Evidence:
    id: str
    page_id: str
    url: str
    title: str
    tier: Tier
    quote: str  # page-verbatim wording: the whole rows / sentences the recorded passage touches
    note: str
    fetched_at: str
    page_sha256: str
    dates: list[str] = field(default_factory=list)
    numbers: list[float] = field(default_factory=list)
    context: str = ""  # page title + headings above the quote + URL path (used to check the quote is about the right subject)
    outer: int = 0  # how many of the first lines of ``context`` are the address and the title (the rest are headings)
    start: int = -1  # offset of the quote in the page text
    #: per line of the quote: the line of column headings of the table it is a row of ("" for any other line)
    heads: tuple = ()

    @property
    def header(self) -> str:
        """The column headings the quoted row(s) stand under ("" when there are none)."""
        return next((h for h in self.heads if h), "")


class ResearchSession:
    def __init__(
        self,
        settings: Settings,
        portal: PortalData,
        *,
        fetcher: SafeFetcher,
        search: SearchBackend,
        renderer: Any = None,
        task: dict | None = None,
    ):
        self.settings = settings
        self.portal = portal
        self.fetcher = fetcher
        self.search = search
        self.renderer = renderer
        self.task = task or {}
        self.classifier = TierClassifier(portal.sources, settings.extra_official_domains)
        self.pages: dict[str, Page] = {}
        self._page_by_url: dict[str, str] = {}
        self.evidence: dict[str, Evidence] = {}
        #: search results whose title, snippet, date or address read like an instruction to an AI ("<url or host>: <excerpt>");
        #: withheld from the model
        self.suspicious_results: list[str] = []
        #: text in the portal's own data that reads like an instruction to an AI ("<file><path>: <excerpt>"): withheld from the model
        self.suspicious_portal: list[str] = []
        #: the same in the handoff notes of earlier runs ("<role> <runId>: <excerpt>", research/handoff.py): withheld from the model
        self.suspicious_notes: list[str] = []
        #: the last proposal the model submitted, accepted or not: what it says is handed on to the next runs (research/handoff.py)
        self.last_submitted: Any = None
        #: what the search results that were shown said (title, snippet, date): a lead the run "came across" may come
        #: from there - nothing is ever proved by it
        self.result_texts: list[str] = []
        self.allowed_urls: set[str] = set()
        self.trusted_hosts: set[str] = {host_of(s["url"]) for s in portal.sources if s.get("url")}
        self.counters: Counter = Counter()
        self.log: list[dict] = []
        self.started_at = iso_now()
        self.today = today()
        self.proposal: dict | None = None
        self.last_report: dict | None = None
        self._baseline: list[Problem] | None = None

    def baseline_problems(self) -> list[Problem]:
        """Validation problems already present in the unmodified portal data (computed once)."""
        if self._baseline is None:
            self._baseline = validate_docs(self.portal.schema, self.portal.docs)
        return self._baseline

    # ---- ids ------------------------------------------------------------------------------
    def next_id(self, prefix: str) -> str:
        self.counters[f"id:{prefix}"] += 1
        return f"{prefix}{self.counters[f'id:{prefix}']}"

    # ---- budgets --------------------------------------------------------------------------
    def tick(self, kind: str = "", *, count_only: bool = False) -> str | None:
        """Count a tool call; returns an error string if a budget is exhausted.

        ``kind`` is "search" / "fetch" for the tools with their own budget.  ``count_only`` tools (record_evidence,
        review_work, ...) are counted but never blocked, so the model can always finish by recording evidence and submitting.
        """
        self.counters["tool_calls"] += 1
        if not count_only and self.counters["tool_calls"] > self.settings.max_tool_calls:
            return "tool-call budget exhausted: stop researching and call submit_proposal now with what you have verified"
        limit = {"search": self.settings.max_searches, "fetch": self.settings.max_fetches}.get(kind)
        if limit is not None:
            self.counters[kind] += 1
            if self.counters[kind] > limit:
                return f"{kind} budget exhausted ({limit}): use pages you already fetched (find_in_page/read_page), or call submit_proposal"
        return None

    # ---- url policy -----------------------------------------------------------------------
    def remember_urls(self, urls: list[str]) -> None:
        for u in urls:
            self.allowed_urls.add(normalize_url(u))

    def fetch_allowed(self, url: str) -> tuple[bool, str]:
        n = normalize_url(url)
        if n in self.allowed_urls:
            return True, "listed (search result, task or portal source)"
        host = host_of(url)
        tier = self.classifier.classify(url)
        if tier.verification == "official":
            return True, "official domain"
        if host in self.trusted_hosts:
            return True, "domain already used by a portal source"
        if any(host == d or host.endswith("." + d) for d in self.settings.extra_allow_domains):
            return True, "operator allow-list"
        return False, (
            f"{host} is not an official domain, not one the portal already uses, and the URL did not come from a search result. "
            "Search for it first (web_search), or choose an official source."
        )

    # ---- pages ----------------------------------------------------------------------------
    def page_for_url(self, url: str) -> Page | None:
        pid = self._page_by_url.get(normalize_url(url))
        return self.pages.get(pid) if pid else None

    def add_page(self, page: Page) -> None:
        self.pages[page.id] = page
        self._page_by_url[normalize_url(page.url)] = page.id
        self._page_by_url[normalize_url(page.final_url)] = page.id

    def page_urls(self) -> set[str]:
        return set(self._page_by_url)

    # ---- output stats ---------------------------------------------------------------------
    def stats(self) -> dict:
        return {
            "searches": self.counters["search"],
            "fetches": self.counters["fetch"],
            "toolCalls": self.counters["tool_calls"],
            "pages": [{"id": p.id, "url": p.final_url, **({"requestedUrl": p.url} if p.url != p.final_url else {}),
                       "tier": p.tier.verification, "chars": len(p.text),
                       "rendered": p.rendered, "truncated": p.truncated, "suspiciousText": bool(p.injection_hits)}
                      for p in self.pages.values()],
            "evidence": len(self.evidence),
            "suspiciousSearchResults": len(self.suspicious_results),
            "suspiciousPortalText": len(self.suspicious_portal),
        }
