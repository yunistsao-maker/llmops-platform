"""What a proposal may take from its quotes: every fact from ONE quote that states it, for the right model, on a page
good enough for the place it is written to.

These are the ways a third review got wrong facts past the validator (and, in `auto` mode, onto the portal) - each one
with the honest proposal next to it that must keep working.
"""

import copy
import json

import pytest

from helpers import AWS_LIFECYCLE_HTML, ANTHROPIC_PRICING_HTML, GITHUB_ISSUE_HTML, add_html, add_quote, make_session, op, proposal, source_url
from research.portal.data import PortalData
from research.proposal import validate_proposal

LC, CAT, LL = "data/lifecycle.json", "data/catalog.json", "data/litellm.json"
M = "claude-sonnet-5-5"
MODEL = f"/models/{M}"
OFF = f"{MODEL}/offerings/{M}@anthropic"
SHEET = f"{OFF}/pricing/standard"
H3 = "/events/bedrock-claude-3-haiku-eol"
ROW_H3 = "Claude 3 Haiku | anthropic.claude-3-haiku-20240307-v1:0 | 2025-12-01 | 2026-09-12"
ROW_H35 = "Claude 3.5 Haiku | anthropic.claude-3-5-haiku-20241022-v1:0 | 2026-02-01 | 2026-12-01"
PRICE_ROW = "Claude Sonnet 5.5 | $3 / MTok | $3.75 / MTok | $0.30 / MTok | $15 / MTok"
NAME_ONLY = "Claude Sonnet 5.5 is our balanced model for coding and agents."
ISSUE_QUOTE = "Claude 3 Haiku on Bedrock will be retired on 2026-09-30 according to our TAM."
ISSUE_URL = "https://github.com/BerriAI/litellm/issues/123"
REDDIT = "https://www.reddit.com/r/LocalLLaMA/comments/x/y"


def html(title, *paragraphs):
    return f"<html><head><title>{title}</title></head><body><main><h1>{title}</h1>" + "".join(f"<p>{p}</p>" for p in paragraphs) + "</main></body></html>"


@pytest.fixture
def s():
    return make_session()


def page(s, where, body):
    url = where if "://" in where else source_url(s, where)
    s.remember_urls([url])
    return add_html(s, url, body)


def said(s, where, title, sentence):
    """One sentence on a page, quoted."""
    return add_quote(s, page(s, where, html(title, sentence)), sentence)


def aws(s):
    return page(s, "aws-bedrock-lifecycle-legacy", AWS_LIFECYCLE_HTML)


def named(s):
    """An official page that names Claude Sonnet 5.5 and states nothing else."""
    return said(s, "anthropic-sonnet55-overview", "Claude Sonnet 5.5 - overview", NAME_ONLY)


def check(s, *ops, **kw):
    return validate_proposal(s, proposal(list(ops), **kw))


def errors(rep):
    return "\n".join(rep.errors)


def event(s, eid="bedrock-claude-3-haiku-eol"):
    return next(e for e in s.portal.docs[LC]["events"] if e["id"] == eid)


def model(s):
    return next(m for m in s.portal.docs[CAT]["models"] if m["id"] == M)


# --------------------------------------------------------------------------- one quote: the value, the subject, the tier
def test_a_tier_is_not_lent_from_one_quote_to_another(s):
    """[an official sentence that names the model, a forum post with the wanted value] used to pass every check."""
    official = add_quote(s, aws(s), ROW_H3)
    issue = add_quote(s, page(s, ISSUE_URL, GITHUB_ISSUE_HTML), ISSUE_QUOTE)
    rep = check(s, op(LC, "replace", f"{H3}/date", "2026-09-30", [official.id, issue.id]))
    assert not rep.ok and f"{H3}/date = 2026-09-30 is stated for Claude 3 Haiku" in errors(rep) and "only by community-tier evidence" in errors(rep)
    assert "cannot lend its tier" in errors(rep)
    # the price sheet of an official offering: the numbers are in a blog post, the pricing row says something else
    row = add_quote(s, page(s, "anthropic-pricing", ANTHROPIC_PRICING_HTML), PRICE_ROW)
    blog = said(s, "https://dev.to/someone/prices", "prices", "Claude Sonnet 5.5 now costs $9 per MTok input and $45 per MTok output.")
    rep = check(s, op(CAT, "replace", f"{SHEET}/input", 9, [row.id, blog.id]), op(CAT, "replace", f"{SHEET}/output", 45, [row.id, blog.id]))
    assert not rep.ok and errors(rep).count("only by community-tier evidence") == 2
    # which LiteLLM release to run: from Reddit, with any sentence of docs.litellm.ai next to it
    notes = said(s, "litellm-release-notes", "Release notes", "LiteLLM release notes list the changes of every stable release.")
    forum = said(s, REDDIT, "thread", "Just pin litellm to v1.40.0, newer builds are broken for us.")
    rep = check(s, op(LL, "replace", "/recommended/version", "v1.40.0", [notes.id, forum.id]), op(LL, "replace", "/minimumBaseline/version", "v1.40.0", [notes.id, forum.id]))
    assert not rep.ok and errors(rep).count("names version 1.40.0, which is stated for LiteLLM only by community-tier evidence") == 2
    # the honest versions of all three
    assert check(s, op(LC, "replace", f"{H3}/date", "2026-09-12", [official.id, issue.id])).ok
    assert check(s, op(CAT, "replace", f"{SHEET}/input", 3, [row.id, blog.id]), op(CAT, "replace", f"{SHEET}/output", 15, [row.id])).ok


def test_the_row_of_another_model_backs_nothing(s):
    pricing = """<html><head><title>Pricing - Claude API Docs</title></head><body><main><h1>Pricing</h1><h2>Model pricing</h2><table>
<tr><th>Model</th><th>Base Input Tokens</th><th>Cache Writes</th><th>Cache Hits</th><th>Output Tokens</th></tr>
<tr><td>Claude Sonnet 5.5</td><td>$2 / MTok</td><td>$2.50 / MTok</td><td>$0.20 / MTok</td><td>$10 / MTok</td></tr>
<tr><td>Claude Sonnet 5</td><td>$3 / MTok</td><td>$3.75 / MTok</td><td>$0.30 / MTok</td><td>$15 / MTok</td></tr>
</table></main></body></html>"""
    below = add_quote(s, page(s, "anthropic-pricing", pricing), "Claude Sonnet 5 | $3 / MTok | $3.75 / MTok | $0.30 / MTok | $15 / MTok")
    rep = check(s, op(CAT, "replace", f"{SHEET}/input", 3, [below.id]), op(CAT, "replace", f"{SHEET}/output", 15, [below.id]))
    assert not rep.ok and "none of the cited quotes" in errors(rep) and "Claude Sonnet 5.5" in errors(rep)
    # a new event for Claude 3.5 Haiku with the EOL date of the Claude 3 Haiku row
    row3 = add_quote(s, aws(s), ROW_H3)
    new = {"id": "bedrock-claude-3-5-haiku-eol", "modelName": "Claude 3.5 Haiku", "vendor": "anthropic", "providerModelId": "anthropic.claude-3-5-haiku-20241022-v1:0",
           "cloud": "aws", "service": "bedrock", "event": "retirement", "date": "2026-09-12", "verification": "official", "sources": [{"id": "aws-bedrock-lifecycle-legacy"}]}
    rep = check(s, op(LC, "add", "/events/-", new, [row3.id]))
    assert not rep.ok and "none of the cited quotes" in errors(rep) and "Claude 3.5 Haiku" in errors(rep)
    # both rows quoted: the date still has to be on the row of the model
    row35 = add_quote(s, s.pages[row3.page_id], ROW_H35)
    rep = check(s, op(LC, "add", "/events/-", new, [row3.id, row35.id]))
    assert not rep.ok and "date = 2026-09-12 is in a cited quote, but not where the quote is about Claude 3.5 Haiku" in errors(rep)
    assert check(s, op(LC, "add", "/events/-", dict(new, date="2026-12-01"), [row3.id, row35.id])).ok
    # a quote that starts in one row and ends in the next
    straddling = add_quote(s, s.pages[row3.page_id], "2025-12-01 | 2026-09-12 Claude 3.5 Haiku | anthropic.claude-3-5-haiku-20241022-v1:0")
    assert "\n" in straddling.quote
    rep = check(s, op(LC, "add", "/events/-", new, [straddling.id]))
    assert not rep.ok and "not where the quote is about Claude 3.5 Haiku" in errors(rep)


