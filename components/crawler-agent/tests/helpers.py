"""Shared builders for tests: sessions with pre-loaded pages and recorded evidence."""

from __future__ import annotations

from research.config import Settings
from research.evidence import record_quote
from research.portal.data import PortalData
from research.session import Evidence, Page, ResearchSession
from research.tools import ingest
from research.web.safe_http import FetchResult
from research.web.search_backend import FakeSearchBackend

NOW = "2026-10-02T03:00:00Z"

AWS_LIFECYCLE_HTML = """<html><head><title>Model lifecycle for models launched before 2026-09-07 - Amazon Bedrock</title></head>
<body><nav>menu</nav><main>
<h1>Model lifecycle</h1>
<h2>Legacy and EOL models</h2>
<table>
<tr><th>Model</th><th>Model ID</th><th>Legacy date</th><th>EOL date</th></tr>
<tr><td>Claude 3 Haiku</td><td>anthropic.claude-3-haiku-20240307-v1:0</td><td>2025-12-01</td><td>2026-09-12</td></tr>
<tr><td>Claude 3.5 Haiku</td><td>anthropic.claude-3-5-haiku-20241022-v1:0</td><td>2026-02-01</td><td>2026-12-01</td></tr>
</table></main><footer>footer</footer></body></html>"""

ANTHROPIC_PRICING_HTML = """<html><head><title>Pricing - Claude API Docs</title></head><body><main>
<h1>Pricing</h1><h2>Model pricing</h2>
<table>
<tr><th>Model</th><th>Base Input Tokens</th><th>Cache Writes</th><th>Cache Hits</th><th>Output Tokens</th></tr>
<tr><td>Claude Sonnet 5.5</td><td>$3 / MTok</td><td>$3.75 / MTok</td><td>$0.30 / MTok</td><td>$15 / MTok</td></tr>
<tr><td>Claude Opus 5.5</td><td>$5 / MTok</td><td>$6.25 / MTok</td><td>$0.50 / MTok</td><td>$25 / MTok</td></tr>
</table></main></body></html>"""

GITHUB_ISSUE_HTML = """<html><head><title>Claude 3 Haiku retires on 2026-09-30? · Issue 123 · BerriAI/litellm</title></head><body><main>
<h1>Claude 3 Haiku retires on 2026-09-30?</h1>
<p>Claude 3 Haiku on Bedrock will be retired on 2026-09-30 according to our TAM.</p></main></body></html>"""


def make_session(portal: PortalData | None = None, **settings_kw) -> ResearchSession:
    portal = (portal or PortalData.load("bundled")).clone()
    settings = Settings(model_provider="scripted", **settings_kw)
    return ResearchSession(settings, portal, fetcher=None, search=FakeSearchBackend({}))  # type: ignore[arg-type]


def add_html(session: ResearchSession, url: str, html: str, *, status: int = 200, content_type: str = "text/html; charset=utf-8") -> Page:
    res = FetchResult(url, url, status, content_type, html.encode("utf-8"), False, NOW)
    return ingest(session, res)


def add_quote(session: ResearchSession, page: Page, quote: str, note: str = "", line: int = 0) -> Evidence:
    ev, problem = record_quote(session, page, quote, note, line)
    assert ev is not None, f"quote not recorded: {problem}"
    return ev


def source_url(session: ResearchSession, source_id: str) -> str:
    return session.portal.source_by_id()[source_id]["url"]


def proposal(ops=None, **kw) -> dict:
    base = {"summary": "測試用 proposal。", "ops": ops or []}
    base.update(kw)
    return base


def op(file, op_, path, value=None, evidence=(), rationale="頁面寫明此值，portal 為舊值。") -> dict:
    d = {"file": file, "op": op_, "path": path, "evidence": list(evidence), "rationale": rationale}
    if op_ != "remove":
        d["value"] = value
    return d
