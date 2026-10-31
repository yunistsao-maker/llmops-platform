"""Whose is the value? - the three answers of ``subjects.judge``.

``yes``: a program can be sure, in one of a few shapes.  ``no``: it is another model's, or nobody's.  ``unsure``:
everything in between - accepted, flagged, and held for a person by the publisher.  The cases are the ones the fourth
review used (wrong facts that were accepted) and the honest quotes of real page layouts that must not be turned away.
"""

import datetime as dt

import pytest

from research.portal.labels import versions_in
from research.proposal import LITELLM
from research.validate import dates as D
from research.validate.subjects import (ABSENT, NO, UNSURE, YES, date_finder, judge, mentions, monthday_finder, names_agree, number_finder,
                                        portal_subjects, subject_from, subject_of, version_finder, versions_at, without_names)

KNOWN = portal_subjects({
    "data/catalog.json": {"models": [{"id": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5", "vendorModelId": "claude-sonnet-5-5"},
                                     {"id": "gpt-6-sol", "name": "GPT-6 Sol"}, {"id": "grok-4-fast", "name": "Grok 4 Fast"}]},
    "data/lifecycle.json": {"events": [{"id": "a", "modelName": "Claude 3 Haiku", "providerModelId": "anthropic.claude-3-haiku-20240307-v1:0"},
                                       {"id": "b", "modelName": "Claude Haiku 4.5", "providerModelId": "claude-haiku-4-5"}, {"id": "c", "modelName": "o3-mini"}]},
})
H3 = subject_from(["Claude 3 Haiku", "anthropic.claude-3-haiku-20240307-v1:0"])
S45 = subject_from(["Claude Sonnet 4.5", "claude-sonnet-4-5"])
O45 = subject_from(["Claude Opus 4.5", "claude-opus-4-5"])
S55 = subject_from(["Claude Sonnet 5.5", "claude-sonnet-5-5"])
G4O = subject_from(["gpt-4o", "gpt-4o-2024-05-13"])
G25 = subject_from(["Gemini 2.5 Flash", "gemini-2.5-flash"])
RELEASE = subject_of({"version": "v1.104.0", "channel": "stable", "supported": True})
ROW = "Claude 3 Haiku | anthropic.claude-3-haiku-20240307-v1:0 | 2025-12-01 | 2026-09-12"
MIGRATE = "We recommend migrating from Claude Sonnet 4.5 to Claude Sonnet 5.5 before November 30, 2026."
HEAD = "Model | Suggested replacement | Retirement date"
TWO = "Claude 3 Haiku will reach end-of-life on September 12, 2026 and Stable Diffusion XL 1.0 on October 15, 2026."
ROWS = ROW + "\nCommand R+ | cohere.command-r-plus-v1:0 | 2026-03-01 | 2026-10-15"


def day(text):
    return date_finder(dt.date.fromisoformat(text))


# (what it shows, subject, quote, context, finder, heads, the answer)
CASES = [
    ("the model's own row", H3, ROW, "", day("2026-09-12"), (), YES),
    # --- two models in front of one value (A2)
    ("'migrating from A to B before <date>', read for A", S45, MIGRATE, "", day("2026-11-30"), (), UNSURE),
    ("... and for B", S55, MIGRATE, "", day("2026-11-30"), (), UNSURE),
    ("a row with a replacement column, read for the model", S45, "claude-sonnet-4-5 | claude-sonnet-5-5 | 2026-11-30", "", day("2026-11-30"), (HEAD,), YES),
    ("... and for the replacement", S55, "claude-sonnet-4-5 | claude-sonnet-5-5 | 2026-11-30", "", day("2026-11-30"), (HEAD,), NO),
    ("the same row without its column headings", S45, "claude-sonnet-4-5 | claude-sonnet-5-5 | 2026-11-30", "", day("2026-11-30"), (), UNSURE),
    ("... for the second name", S55, "claude-sonnet-4-5 | claude-sonnet-5-5 | 2026-11-30", "", day("2026-11-30"), (), UNSURE),
    ("no column headings, but the cell says what the second name is: read for the model", S45,
     "claude-sonnet-4-5 | Replacement: claude-sonnet-5-5 | 2026-11-30", "", day("2026-11-30"), (), YES),
    ("... and for the replacement", S55, "claude-sonnet-4-5 | Replacement: claude-sonnet-5-5 | 2026-11-30", "", day("2026-11-30"), (), NO),
    ("... said in Chinese", S55, "claude-sonnet-4-5 | 建議改用 claude-sonnet-5-5 | 2026-11-30", "", day("2026-11-30"), (), NO),
    ("the replacement stands behind the value", S45, "claude-sonnet-4-5 | 1 | GA | 2026-11-30 | claude-sonnet-5-5", "", day("2026-11-30"), (), YES),
    ("... read for the replacement", S55, "claude-sonnet-4-5 | 1 | GA | 2026-11-30 | claude-sonnet-5-5", "", day("2026-11-30"), (), NO),
    # --- names this code does not know (A3)
    ("its own date in a sentence with an unknown name behind it", H3, TWO, "", day("2026-09-12"), (), YES),
    ("the unknown name's date", H3, TWO, "", day("2026-10-15"), (), UNSURE),
    ("two rows quoted, the date of the unknown one", H3, ROWS, "", day("2026-10-15"), (), UNSURE),
    ("two rows quoted, its own date", H3, ROWS, "", day("2026-09-12"), (), YES),
    ("a row with a name nobody knows, under the model's own heading", H3, "Command R+ | cohere.command-r-plus-v1:0 | 2026-03-01 | 2026-10-15",
     "docs bedrock lifecycle\nModel lifecycle - Amazon Bedrock\nClaude 3 Haiku", day("2026-10-15"), (), UNSURE),
    ("... and a row that names nobody, under that heading", H3, "Legacy | 2025-12-01 | 2026-09-12",
     "docs bedrock lifecycle\nModel lifecycle - Amazon Bedrock\nClaude 3 Haiku", day("2026-09-12"), (), YES),
    ("the name goes on with capitalised words", G4O, "GPT-4o Audio Preview will be retired on October 15, 2026.", "", day("2026-10-15"), (), UNSURE),
    ("the name goes on as an identifier", G4O, "gpt-4o-audio-preview will be retired on October 15, 2026.", "", day("2026-10-15"), (), NO),
    ("Gemini 2.5 Flash Live API", G25, "Gemini 2.5 Flash Live API | October 9, 2026", "", day("2026-10-09"), (), UNSURE),
    ("Gemini 2.5 Flash Native Audio", G25, "Gemini 2.5 Flash Native Audio | October 9, 2026", "", day("2026-10-09"), (), UNSURE),
    ("Gemini 2.5 Flash Image", G25, "Gemini 2.5 Flash Image | October 9, 2026", "", day("2026-10-09"), (), UNSURE),
    ("Gemini 2.5 Flash-Lite", G25, "Gemini 2.5 Flash-Lite | October 9, 2026", "", day("2026-10-09"), (), NO),
    ("Gemini 2.5 Flash", G25, "Gemini 2.5 Flash | October 9, 2026", "", day("2026-10-09"), (), YES),
    ("GPT-4o Transcribe", G4O, "GPT-4o Transcribe | October 15, 2026", "", day("2026-10-15"), (), UNSURE),
    ("GPT-4o mini", G4O, "GPT-4o mini | October 15, 2026", "", day("2026-10-15"), (), NO),
    ("GPT-4o", G4O, "GPT-4o | October 15, 2026", "", day("2026-10-15"), (), YES),
    ("a release: the row of another release", RELEASE, "1.104.0 | Oct 1, 2026\n1.103.2 | Sep 30, 2026", "pypi litellm\nlitellm · PyPI", day("2026-09-30"), (), NO),
    ("a release: its own row", RELEASE, "1.104.0 | Oct 1, 2026\n1.103.2 | Sep 30, 2026", "pypi litellm\nlitellm · PyPI", day("2026-10-01"), (), YES),
    # --- honest sentences that name more than one thing (A18)
    ("two models share a date", H3, "Claude 3 Haiku and Claude 3.5 Haiku will be retired on October 15, 2026.", "", day("2026-10-15"), (), YES),
    ("... the date in front, the subject second in the list", S45, "On November 30, 2026, Claude Opus 4.5 and Claude Sonnet 4.5 will be retired.", "", day("2026-11-30"), (), YES),
    ("... and first", O45, "On November 30, 2026, Claude Opus 4.5 and Claude Sonnet 4.5 will be retired.", "", day("2026-11-30"), (), YES),
    ("a shared cell", S45, "claude-sonnet-4-5, claude-opus-4-5 | 1 | GA | 2026-11-30", "", day("2026-11-30"), (), YES),
    ("the date first, then a shared cell", S45, "November 30, 2026 | claude-opus-4-5, claude-sonnet-4-5 | claude-sonnet-5-5", "", day("2026-11-30"), (), YES),
    ("'the same as'", S55, "Claude Sonnet 5.5 costs the same as Claude Sonnet 5: retiring October 15, 2026.", "", day("2026-10-15"), (), YES),
    ("a product of the family is not a model", H3, "Claude Code now uses Claude 3 Haiku until October 15, 2026.", "", day("2026-10-15"), (), YES),
    ("plans are not models", H3, "Claude Pro and Max subscribers keep Claude 3 Haiku until October 15, 2026.", "", day("2026-10-15"), (), YES),
    ("the family is not a model", H3, "Claude 3 Haiku, part of the Claude 3 family, retires on October 15, 2026.", "", day("2026-10-15"), (), YES),
    # --- lines that name nobody
    ("a change log: heading and item quoted together", S55, "#### September 28, 2026\n- We've launched Claude Sonnet 5.5, our most capable balanced model.", "",
     day("2026-09-28"), (), YES),
    ("the line under the model's own heading", H3, "Retirement date: September 12, 2026", "/docs/models/deprecations\nModel deprecations\nDeprecations\nClaude 3 Haiku",
     day("2026-09-12"), (), YES),
    ("the line under a heading with a name nobody knows", H3, "Retirement date: September 12, 2026",
     "/docs/x\nClaude 3 Haiku - model card\nClaude 3 Haiku\nCompared with Command R+", day("2026-09-12"), (), UNSURE),
    ("is the quote about the model at all: yes", H3, ROW, "", None, (), YES),
    ("... no: nobody is named anywhere", H3, "Dates are in UTC and may move.", "bedrock latest userguide eol-table.html", None, (), NO),
    ("a line that names nobody, after a line of the model with a value of its own", H3, ROW.replace("2026-09-12", "2026-09-10") + "\nEnd of life: 2026-09-12", "",
     day("2026-09-12"), (), UNSURE),
    ("the row of another model backs nothing", H3, "Claude 3.5 Haiku | anthropic.claude-3-5-haiku-20241022-v1:0 | 2026-02-01 | 2026-12-01", "", day("2026-12-01"), (), NO),
    # --- a sentence that begins with its value: it is whose name stands right behind it
    ("the date first, another model right behind it", H3, "On October 15, 2026, Claude 3.5 Haiku will be retired.", "", day("2026-10-15"), (), NO),
    ("... also under the model's own heading", H3, "On October 15, 2026, Claude 3.5 Haiku will be retired.",
     "/docs/models/deprecations\nModel deprecations\nDeprecations\nClaude 3 Haiku", day("2026-10-15"), (), NO),
    ("the date first, then the model to move to: the heading says whose date it is", H3, "By October 15, 2026, migrate to Claude 3.5 Haiku.",
     "/docs/models/deprecations\nModel deprecations\nDeprecations\nClaude 3 Haiku", day("2026-10-15"), (), YES),
    ("... and without a heading nobody does", H3, "By October 15, 2026, migrate to Claude 3.5 Haiku.", "", day("2026-10-15"), (), NO),
    ("the value is not in the quote", H3, ROW, "", day("2026-12-01"), (), ABSENT),
    # --- a day in the heading the quote stands under (A19)
    ("a change log: the item alone, the day is its heading", S55, "- We've launched Claude Sonnet 5.5, our most capable balanced model.",
     "docs en release-notes\nRelease notes - Claude API Docs\nRelease notes\nSeptember 28, 2026", day("2026-09-28"), (), YES),
    ("... an item about two models", S55, "- We've launched Claude Sonnet 5.5 and retired Claude Sonnet 4.",
     "docs en release-notes\nRelease notes - Claude API Docs\nRelease notes\nSeptember 28, 2026", day("2026-09-28"), (), UNSURE),
    ("... an item about another model", S55, "- We've launched Claude Opus 5.5.",
     "docs en release-notes\nRelease notes - Claude API Docs\nRelease notes\nSeptember 22, 2026", day("2026-09-22"), (), NO),
    ("... the nearest dated heading is another day", S55, "- We've launched Claude Sonnet 5.5.",
     "docs en release-notes\nRelease notes - Claude API Docs\nSeptember 28, 2026\nSeptember 22, 2026", day("2026-09-28"), (), ABSENT),
    ("... a day in the page's TITLE is not the day of every line", S55, "- We've launched Claude Sonnet 5.5.",
     "docs en release-notes\nRelease notes, September 28, 2026\nRelease notes", day("2026-09-28"), (), ABSENT),
    ("an amount is not read from a heading", S55, "- Claude Sonnet 5.5 is now generally available.",
     "docs en pricing\nPricing\nPricing\n$3 per MTok", number_finder(3), (), ABSENT),
    # --- a date with two readings, a figure that may belong to a name (A12)
    ("11/10/2026: the US reading", S45, "claude-sonnet-4-5 | 1 | GA | Retirement date: 11/10/2026 | claude-sonnet-5-5", "", day("2026-11-10"), (), UNSURE),
    ("11/10/2026: the other reading", S45, "claude-sonnet-4-5 | 1 | GA | Retirement date: 11/10/2026 | claude-sonnet-5-5", "", day("2026-10-11"), (), UNSURE),
    ("13/10/2026 has one reading", S45, "claude-sonnet-4-5 | Retirement date: 13/10/2026", "", day("2026-10-13"), (), YES),
    ("the 4.5 of a name and a month are no date", subject_from(["Claude Haiku 4.5"]), "Planned retirements: Claude Haiku 4.5 October 2026 (exact day to be announced).", "",
     day("2026-10-05"), (), ABSENT),
    ("'Tier 2 November 2026' may be a tier and a month", H3, "Claude 3 Haiku moves to Tier 2 November 2026.", "", day("2026-11-02"), (), UNSURE),
    # --- a day without its year
    ("a day without a year, found in a full date", H3, ROW, "", monthday_finder(9, 12), (), YES),
    ("... and in a short one", H3, "Claude 3 Haiku 將於 9 月 12 日退役。", "", monthday_finder(9, 12), (), YES),
    # --- amounts
    ("a price behind its model", S55, "Claude Sonnet 5.5 | $3 / MTok | $15 / MTok", "", number_finder(15, True), (), YES),
    ("per 1,000 tokens, read for a price", S55, "Claude Sonnet 5.5 | $0.003 per 1,000 input tokens", "", number_finder(3, True), (), YES),
    ("per 1,000 tokens, read for something that is no price", S55, "Claude Sonnet 5.5 | $0.003 per 1,000 input tokens", "", number_finder(3, False), (), ABSENT),
    ("'per 1,000' of something else on the line does not scale the prices",
     S55, "Claude Sonnet 5.5 costs $3 per million input tokens; web search is billed at $10 per 1,000 searches.", "", number_finder(3000, True), (), ABSENT),
    ("per 1,000 in the heading of the column", S55, "Claude Sonnet 5.5 | $0.003 | $0.015", "", number_finder(15, True), ("Model | Input per 1,000 tokens | Output per 1,000 tokens",), YES),
    ("the figures of a name are no amount", S55, "Claude Sonnet 5.5 is our balanced model.", "", number_finder(5.5), (), ABSENT),
    ("a time of day and a status code are no amounts", S55, "Claude Sonnet 5.5 pricing changes at 00:00 UTC; requests that fail with HTTP 429 are not billed.", "",
     number_finder(429), (), ABSENT),
    ("1M in Japanese text", S55, "Claude Sonnet 5.5 のコンテキストウィンドウは 100万トークンです。", "", number_finder(1_000_000), (), YES),
]


@pytest.mark.parametrize("subject, quote, context, find, heads, want", [pytest.param(*c[1:], id=c[0]) for c in CASES])
def test_whose_the_value_is(subject, quote, context, find, heads, want):
    verdict = judge(subject, quote, context, find, KNOWN, heads, min(2, len([x for x in context.split("\n") if x.strip()])))
    assert verdict.status == want, verdict.why
    assert (verdict.why != "") == (want in (UNSURE, NO)) or want == NO, "an answer that is not yes says why"


# --------------------------------------------------------------------------- a piece of software (LiteLLM)
DOCS = "docs.litellm.ai\nrelease_notes\nRelease notes | LiteLLM"
RELEASES = "github.com\nBerriAI litellm releases\nReleases · BerriAI/litellm"
PYPI = "pypi.org\nproject litellm\nlitellm · PyPI"
AWS = "docs.aws.amazon.com\nbedrock latest userguide model-card\nClaude Sonnet 5.5 - Amazon Bedrock"
NVD = "nvd.nist.gov\nvuln detail CVE-2026-11111\nNVD - CVE-2026-11111"
PRODUCT = [
    # (context, quote, the version asked for, the answer)
    (DOCS, "Requires Python 3.9.2 or later.", "3.9.2", ABSENT),                    # another thing's version is not found at all
    (DOCS, "LiteLLM requires Python 3.9.2 or later.", "3.9.2", ABSENT),
    (DOCS, "requires python>=3.9.2", "3.9.2", ABSENT),
    (PYPI, "orjson>=3.9.7", "3.9.7", ABSENT),
    (DOCS, "Upgrade to v1.104.0 or later. Redis 7.2 is required for the new cache.", "7.2", ABSENT),
    (DOCS, "LiteLLM v1.104.0 drops Python 3.8 support", "3.8", ABSENT),
    (DOCS, "LiteLLM v1.104.0 drops Python 3.8 support", "1.104.0", YES),
    (DOCS, "Upgrade to v1.104.0 or later. Redis 7.2 is required for the new cache.", "1.104.0", YES),  # ... and does not take the line away
    (PYPI, "litellm 1.104.0", "1.104.0", YES),
    (PYPI, "pip install litellm==1.104.0", "1.104.0", YES),
    (PYPI, "litellm-1.104.0-py3-none-any.whl", "1.104.0", YES),
    (RELEASES, "v1.104.0-stable", "1.104.0", YES),
    (RELEASES, "docker pull docker.litellm.ai/berriai/litellm:1.104.0", "1.104.0", YES),
    (RELEASES, "docker pull ghcr.io/berriai/litellm:main-v1.104.0-stable", "1.104.0", YES),
    (DOCS, "LiteLLM v1.74.9-stable.patch.1 is the current stable release.", "1.74.9-stable.patch.1", YES),  # a version is read with all of its tags
    (DOCS, "LiteLLM v1.74.9-stable.patch.1 is the current stable release.", "1.74.9", ABSENT),
    (PYPI, "litellm-proxy-extras 0.2.1", "0.2.1", UNSURE),                         # another package of the family: who knows
    (DOCS, "Bumps openai from 1.99.4 to 1.99.5", "1.99.5", UNSURE),
    (AWS, "The AWS SDK for Python (Boto3) version 1.40.0 adds support for Claude Sonnet 5.5 in Amazon Bedrock.", "1.40.0", NO),  # nothing names LiteLLM
    (AWS, "LiteLLM 1.104.0 or later is required to call Claude Sonnet 5.5 through Amazon Bedrock.", "1.104.0", YES),
    (NVD, "CVE-2026-11111: LangChain before 0.3.9 allows remote code execution via crafted prompt templates. Fixed in 0.3.9.", "0.3.9", NO),
    (NVD.replace("11111", "12345"), "CVE-2026-12345: LiteLLM before 1.105.0 leaks virtual keys in debug logs. Fixed in 1.105.0.", "1.105.0", YES),
    ("github.com\nadvisories GHSA-4xpc-pv4p-pm3w\nHost header bypass", "Package | Affected versions | Patched versions\nlitellm (pip) | < 1.84.0 | 1.84.0", "1.84.0", YES),
    (DOCS, "LiteLLM now supports Claude Sonnet 5.5.", "5.5", ABSENT),              # a model's number is no release
    (DOCS, "LiteLLM Enterprise costs $1.50 per seat.", "1.50", ABSENT),            # nor is a price
]


@pytest.mark.parametrize("context, quote, version, want", PRODUCT, ids=[f"{c[1][:50]} [{c[2]}]" for c in PRODUCT])
def test_a_version_on_a_page_about_litellm_is_litellms_or_somebody_elses(context, quote, version, want):
    asked = versions_in(version)[0]  # (as a field's value is read: "v1.74.9-stable.patch.1" is one version with its tags)
    assert judge(LITELLM, quote, context, version_finder(asked), KNOWN, (), 2).status == want


def test_a_product_is_called_what_it_is_called():
    """A model may be meant by a name this code does not know; LiteLLM is called LiteLLM - by the quote, the headings
    above it, the title of the page or its address.  Where none of them does, the quote is not about it."""
    quote = "v1.104.0-stable is the latest stable release."
    assert judge(LITELLM, quote, "docs.litellm.ai\nrelease_notes\nRelease notes", version_finder("1.104.0"), (), (), 2).status == YES  # the host
    assert judge(LITELLM, quote, "github.com\nBerriAI litellm releases\nReleases", version_finder("1.104.0"), (), (), 2).status == YES  # the path
    verdict = judge(LITELLM, quote, "github.com\nsomebody fork releases\nReleases", version_finder("1.104.0"), (), (), 2)
    assert verdict.status == NO and "LiteLLM" in verdict.why
    assert judge(LITELLM, quote, "github.com\nsomebody fork releases\nReleases", None, (), (), 2).status == NO


# --------------------------------------------------------------------------- names, ids, versions
def test_an_advisory_is_found_by_its_whole_id():
    cve = subject_of({"id": "CVE-2026-1234", "title": "x"})
    assert cve.external == ("CVE-2026-1234",) and mentions(cve, "Fixes CVE-2026-1234.") and mentions(cve, "cve-2026-1234: details")
    assert not mentions(cve, "Fixes CVE-2026-12345.") and not mentions(cve, "CVE-2026-1234-b")
    # other databases have ids too; an id somebody made up is nothing a page could be searched for
    assert subject_of({"id": "PYSEC-2026-77"}).external == ("PYSEC-2026-77",) and subject_of({"id": "GHSA-4xpc-pv4p-pm3w"}).external
    assert not subject_of({"id": "LITELLM-INCIDENT-GUARDRAIL-LOGGING"}) and not subject_of({"id": "azure-2026-10"})


def test_the_names_of_one_record_are_names_of_one_model():
    assert names_agree("Claude 3 Haiku", "anthropic.claude-3-haiku-20240307-v1:0") and names_agree("GPT-6 Sol", "openai.gpt-6-sol")
    assert not names_agree("GPT-6 Sol", "anthropic.claude-3-haiku-20240307-v1:0") and not names_agree("Claude 3 Haiku", "anthropic.claude-sonnet-5-5")
    assert names_agree("GPT", "anything-1")  # a name that is no usable name has nothing in it to disagree with


def test_versions_are_read_whole_and_only_where_a_version_stands():
    def found(text):
        return [version for _start, _end, version in versions_at(text)]

    assert found("litellm v1.74.9-stable.patch.1 is current") == ["1.74.9-stable-patch1"]
    assert found("1.103.0rc1 and 1.103.0.dev2, v1.102.x") == ["1.103.0-rc1", "1.103.0-dev2", "1.102.x"]
    assert found("claude-sonnet-5-5 and gpt-6.1-sol on 2026-10-03 cost $3.75") == []  # names, dates and prices hold no versions
    assert found("image :main-v1.104.0-stable, file -1.104.0-py3-none-any.whl") == ["1.104.0-stable", "1.104.0"]


def test_dates_are_read_from_a_text_without_its_names():
    def dates(text):
        return [(f.day.isoformat(), bool(f.doubt)) for f in D.date_spans(without_names(text, KNOWN))]

    assert dates("GPT-5 March 2027") == [] and dates("Gemini 3 December 2026") == [] and dates("Llama 4 April 2027") == []
    assert dates("Claude Opus 4 June 2027") == [] and dates("Mistral Large 3 January 2027") == []  # the figure behind a name is the rest of the name
    assert dates("Retires on 5 March 2027") == [("2027-03-05", False)] and dates("Claude Opus 4.1 14 October 2026") == [("2026-10-14", False)]
    assert dates("Tier 2 November 2026") == [("2026-11-02", True)] and dates("Phase 3 December 2026 rollout") == [("2026-12-03", True)]