def test_a_record_is_checked_against_its_own_name_not_against_a_name_inside_it(s):
    """A benchmark has a `name` too; a sentence about another model that names the benchmark is not about this model."""
    aa = said(s, "aa-sonnet55", "Models leaderboard | Artificial Analysis", "GPT-6 Sol scores 61 on the Artificial Analysis Intelligence Index, ranked #1 as of 2026-10-01.")
    score = f"{MODEL}/benchmarks/Artificial Analysis Intelligence Index/score"
    rep = check(s, op(CAT, "replace", score, 61, [aa.id]))
    assert not rep.ok and "none of the cited quotes" in errors(rep) and "Claude Sonnet 5.5" in errors(rep)
    own = said(s, "https://artificialanalysis.ai/models/claude-sonnet-5-5/providers", "Claude Sonnet 5.5 | Artificial Analysis",
               "Claude Sonnet 5.5 scores 57 on the Artificial Analysis Intelligence Index.")
    assert check(s, op(CAT, "replace", score, 57, [own.id])).ok
    # an operation cannot give the record the name its quote happens to be about
    row35 = add_quote(s, aws(s), ROW_H35)
    renamed = dict(copy.deepcopy(event(s)), providerModelId="anthropic.claude-3-5-haiku-20241022-v1:0", date="2026-12-01")
    rep = check(s, op(LC, "replace", H3, renamed, [row35.id]))
    assert not rep.ok and "none of the cited quotes" in errors(rep) and "Claude 3 Haiku" in errors(rep)


def test_a_short_name_is_a_name(s):
    """`o3`: the validator used to find nothing to look for (no check at all), or only the record's own id (nothing passes)."""
    table = """<html><head><title>Model retirements - Azure AI Foundry</title></head><body><main><h1>Model retirements</h1><table>
<tr><th>Model</th><th>Version</th><th>Retirement date</th></tr>
<tr><td>o3</td><td>2025-04-16</td><td>2026-12-01</td></tr>
<tr><td>o3-mini</td><td>2025-01-31</td><td>2026-11-05</td></tr>
</table></main></body></html>"""
    p = page(s, "ms-foundry-retirement-schedule", table)
    o3, mini = add_quote(s, p, "o3 | 2025-04-16 | 2026-12-01"), add_quote(s, p, "o3-mini | 2025-01-31 | 2026-11-05")
    assert check(s, op(LC, "replace", "/events/azure-o3-eol/date", "2026-12-01", [o3.id])).ok
    rep = check(s, op(LC, "replace", "/events/azure-o3-eol/date", "2026-11-05", [mini.id]))
    assert not rep.ok and "none of the cited quotes" in errors(rep) and "mention o3" in errors(rep)
    # a new record whose names are too short to be looked for is not waved through: it is sent back
    row35 = add_quote(s, aws(s), ROW_H35)
    nameless = {"id": "x-eol", "modelName": "X", "vendor": "openai", "providerModelId": "x", "cloud": "azure", "service": "foundry", "event": "retirement",
                "date": "2026-12-01", "verification": "official", "sources": [{"id": "aws-bedrock-lifecycle-legacy"}]}
    rep = check(s, op(LC, "add", "/events/-", nameless, [row35.id]))
    assert not rep.ok and "has no model name that a quote could be checked against" in errors(rep)


def test_a_quote_from_the_section_of_another_model(s):
    sections = """<html><head><title>Pricing - Claude API Docs</title></head><body><main><h1>Pricing</h1>
<h2>Claude Opus 5.5</h2><table><tr><th>Input</th><th>Output</th></tr><tr><td>$5 / MTok</td><td>$25 / MTok</td></tr></table>
<h2>Claude Sonnet 5.5</h2><table><tr><th>Input</th><th>Output</th></tr><tr><td>$3 / MTok</td><td>$15 / MTok</td></tr></table>
<h2>Claude Haiku 4.5</h2><table><tr><th>Input</th><th>Output</th></tr><tr><td>$1 / MTok</td><td>$6 / MTok</td></tr></table>
</main></body></html>"""
    p = page(s, "anthropic-pricing", sections)
    haiku, sonnet = add_quote(s, p, "$1 / MTok | $6 / MTok"), add_quote(s, p, "$3 / MTok | $15 / MTok")
    assert haiku.context.split("\n")[-1] == "Claude Haiku 4.5" and sonnet.context.split("\n")[-1] == "Claude Sonnet 5.5"
    rep = check(s, op(CAT, "replace", f"{SHEET}/input", 1, [haiku.id]), op(CAT, "replace", f"{SHEET}/output", 6, [haiku.id]))
    assert not rep.ok and "none of the cited quotes" in errors(rep)
    assert check(s, op(CAT, "replace", f"{SHEET}/input", 3, [sonnet.id]), op(CAT, "replace", f"{SHEET}/output", 15, [sonnet.id])).ok


# --------------------------------------------------------------------------- numbers and versions
def test_the_digits_of_a_name_are_not_an_amount(s):
    """From "Claude Sonnet 5.5 is our balanced model": context window 5500, input price 5.5 (a thousand times a quoted
    number used to count for every numeric field, and the 5.5 of the name was a quoted number)."""
    ev = named(s)
    for path, value in ((f"{MODEL}/contextWindow", 5500), (f"{MODEL}/maxOutputTokens", 5500), (f"{SHEET}/output", 5500), (f"{SHEET}/input", 5.5),
                        (f"{MODEL}/benchmarks/Artificial Analysis Intelligence Index/score", 5.5)):
        rep = check(s, op(CAT, "replace", path, value, [ev.id]))
        assert not rep.ok and f"/{path.rsplit('/', 1)[1]} = {value} does not appear in the cited quotes" in errors(rep), path
        assert "numbers there, not counting the digits of names, ids and dates: none" in errors(rep)
    # a model-card line without a price: 0, 9 and 12 are in its id and its date
    card = said(s, "aws-bedrock-sonnet55-card", "Claude Sonnet 5.5 - Amazon Bedrock", "Claude Sonnet 5.5 | anthropic.claude-sonnet-5-5-20260928-v1:0 | Active | launched 2026-09-12")
    sheet = {"scope": "standard", "currency": "USD", "unit": "per_1m_tokens", "input": 0, "output": 0, "cacheRead": 12, "cacheWrite": 9, "notes": [],
             "sources": [{"id": "aws-bedrock-sonnet55-card"}], "verification": "official"}
    rep = check(s, op(CAT, "add", f"{MODEL}/offerings/{M}@bedrock/pricing/-", sheet, [card.id]))
    assert not rep.ok and all(f"/{field} = {value} does not appear" in errors(rep) for field, value in (("input", 0), ("output", 0), ("cacheRead", 12), ("cacheWrite", 9)))


def test_a_version_is_compared_whole(s):
    stable = said(s, "litellm-release-notes", "v1.102.1 - LiteLLM release notes", "LiteLLM v1.102.1 is the current stable release.")
    for value in ("v2", "v1", "litellm_v1.40.0", "ｖ1.40.0", "v1.102.1-rc.1", "v1.40.0", "v1.102", "1.102.x"):
        rep = check(s, op(LL, "replace", "/recommended/version", value, [stable.id]))
        assert not rep.ok, value
    rep = check(s, op(LL, "replace", "/recommended/version", "v1.102.1-rc.1", [stable.id]))
    assert "names version 1.102.1-rc1, which does not appear in the cited quotes" in errors(rep)  # a release candidate is not the release
    rep = check(s, op(LL, "replace", "/minimumBaseline/version", "v1", [stable.id]))
    assert "portal validation: data/litellm.json: minimumBaseline/version" in errors(rep) and "is not a release version such as v1.102.1" in errors(rep)
    assert check(s, op(LL, "replace", "/minimumBaseline/version", "v1.102.1", [stable.id])).ok
    # the versions to avoid are read by the Gateway page: "1." would mean every release there is
    for versions in ("1.", "v1.", "1.x", "*", "rc"):
        entry = {"versions": versions, "reason": "嚴重漏洞，所有 1.x 版本都應停用。", "sources": [{"id": "litellm-release-notes"}]}
        rep = check(s, op(LL, "add", "/avoid/-", entry, [stable.id]))
        assert not rep.ok and "portal validation: data/litellm.json: avoid" in errors(rep), versions
    entry = {"versions": "1.102.1", "reason": "【測試】", "sources": [{"id": "litellm-release-notes"}]}
    assert check(s, op(LL, "add", "/avoid/-", entry, [stable.id])).ok
    rep = check(s, op(LL, "add", "/avoid/-", dict(entry, versions="1.82.7、1.82.8"), [stable.id]))
    assert not rep.ok and "names version 1.82.7, which does not appear" in errors(rep) and "names version 1.82.8, which does not appear" in errors(rep)
    # the spellings of a package index and of a git tag are the same version
    pypi = said(s, "pypi-litellm", "litellm · PyPI", "litellm 1.104.0rc1 was uploaded on 2026-10-01; 1.103.2.dev1 is a development build.")
    release = {"version": "v1.104.0-rc.1", "channel": "rc", "supported": False, "date": "2026-10-01", "highlights": [], "sources": [{"id": "pypi-litellm"}]}
    assert check(s, op(LL, "add", "/releases/-", release, [pypi.id])).ok
    rep = check(s, op(LL, "add", "/releases/-", dict(release, version="v1.104.0"), [pypi.id]))
    assert not rep.ok and "v1.104.0" in errors(rep)  # the release is not out: the quote has its candidate


# --------------------------------------------------------------------------- labels, citations, annotations
def test_raising_a_label_reasserts_what_stands_under_it(s):
    """A date known only from GitHub issues relabelled `official` with one official sentence that states no date."""
    target = "/events/vertex-gemini-2-5-flash-eol"
    assert event(s, "vertex-gemini-2-5-flash-eol")["verification"] == "community"
    card = said(s, "https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/gemini/2-5-flash", "Gemini 2.5 Flash | Vertex AI",
                "gemini-2.5-flash supports text, image, audio and video input.")
    assert card.tier.verification == "official"
    rep = check(s, op(LC, "replace", f"{target}/verification", "official", [card.id]))
    assert not rep.ok and "raising the label to 'official'" in errors(rep) and f"{target}/date = 2027-03-31 is not stated for gemini-2.5-flash by a cited official quote" in errors(rep)
    dated = said(s, "google-vertex-model-versions", "Model versions | Vertex AI", "gemini-2.5-flash | Retirement date: March 31, 2027")
    assert dated.tier.verification == "official"
    rep = check(s, op(LC, "replace", f"{target}/verification", "official", [dated.id]),
                op(LC, "replace", f"{target}/sources", [{"id": "gcp-gemini25-notice-1763"}, {"id": "gcp-gemini25-notice-4509"}, {"id": "google-vertex-model-versions"}], [dated.id]))
    assert rep.ok, rep.errors
    assert any("the label at /events/vertex-gemini-2-5-flash-eol/verification changes from 'community' to 'official'" in w and "wait for a person" in w for w in rep.warnings)
    # computed -> official: a price sheet that is `derived` does not become a quotation by being relabelled
    model(s)["offerings"][0]["pricing"][0]["verification"] = "derived"
    s._baseline = None
    rep = check(s, op(CAT, "replace", f"{SHEET}/verification", "official", [named(s).id]))
    assert not rep.ok and errors(rep).count("raising the label to 'official'") >= 2 and f"{SHEET}/input = 2.0 is not stated" in errors(rep)


def test_the_citations_of_a_labelled_record_keep_a_page_of_its_tier(s):
    """All citations of an official record replaced by a GitHub issue, registered as a new source under a title and a
    publisher of the model's choosing ("AWS")."""
    row = add_quote(s, aws(s), ROW_H3)
    issue = page(s, ISSUE_URL, GITHUB_ISSUE_HTML)
    new_source = [{"id": "aws-lifecycle-notice", "url": issue.url, "title": "Amazon Bedrock model lifecycle - official notice", "publisher": "AWS", "type": "csp-doc"}]
    ev = add_quote(s, issue, ISSUE_QUOTE)
    rep = check(s, op(LC, "replace", f"{H3}/sources", [{"id": "aws-lifecycle-notice"}], [ev.id]), newSources=new_source)
    assert not rep.ok and f"{H3}/sources would not cite a single official page" in errors(rep)
    # citing it next to the official page is what a community report is for - under the page's own title and publisher
    rep = check(s, op(LC, "replace", f"{H3}/sources", [{"id": "aws-bedrock-lifecycle-legacy"}, {"id": "aws-lifecycle-notice"}], [ev.id, row.id]), newSources=new_source)
    assert rep.ok, rep.errors
    added = rep.proposal["newSources"][0]
    assert added["title"] == "Claude 3 Haiku retires on 2026-09-30? · Issue 123 · BerriAI/litellm" and added["publisher"] == "GitHub / BerriAI" and added["type"] == "github"
    # the same page cited twice is not a change
    rep = check(s, op(LC, "add", f"{H3}/sources/-", {"id": "aws-bedrock-lifecycle-legacy"}, [row.id]))
    assert not rep.ok and "this list already cites 'aws-bedrock-lifecycle-legacy'" in errors(rep)
    # ... also when the whole list is replaced by one that names the page twice
    twice = [{"id": "aws-bedrock-lifecycle-legacy"}, {"id": "aws-bedrock-lifecycle-legacy", "note": "again"}]
    rep = check(s, op(LC, "replace", f"{H3}/sources", twice, [row.id]))
    assert not rep.ok and "2 records have id = \"aws-bedrock-lifecycle-legacy\"" in errors(rep)
    assert "would cite 'aws-bedrock-lifecycle-legacy' more than once" in errors(rep)  # (said where the model looks for it: with its operation)


def test_what_another_page_says_and_where_to_move_to_are_facts_too(s):
    """`conflicts` and `replacement` annotate a record; what they state still needs its quote."""
    row = add_quote(s, aws(s), ROW_H3)
    issue = add_quote(s, page(s, ISSUE_URL, GITHUB_ISSUE_HTML), ISSUE_QUOTE)
    conflict = [{"date": "2026-09-30", "kind": "conflicting", "note": "另一頁面寫 2026-09-30。", "sources": [{"id": "litellm-issue-123"}]}]
    src = [{"id": "litellm-issue-123", "url": ISSUE_URL}]
    rep = check(s, op(LC, "add", f"{H3}/conflicts", conflict, [row.id, issue.id]), newSources=src)
    assert not rep.ok and f"{H3}/conflicts/0/date = 2026-09-30 is stated for Claude 3 Haiku" in errors(rep) and "only by community-tier evidence" in errors(rep)
    # the date the record has today may become history without a quote; another date of the record may not
    history = [{"date": "2026-09-10", "kind": "superseded", "note": "AWS 表格原列 2026-09-10。", "sources": [{"id": "aws-bedrock-lifecycle-legacy"}]}]
    assert check(s, op(LC, "replace", f"{H3}/date", "2026-09-12", [row.id]), op(LC, "add", f"{H3}/conflicts", history, [row.id])).ok
    o3 = event(s, "azure-o3-eol")
    assert o3["version"] == "2025-04-16"
    table = said(s, "ms-foundry-retirement-schedule", "Model retirements", "o3 remains available in all regions.")
    wrong = o3["conflicts"] + [{"date": "2025-04-16", "kind": "superseded", "note": "先前公告的日期。", "sources": [{"id": "ms-foundry-retirement-schedule"}]}]
    rep = check(s, op(LC, "replace", "/events/azure-o3-eol/conflicts", wrong, [table.id]))
    assert not rep.ok and "/events/azure-o3-eol/conflicts/1/date = 2025-04-16 does not appear" in errors(rep)
    # a new record has no history: not the date of whatever record stands last in the list today
    last = s.portal.docs[LC]["events"][-1]
    row35 = add_quote(s, s.pages[row.page_id], ROW_H35)
    new = {"id": "bedrock-claude-3-5-haiku-eol", "modelName": "Claude 3.5 Haiku", "vendor": "anthropic", "providerModelId": "anthropic.claude-3-5-haiku-20241022-v1:0",
           "cloud": "aws", "service": "bedrock", "event": "retirement", "date": "2026-12-01", "verification": "official", "sources": [{"id": "aws-bedrock-lifecycle-legacy"}],
           "notes": [f"AWS 原訂 {last['date']} 退役，後提前至 2026-12-01。"],
           "conflicts": [{"date": last["date"], "kind": "superseded", "note": "先前公告的日期。", "sources": [{"id": "aws-bedrock-lifecycle-legacy"}]}]}
    rep = check(s, op(LC, "add", "/events/-", new, [row35.id]))
    assert not rep.ok and f"conflicts/0/date = {last['date']} does not appear" in errors(rep) and f"mentions the date {last['date']} in prose" in errors(rep)


# --------------------------------------------------------------------------- places without a label
def test_what_a_model_is_does_not_follow_from_a_forum_post(s):
    """On a catalog model everything outside its offerings carries no label: the context window, the release date and a
    benchmark score used to take a quote from any page."""
    post = said(s, REDDIT, "thread", "Claude Sonnet 5.5 has a 2M context window, was released on 2026-01-01 and scores 99 on the Artificial Analysis Intelligence Index.")
    score = f"{MODEL}/benchmarks/Artificial Analysis Intelligence Index/score"
    for path, value, kind in ((f"{MODEL}/contextWindow", 2_000_000, "an official page"), (f"{MODEL}/releaseDate", "2026-01-01", "an official page"),
                              (score, 99, "an independent page")):
        rep = check(s, op(CAT, "replace", path, value, [post.id]))
        assert not rep.ok and f"needs evidence from {kind} about Claude Sonnet 5.5" in errors(rep) and "community-tier" in errors(rep), path
    # ... and what the gateway needs for the model is read where LiteLLM is the subject: a forum post about the model is not even that
    rep = check(s, op(CAT, "replace", f"{MODEL}/litellm/recommendedVersion", "v1.40.0", [post.id]))
    assert not rep.ok and "mention Claude Sonnet 5.5 / claude-sonnet-5-5 on a page or a line that is about LiteLLM" in errors(rep)
    pinned = said(s, f"{REDDIT}2", "thread", "For Claude Sonnet 5.5 just pin LiteLLM to v1.40.0, newer builds are broken for us.")
    rep = check(s, op(CAT, "replace", f"{MODEL}/litellm/recommendedVersion", "v1.40.0", [pinned.id]))
    assert not rep.ok and "needs evidence from an official page about Claude Sonnet 5.5" in errors(rep) and "community-tier" in errors(rep)
    # a note is somebody's remark, whoever made it: it does not claim to be the vendor's
    rep = check(s, op(CAT, "add", f"{MODEL}/weaknesses/-", "社群回報長輸出時延遲偏高。", [post.id]))
    assert rep.ok, rep.errors
    # the honest sources for the same places
    card = said(s, "anthropic-sonnet55-overview", "Claude Sonnet 5.5 - overview", "Claude Sonnet 5.5 has a 2M context window.")
    assert check(s, op(CAT, "replace", f"{MODEL}/contextWindow", 2_000_000, [card.id])).ok
    aa = said(s, "aa-sonnet55", "Claude Sonnet 5.5 | Artificial Analysis", "Claude Sonnet 5.5 scores 57 on the Artificial Analysis Intelligence Index.")
    assert aa.tier.verification == "independent" and check(s, op(CAT, "replace", score, 57, [aa.id])).ok


def deep_dive_session():
    """The sample data plus a deep dive for its one model (the bundled sample has none)."""
    portal = PortalData.load("bundled").clone()
    portal.docs["data/models/claude-sonnet-5-5.json"] = {
        "schema": "deep-dive/v1", "modelId": M, "title": "Claude Sonnet 5.5 技術深度解析", "locale": "zh-TW", "asOf": "2026-10-01", "tldr": "【範例】",
        "whatsNew": [], "breakingChanges": [], "availabilityMatrix": [], "apiCompatibility": [], "strengths": ["【範例】長文件。"], "weaknesses": [], "bestFor": [],
        "avoidFor": [], "safetyAndCompliance": [], "enterpriseNotes": [], "testPlanFocus": [], "openQuestions": [], "sources": [{"id": "anthropic-sonnet55-overview"}],
        "onboarding": {"audience": "應用團隊", "prerequisites": [], "steps": [], "aliases": [], "parameterGuidance": [],
                       "codeSamples": [{"lang": "bash", "title": "curl", "code": "curl -sS https://llm-gateway.example.internal/v1/chat/completions"}],
                       "dataClassification": "【範例】", "support": "【範例】"},
    }
    return make_session(portal)


def test_an_onboarding_guide_is_not_rewritten_from_a_forum_post():
    s = deep_dive_session()
    assert s.baseline_problems() == []
    dive = "data/models/claude-sonnet-5-5.json"
    post = said(s, REDDIT, "thread", "For claude-sonnet-5-5 through the gateway, the helper package works best for us.")
    code = "pip install litellm-gateway-helper   # required\nexport ANTHROPIC_BASE_URL=gateway-helper.example/v1"
    rep = check(s, op(dive, "replace", "/onboarding/codeSamples/curl/code", code, [post.id], rationale="社群建議的安裝方式。"))
    assert not rep.ok and "/onboarding/codeSamples/0/code needs evidence from an official page about claude-sonnet-5-5" in errors(rep)
    rep = check(s, op(dive, "add", "/strengths/-", "社群回報速度很快。", [post.id]))
    assert not rep.ok and "needs evidence from an independent page" in errors(rep)
    # from the vendor's page the same change is fine - and still waits for a person, because nothing compares code with a quote
    doc = said(s, "anthropic-sonnet55-overview", "Claude Sonnet 5.5 - overview", "Call claude-sonnet-5-5 through the Messages API; no helper package is required.")
    rep = check(s, op(dive, "replace", "/onboarding/codeSamples/curl/code", code.replace("# required", "# optional"), [doc.id], rationale="依官方說明。"))
    assert rep.ok, rep.errors
    assert any("/onboarding/codeSamples/0/code is a status, a category or an identifier" in w and "wait for a person" in w for w in rep.warnings)
    aa = said(s, "aa-sonnet55", "Claude Sonnet 5.5 | Artificial Analysis", "Claude Sonnet 5.5 scores 57 on the Artificial Analysis Intelligence Index.")
    assert check(s, op(dive, "add", "/strengths/-", "AA 智慧指數 57。", [aa.id])).ok


# --------------------------------------------------------------------------- what no program can compare with a quote
def test_statuses_categories_and_identifiers_wait_for_a_person(s):
    """One official sentence that names the model used to publish: Taiwan residency unknown -> yes, an offering ga ->
    retired, a feature not-supported -> supported, the model id an application calls.  The validator cannot compare such
    values with the quote - it says so, and the publisher holds every one of them (pipeline tests)."""
    ev = named(s)
    changes = [(f"{OFF}/dataProcessing/taiwanResidency", "yes"), (f"{OFF}/dataProcessing/zdr", "available"), (f"{OFF}/status", "retired"),
               (f"{OFF}/features/forced-tool-choice/status", "supported"), (f"{MODEL}/offerings/{M}@bedrock/lifecycle/legacyPeriod", "45 天"),
               (f"{MODEL}/knowledgeCutoff", "2027-01"), (f"{MODEL}/reasoning/alwaysOn", False), (f"{MODEL}/tier", "frontier"),
               (f"{OFF}/providerModelId", "claude-sonnet-5-5-preview-x")]
    gateway = said(s, "litellm-release-notes", "Release notes - LiteLLM", "LiteLLM routes Claude Sonnet 5.5 on Amazon Bedrock through the Converse API.")
    changes.append((f"{MODEL}/litellm/providerStrings/bedrock/model", "openai/some-other-model"))   # (a gateway setting: from a page about LiteLLM)
    for path, value in changes:
        rep = check(s, op(CAT, "replace", path, value, [gateway.id if "/litellm/" in path else ev.id]))
        assert rep.ok, (path, rep.errors)
        assert any(path in w and "no program can compare such a value with a quote" in w and "wait for a person" in w for w in rep.warnings), (path, rep.warnings)
    row = add_quote(s, aws(s), ROW_H3)
    rep = check(s, op(LC, "replace", f"{H3}/event", "legacy", [row.id]))
    assert rep.ok and any(f"{H3}/event is a status, a category or an identifier" in w for w in rep.warnings)
    # a date, a number and a version ARE compared; text that is added is published as it is; text that is rewritten waits
    rep = check(s, op(LC, "replace", f"{H3}/date", "2026-09-12", [row.id]), op(LC, "add", f"{H3}/notes/-", "AWS 表列 EOL 2026-09-12。", [row.id]))
    assert rep.ok and not any("wait for a person" in w for w in rep.warnings), rep.warnings
    rep = check(s, op(LC, "replace", f"{H3}/notes/0", "尚未退役：呼叫仍會成功。", [row.id]))
    assert rep.ok and any(f"{H3}/notes/0 replaces text the portal has today" in w and "wait for a person" in w for w in rep.warnings)


def test_what_else_waits_is_said_to_the_model(s):
    row = add_quote(s, aws(s), ROW_H3)
    # emptying a text is a deletion
    rep = check(s, op(LC, "replace", f"{H3}/scopeNote", "", [row.id]), op(LC, "replace", f"{H3}/notes/0", " ", [row.id]))
    assert rep.ok and sum("would be left empty" in w and "waits for a person" in w for w in rep.warnings) == 2
    # lowering a label and writing a forum date under it: valid, and for a person to decide
    issue = add_quote(s, page(s, ISSUE_URL, GITHUB_ISSUE_HTML), ISSUE_QUOTE)
    rep = check(s, op(LC, "replace", f"{H3}/verification", "community", [issue.id]), op(LC, "replace", f"{H3}/date", "2026-09-30", [issue.id]))
    assert rep.ok and any("changes from 'official' to 'community'" in w and "wait for a person" in w for w in rep.warnings)
    # anything under a `derived` label
    model(s)["offerings"][0]["pricing"][0]["verification"] = "derived"
    s._baseline = None
    rep = check(s, op(CAT, "add", f"{SHEET}/notes/-", "已對照官方頁面。", [named(s).id], rationale="依官方頁面核對推算結果，數值不變。"))
    assert rep.ok and any("stands under the label 'derived'" in w and "wait for a person" in w for w in rep.warnings)
    # more operations than are published automatically
    notes = [op(LC, "add", f"{H3}/notes/-", f"第 {n} 則：AWS 表列 EOL 2026-09-12。", [row.id]) for n in range(61)]
    rep = check(s, *notes)
    assert rep.ok and any("61 operations" in w and "more than 60" in w and "wait for a person" in w for w in rep.warnings)
    assert not any("more than 60" in w for w in check(s, *notes[:60]).warnings)


def test_a_date_written_over_text_a_name_or_a_category_waits_for_a_person(s):
    """Whether "a date changed" was decided by the look of the new value.  One official quote that names the model and
    holds a date then put that date where a summary, an identifier, a category or a note stood - accepted without a
    word, and published in auto mode."""
    dated = said(s, "anthropic-sonnet55-overview", "Claude Sonnet 5.5 - overview", "Claude Sonnet 5.5 was released on 2026-09-28 and is supported from LiteLLM v1.103.0.")
    row = add_quote(s, aws(s), ROW_H3)
    for file, path, value, ev, why in (
        (CAT, f"{MODEL}/summary", "2026-09-28", dated, "replaces text the portal has today"),
        (CAT, f"{OFF}/providerModelId", "2026-09-28", dated, "is a status, a category or an identifier"),
        (CAT, f"{MODEL}/reasoning/defaultEffort", "2026-09-28", dated, "is a status, a category or an identifier"),
        (LC, f"{H3}/notes/0", "2026-09-12", row, "replaces text the portal has today"),
        (LC, f"{H3}/scopeNote", "2026-09-12", row, "replaces text the portal has today"),
    ):
        rep = check(s, op(file, "replace", path, value, [ev.id]))
        assert rep.ok, (path, rep.errors)
        assert any(path in w and why in w and "wait for a person" in w for w in rep.warnings), (path, rep.warnings)
    # (a date written over a NAME of the record is turned down outright: its names are what the quotes are searched for)
    assert not check(s, op(LC, "replace", f"{H3}/modelName", "2026-09-12", [row.id])).ok
    assert not check(s, op(LC, "replace", f"{H3}/providerModelId", "2026-09-12", [row.id])).ok
    # a version field takes a version for a version; a sentence that mentions one is text there
    told = "/models/claude-sonnet-5-5/litellm/recommendedVersion is a status, a category or an identifier"
    rep = check(s, op(CAT, "replace", f"{MODEL}/litellm/recommendedVersion", "請改用 v1.103.0（見說明）", [dated.id]))
    assert any(told in w and "wait for a person" in w for w in rep.warnings), rep.warnings
    # the changes this is not about: a date where a date stood, a version where a version stood
    rep = check(s, op(LC, "replace", f"{H3}/date", "2026-09-12", [row.id]), op(CAT, "replace", f"{MODEL}/litellm/recommendedVersion", "v1.103.0", [dated.id]))
    assert rep.ok and not any("wait for a person" in w for w in rep.warnings), (rep.errors, rep.warnings)


def test_a_list_grows_at_its_end(s):
    """Pages show the first price sheet, the first note, the first citation.  The order of a list was protected against
    records changing places - and something NEW in front of them, or a second record with the key of the first, got the
    same place without any existing value having changed."""
    row = add_quote(s, aws(s), ROW_H3)
    price = add_quote(s, page(s, "anthropic-pricing", ANTHROPIC_PRICING_HTML), PRICE_ROW)
    note = "AWS 表列 EOL 2026-09-12。"
    assert event(s)["notes"] and len(event(s)["sources"]) == 1

    rep = check(s, op(LC, "add", f"{H3}/notes/0", note, [row.id]))
    assert rep.ok and any(f"puts something new in front of what {H3}/notes holds today" in w and "wait for a person" in w for w in rep.warnings), rep.warnings
    rep = check(s, op(LC, "add", f"{H3}/notes/-", note, [row.id]))
    assert rep.ok and not any("wait for a person" in w for w in rep.warnings), rep.warnings
    # a second price sheet for the scope that has one: refused, with the path of the record that is there
    sheet = copy.deepcopy(model(s)["offerings"][0]["pricing"][0])
    assert sheet["scope"] == "standard"
    rep = check(s, op(CAT, "add", f"{SHEET.rsplit('/', 1)[0]}/-", dict(sheet, input=3, output=15), [price.id]))
    assert not rep.ok and "this list already has a record with scope = 'standard'" in errors(rep) and f"{SHEET}/<field>" in errors(rep)
    # the records of a list in another order: not a change a proposal makes
    rep = check(s, op(LC, "replace", "/events", list(reversed(s.portal.docs[LC]["events"])), [row.id]))
    assert not rep.ok and "this only puts the records of /events in another order" in errors(rep)
    # what a fact cites: one more page at the end is new content - a note on the citation that is there is not
    rep = check(s, op(LC, "replace", f"{H3}/sources", [dict(event(s)["sources"][0], note="改寫的附註")], [row.id]))
    assert rep.ok and any(f"{H3}/sources would no longer begin with the citations it has today" in w and "wait for a person" in w for w in rep.warnings), rep.warnings
    issue = add_quote(s, page(s, "anthropic-deprecations", html("Model deprecations", "Claude 3 Haiku on Amazon Bedrock reaches end of life on 2026-09-12.")),
                      "Claude 3 Haiku on Amazon Bedrock reaches end of life on 2026-09-12.")
    rep = check(s, op(LC, "add", f"{H3}/sources/-", {"id": "anthropic-deprecations"}, [issue.id]))
    assert rep.ok and not any("wait for a person" in w for w in rep.warnings), (rep.errors, rep.warnings)
    rep = check(s, op(LC, "replace", f"{H3}/sources", [{"id": "anthropic-deprecations"}, *event(s)["sources"]], [issue.id]))
    assert rep.ok and any("would no longer begin with the citations it has today" in w for w in rep.warnings), (rep.errors, rep.warnings)


# --------------------------------------------------------------------------- rules no test was looking at (mutation check, fourth review)
def test_an_advisory_is_not_read_from_a_newsroom_however_established(s):
    """The advisory tests cited community pages, which fail on the tier alone: an independent newsroom has the tier
    and still is not an advisory page."""
    text = "Versions before 1.84.2 expose the spend logs endpoint without authentication. Fixed in 1.84.2, published 2026-09-30."
    press = said(s, "https://www.infoq.com/news/2026/09/litellm-spend-logs/", "LiteLLM spend logs bug - InfoQ", text)
    assert (press.tier.verification, press.tier.source_type) == ("independent", "news")
    advisory = {"id": "LITELLM-SPEND-LOGS-2026-09", "title": "spend logs endpoint 未驗證", "affected": "< 1.84.2", "fixedIn": "1.84.2", "severity": "high",
                "published": "2026-09-30", "summary": "1.84.2 之前的版本未驗證 spend logs endpoint。", "action": "升級到 1.84.2 以上。", "sources": [{"id": "infoq-post"}]}
    rep = check(s, op(LL, "add", "/advisories/-", advisory, [press.id]), newSources=[{"id": "infoq-post", "url": press.url}])
    assert not rep.ok and "needs evidence from an advisory page" in errors(rep) and "independent-tier (news)" in errors(rep), rep.errors


def test_a_new_catalog_model_without_a_usable_name_is_sent_back_too(s):
    ev = named(s)
    new = copy.deepcopy(model(s))
    new.update(id="x", name="X", vendorModelId="x", offerings=[])
    rep = check(s, op(CAT, "add", "/models/-", new, [ev.id]))
    assert not rep.ok and "the record at /models/x has no model name that a quote could be checked against" in errors(rep), rep.errors


def test_a_change_to_the_teams_own_file_is_said_to_wait(s):
    s.portal.docs["data/requirements.json"] = {
        "schema": "requirements/v1", "generatedAt": "2026-10-01T00:00:00Z", "locale": "zh-TW", "asOf": "2026-10-01", "requirements": [], "gates": [],
        "metricDefinitions": [{"metric": "TTFT", "definition": "第一個 token 的延遲。", "howMeasured": "p50。", "pitfalls": []}], "securityTestCatalog": [], "sources": []}
    s._baseline = None
    ev = named(s)
    rep = check(s, op("data/requirements.json", "add", "/sources/-", {"id": "anthropic-sonnet55-overview"}, [ev.id]))
    assert rep.ok, rep.errors
    assert any("data/requirements.json is maintained by the platform team" in w and "wait for a person" in w for w in rep.warnings), (rep.errors, rep.warnings)


def test_one_operation_that_makes_more_changes_than_are_published_automatically_is_said_to_wait(s):
    """(The limit counts changes: one operation with 61 of them is told like 61 operations.)"""
    row = add_quote(s, aws(s), ROW_H3)
    many = event(s)["notes"] + [f"第 {n} 則：AWS 表列 EOL 2026-09-12。" for n in range(61)]
    rep = check(s, op(LC, "replace", f"{H3}/notes", many, [row.id]))
    assert any("1 operations and makes 61 changes" in w and "more than 60" in w and "wait for a person" in w for w in rep.warnings), (rep.errors, rep.warnings)
    assert not any("more than 60" in w for w in check(s, op(LC, "replace", f"{H3}/notes", many[:-1], [row.id])).warnings)


def test_a_digest_entry_cannot_take_a_date_from_a_page_it_does_not_cite(s):
    row = add_quote(s, aws(s), ROW_H3)
    said(s, "anthropic-sonnet55-overview", "Claude Sonnet 5.5 - overview", "Claude Sonnet 5.5 will be generally available on 2031-05-06.")
    fix = op(LC, "replace", f"{H3}/date", "2026-09-12", [row.id])
    item = {"title": "Claude 3 Haiku 退役日更正", "detail": "Bedrock 頁面列出的 EOL 為 2031-05-06。", "impact": "medium", "sources": [{"id": "aws-bedrock-lifecycle-legacy"}]}
    digest = {"periodStart": "2026-09-25", "periodEnd": "2026-10-02", "headline": "本週摘要", "newModels": [], "lifecycleChanges": [item], "pricingChanges": [],
              "litellm": [], "security": [], "regulatory": [], "actionItems": [], "watchlist": [], "unverified": []}
    rep = validate_proposal(s, proposal([fix], digest=digest))
    assert not rep.ok and "the date 2031-05-06 is neither in a quote recorded from a page this entry cites" in errors(rep), rep.errors


# --------------------------------------------------------------------------- prose
def test_dates_and_amounts_in_prose_are_read_in_every_spelling_quotes_are_read_in(s):
    row = add_quote(s, aws(s), ROW_H3)
    for note in ("AWS 已公告延後：改於 2027年3月31日 退役。", "改於 2027/03/31 退役。", "改於 March 31, 2027 退役。", "改於 2027-03-31 退役。"):
        rep = check(s, op(LC, "add", f"{H3}/notes/-", note, [row.id]))
        assert not rep.ok and "mentions the date 2027-03-31 in prose" in errors(rep), note
    for note in ("延長期間單價調為 $99美元。", "單價調為$99起。", "單價調為 99 美元。", "單價調為 USD 99。", "單價調為 US$99。"):
        rep = check(s, op(LC, "add", f"{H3}/notes/-", note, [row.id]))
        assert not rep.ok and "in prose, but that amount is neither in the cited quotes nor in the record being edited" in errors(rep), note
    assert check(s, op(LC, "add", f"{H3}/notes/-", "AWS 表列 Legacy 2025年12月1日、EOL 2026/09/12；原記錄為 2026-09-10。", [row.id])).ok


# --------------------------------------------------------------------------- the digest
def digest(**over):
    d = {"periodStart": "2026-09-25", "periodEnd": "2026-10-02", "headline": "本週摘要", "newModels": [], "lifecycleChanges": [], "pricingChanges": [], "litellm": [],
         "security": [], "regulatory": [], "actionItems": [], "watchlist": [], "unverified": []}
    d.update(over)
    return d


def test_a_digest_reports_what_the_run_read(s):
    """No page fetched, no quote recorded: a front page that announces a retirement.  The date only had to occur somewhere
    in the portal (three Gemini events have it)."""
    item = {"title": "Bedrock：Claude Sonnet 5.5 將於 2026-10-20 下架", "detail": "AWS 宣布 Claude Sonnet 5.5 將於 2026-10-20 自 Bedrock 下架。", "impact": "high",
            "date": "2026-10-20", "sources": [{"id": "aws-bedrock-sonnet55-card"}]}
    urgent = digest(headline="緊急：AWS 宣布 Claude Sonnet 5.5 將於 2026-10-20 自 Bedrock 下架。", lifecycleChanges=[item])
    assert any(e["date"] == "2026-10-20" for e in s.portal.docs[LC]["events"])
    rep = validate_proposal(s, proposal([], digest=urgent))
    assert not rep.ok and "digest/lifecycleChanges/0/date: the date 2026-10-20 is neither in a quote recorded from a page this entry cites" in errors(rep)
    assert "digest/headline: the date 2026-10-20 is not one that an entry of this digest establishes" in errors(rep)
    # a source of its own making does not help: its title is the page's, whatever the proposal calls it
    history = page(s, "https://docs.aws.amazon.com/bedrock/latest/userguide/doc-history.html", html("Document history - Amazon Bedrock", "Claude Sonnet 5.5 was added."))
    titled = [{"id": "aws-doc-history", "url": history.url, "title": "Document history - Amazon Bedrock (2026-10-20)"}]
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[dict(item, sources=[{"id": "aws-doc-history"}])]), newSources=titled))
    assert not rep.ok and "the date 2026-10-20 is neither in a quote" in errors(rep)
    # an entry without any source
    rep = validate_proposal(s, proposal([], digest=digest(lifecycleChanges=[dict(item, sources=[])])))
    assert not rep.ok and "digest/lifecycleChanges/0: cites no source" in errors(rep)


def test_a_digest_entry_may_say_what_its_pages_and_its_records_say(s):
    row = add_quote(s, aws(s), ROW_H3)
    fix = op(LC, "replace", f"{H3}/date", "2026-09-12", [row.id])
    change = {"title": "Claude 3 Haiku 退役日更正", "detail": "Bedrock 頁面列出的 EOL 為 2026-09-12（portal 原記錄為 2026-09-10）。", "impact": "medium",
              "date": "2026-09-12", "sources": [{"id": "aws-bedrock-lifecycle-legacy"}]}
    # the new value (from the quote), the old one (the portal's record before this proposal), a deadline (a plan, not a
    # fact), and something that could not be confirmed (it says so itself)
    full = digest(headline="Claude 3 Haiku 退役日更正為 2026-09-12", lifecycleChanges=[change],
                  actionItems=[{"priority": "P1", "action": "確認已無 Claude 3 Haiku 流量。", "due": "2026-10-09"},
                               {"priority": "P0", "action": "Azure o3（2025-04-16）在 2026-11-19 退役前完成遷移。", "due": "2026-10-16"}])
    lead = [{"claim": "Claude 3.5 Haiku 可能於 2026-11-07 提前退役", "reason": "只有社群貼文"}]
    rep = validate_proposal(s, proposal([fix], digest=full, unverified=lead))
    # ... a lead is something the run came across: this date is on no page it read and in no search result it was shown
    assert rep.ok and list(rep.proposal["validation"]["flags"]) == ["digestUnsourced"], (rep.errors, rep.proposal and rep.proposal["validation"]["flags"])
    assert "digest/unverified/0: the date 2026-11-07 is on no page read in this run" in "\n".join(rep.warnings)
    page(s, REDDIT, html("thread", "Heard that Claude 3.5 Haiku might be retired early, on November 7, 2026."))
    rep = validate_proposal(s, proposal([fix], digest=full, unverified=lead))
    assert rep.ok, rep.errors
    assert rep.proposal["validation"]["flags"] == {}
    s.pages.popitem()
    s.result_texts.append("Claude 3.5 Haiku EOL moved?\nA thread says it could be retired on 2026-11-07 already.\n")
    rep = validate_proposal(s, proposal([fix], digest=full, unverified=lead))
    assert rep.ok and rep.proposal["validation"]["flags"] == {}, rep.errors
    # a price change: the old price is the portal's, the new one the quote's
    price = add_quote(s, page(s, "anthropic-pricing", ANTHROPIC_PRICING_HTML), PRICE_ROW)
    raise_ = [op(CAT, "replace", f"{SHEET}/input", 3, [price.id])]
    told = {"title": "Claude Sonnet 5.5 input 價格調整", "detail": "input 由 $2 調為 $3 / MTok。", "impact": "medium", "sources": [{"id": "anthropic-pricing"}]}
    assert validate_proposal(s, proposal(raise_, digest=digest(pricingChanges=[told]))).ok
    # ... and not any other number of the record (its context window is a number, not a price), nor an amount from nowhere
    for detail in ("input 由 $2 調為 $56 / MTok。", "input 由 $2 調為 $128000。", "input 調為 $99。"):
        rep = validate_proposal(s, proposal(raise_, digest=digest(pricingChanges=[dict(told, detail=detail)])))
        assert not rep.ok and "the amount $" in errors(rep), detail
    # repeating what the portal says about a model, from a page that was not read again: allowed, and for a person to see
    watch = {"title": "o3 將於 2026-11-19 退役", "detail": "Foundry 表列 o3 於 2026-11-19 退役。", "impact": "medium", "date": "2026-11-19",
             "sources": [{"id": "ms-foundry-retirement-schedule"}]}
    rep = validate_proposal(s, proposal([fix], digest=digest(lifecycleChanges=[change, watch])))
    assert rep.ok, rep.errors
    assert rep.proposal["validation"]["flags"] == {"digestNotReread": ["digest/lifecycleChanges/1 cites ms-foundry-retirement-schedule"]}
    assert any("only repeats what the portal already says" in w and "wait for a person" in w for w in rep.warnings)
    for action, good in (("Claude 3 Haiku 已於 2026-09-12 退役。", True), ("Claude 3 Haiku 將延到 2031-01-01。", False), ("o3 的 2026-09-12 期限。", True),
                         ("o1 將於 2026-10-20 退役。", False)):
        rep = validate_proposal(s, proposal([fix], digest=digest(lifecycleChanges=[change], actionItems=[{"priority": "P1", "action": action}])))
        assert rep.ok is good, (action, rep.errors)
    # ... while a date the portal has for ANOTHER model is not this entry's to use
    rep = validate_proposal(s, proposal([fix], digest=digest(lifecycleChanges=[change, dict(watch, title="o3 將於 2026-10-20 退役", detail="同上。", date="2026-10-20")])))
    assert not rep.ok and "the date 2026-10-20 is neither" in errors(rep)


# --------------------------------------------------------------------------- honest proposals that used to be turned down
def test_an_advisory_with_an_id_of_the_portals_own_making(s):
    """The real portal has PYPI-LITELLM-2026-03-24 and LITELLM-INCIDENT-...: no page prints such an id, so "the quote must
    mention the subject" could not be met; what can be asked is where the quote comes from."""
    notice = "https://github.com/BerriAI/litellm/security/advisories/GHSA-abcd-1234-wxyz"
    text = "Versions before 1.84.2 expose the spend logs endpoint without authentication. Fixed in 1.84.2, published 2026-09-30."
    ev = said(s, notice, "Spend logs endpoint without authentication · Advisory · BerriAI/litellm", text)
    assert ev.tier.verification == "official"
    advisory = {"id": "LITELLM-SPEND-LOGS-2026-09", "title": "spend logs endpoint 未驗證", "affected": "< 1.84.2", "fixedIn": "1.84.2", "severity": "high",
                "published": "2026-09-30", "summary": "1.84.2 之前的版本未驗證 spend logs endpoint。", "action": "升級到 1.84.2 以上。", "sources": [{"id": "litellm-ghsa-spend-logs"}]}
    rep = check(s, op(LL, "add", "/advisories/-", advisory, [ev.id]), newSources=[{"id": "litellm-ghsa-spend-logs", "url": notice}])
    assert rep.ok, rep.errors
    # the same advisory "read" in a news post: an advisory comes from an advisory database or from the project itself
    news = said(s, "https://daily.example/posts/litellm-spend-logs", "LiteLLM bug | daily", text)
    rep = check(s, op(LL, "add", "/advisories/-", dict(advisory, sources=[{"id": "daily-post"}]), [news.id]), newSources=[{"id": "daily-post", "url": news.url}])
    assert not rep.ok and "needs evidence from an advisory page" in errors(rep)
    # an id that a database gave is looked for in the quote
    cve = dict(advisory, id="CVE-2026-99999", sources=[{"id": "litellm-ghsa-spend-logs"}])
    rep = check(s, op(LL, "add", "/advisories/-", cve, [ev.id]), newSources=[{"id": "litellm-ghsa-spend-logs", "url": notice}])
    assert not rep.ok and "mention CVE-2026-99999" in errors(rep)


def test_half_an_emoji_does_not_end_the_run(s):
    """A lone surrogate (a broken "\\ud83d" escape) cannot be written to a file: the proposal was accepted and then could not be saved."""
    from research.store import save_result

    row = add_quote(s, aws(s), ROW_H3, note="note with half an emoji \ud83d")
    assert "\ud83d" not in row.note
    rep = validate_proposal(s, json.loads('{"summary": "退役日更正 \\ud83d", "ops": [{"file": "data/lifecycle.json", "op": "replace", "path": "%s/date", '
                                          '"value": "2026-09-12", "evidence": ["%s"], "rationale": "頁面寫明此值 \\ud83d"}]}' % (H3, row.id)))
    assert rep.ok, rep.errors
    json.dumps(rep.proposal, ensure_ascii=False).encode("utf-8")  # every string of the result can be written
    assert "�" in rep.proposal["summary"] and callable(save_result)
