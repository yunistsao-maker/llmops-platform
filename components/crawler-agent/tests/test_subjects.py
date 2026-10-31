"""Is this quote about that model?  (research/validate/subjects.py)

A lifecycle table has a row per model and the names differ by a digit: these are the cases a proposal must not get
past - and the honest spellings it must not be stopped by.
"""

import datetime as dt

import pytest

from research.evidence import context_for
from research.session import Page
from research.validate.subjects import (NO, UNSURE, YES, Subject, date_finder, judge, mentions, names_agree, names_another, number_finder,
                                        parts_of, portal_subjects, stands_with, subject_from, subject_of, version_finder)
from research.web.tiers import Tier


def S(*names):
    return subject_from(names)


@pytest.mark.parametrize("name, text", [
    ("Claude Sonnet 5.5", "Claude Sonnet 5.5 | $2 / MTok"),
    ("Claude Sonnet 5.5", "claude-sonnet-5-5"),
    ("Claude Sonnet 5.5", "anthropic.claude-sonnet-5-5-20260928-v1:0"),
    ("Claude Sonnet 5.5", "global.anthropic.claude-sonnet-5-5"),
    ("Claude Sonnet 5.5", "CLAUDE  SONNET 5.5."),
    ("Claude Sonnet 5.5", "Sonnet 5.5 costs the same as Sonnet 5"),            # Anthropic's own short form
    ("Claude Sonnet 5.5", "Claude Sonnet 5.5 (max effort) scores 56"),
    ("Claude Sonnet 5.5", "Claude Sonnet 5.5 max effort scores 56"),            # a sentence about it, not a model "… Max"
    ("Claude Sonnet 5.5", "Claude Sonnet 5.5 code execution is billed separately"),
    ("Claude 3 Haiku", "anthropic.claude-3-haiku-20240307-v1:0"),
    ("Claude Sonnet 4", "anthropic.claude-sonnet-4-20250514-v1:0"),
    ("Amazon Nova Premier", "Nova Premier | amazon.nova-premier-v1:0"),           # the vendor's name is left out as often as not
    ("gpt-4o", "gpt-4o (2024-08-06)"),
    ("gpt-4o", "gpt-4o-2024-08-06"),
    ("gpt-4.1", "GPT-4.1 retires on 2026-10-14"),
    ("o3", "o3 | 2025-04-16 | 2026-12-01"),
    ("o3", "o3-2025-04-16"),
    ("o1", "o1 (2024-12-17)"),
    ("GPT-6 Sol", "gpt-6-sol"),
    ("GPT-6 Sol", "openai.gpt-6-sol"),
    ("Gemini 3.8 Flash", "gemini-3.8-flash"),
    ("DeepSeek-V4-Flash-0731", "DeepSeek-V4-Flash-0731"),
    ("codex-mini", "codex-mini | 2025-05-16"),
])
def test_a_model_is_named_however_its_name_is_spelled(name, text):
    assert mentions(S(name), text), (parts_of(name), text)


@pytest.mark.parametrize("name, text", [
    ("Claude Sonnet 5.5", "Claude Sonnet 5 | $3 / MTok"),                         # the predecessor's row
    ("Claude Sonnet 5", "Claude Sonnet 5.5 | $2 / MTok"),
    ("Claude Sonnet 5", "claude-sonnet-5-5"),
    ("Claude 3.5 Haiku", "Claude 3 Haiku | anthropic.claude-3-haiku-20240307-v1:0"),
    ("Claude 3 Haiku", "Claude 3.5 Haiku | anthropic.claude-3-5-haiku-20241022-v1:0"),
    ("Claude Sonnet 4", "claude-sonnet-4-5-20250929"),
    ("Claude Sonnet 4", "Claude Sonnet 4.5"),
    ("Claude Opus 4.1", "Claude Opus 4.5"),
    ("claude-opus-5", "Claude Opus 5.5"),
    ("gpt-image-1", "gpt-image-1.5"),
    ("gpt-4o", "gpt-4o-mini (2024-07-18)"),
    ("gpt-4o", "gpt-4o-transcribe"),
    ("gpt-4o", "chatgpt-4o-latest"),
    ("gpt-4.1", "gpt-4.1-mini"),
    ("gpt-4.1", "gpt-4.1-nano"),
    ("gpt-4.1", "gpt-4.10"),
    ("o3", "o3-mini | 2025-01-31"),
    ("o3", "o3-pro"),
    ("o3", "o3-deep-research"),
    ("o3", "o3 mini"),
    ("o1", "o1-pro"),
    ("GPT-6", "GPT-6 Sol scores 61"),
    ("GPT-6", "gpt-6.1-sol"),
    ("gemini-2.5-flash", "gemini-2.5-flash-lite"),
    ("gemini-2.5-flash", "gemini-2.5-flash-image"),
    ("gemini-2.5-flash", "Gemini 2.5 Flash Lite"),
    ("gemini-2.5-pro", "gemini-2.5-flash"),
    ("Amazon Nova Premier", "Nova Pro | amazon.nova-pro-v1:0"),
    ("Amazon Nova Sonic (v1)", "Nova Sonic v2"),
    ("sora-2", "sora-2-pro"),
    ("Claude Sonnet 5.5", "Artificial Analysis Intelligence Index v4.3.2"),
    ("Claude Sonnet 5.5", "Sonnet"),
    ("Claude Sonnet 5.5", "5.5"),
])
def test_another_models_name_is_not_this_models_name(name, text):
    assert not mentions(S(name), text), (parts_of(name), text)


def test_one_field_may_name_several_models_and_each_counts_whole():
    """The portal has records for a family ("gpt-4.1 / gpt-4.1-mini"); the pieces of such a list that are not names of
    their own ("flash-lite", "v1:1") do not become subjects."""
    family = S("gemini-2.5-pro / flash / flash-lite（Gemini API）", "gemini-2.5-pro, gemini-2.5-flash, gemini-2.5-flash-lite")
    assert mentions(family, "gemini-2.5-flash-lite | 2026-10-16") and mentions(family, "Gemini 2.5 Pro") and mentions(family, "gemini-2.5-flash")
    assert not mentions(family, "gemini-2.5-flash-image") and not mentions(family, "gemini-3.8-flash") and not mentions(family, "Flash-Lite pricing")
    mai = S("MAI-Image-2.5 / -Pro / -Flash", "MAI-Image-2.5, MAI-Image-2.5-Pro, MAI-Image-2.5-Flash")
    assert mentions(mai, "MAI-Image-2.5-Pro") and mentions(mai, "MAI-Image-2.5") and not mentions(mai, "MAI-Image-3") and not mentions(mai, "Image 2.5")
    azure = S("gpt-4o / gpt-4o-mini", "gpt-4o (2024-08-06, 2024-11-20), gpt-4o-mini (2024-07-18)")
    assert mentions(azure, "gpt-4o-mini") and mentions(azure, "gpt-4o") and not mentions(azure, "gpt-4o-transcribe")
    reel = S("Amazon Nova Reel", "amazon.nova-reel-v1:0 / v1:1")
    assert mentions(reel, "amazon.nova-reel-v1:1") and not mentions(reel, "v1:1") and not mentions(reel, "amazon.nova-canvas-v1:0")
    audio = S("whisper / tts / tts-hd", "whisper, tts, tts-hd")                 # no entry has a number: the entries have to do
    assert mentions(audio, "whisper | 2026-12-01") and mentions(audio, "tts-hd") and not mentions(audio, "gpt-4o-mini-tts")


def test_what_a_record_is_about():
    event = {"id": "bedrock-claude-3-haiku-eol", "modelName": "Claude 3 Haiku", "providerModelId": "anthropic.claude-3-haiku-20240307-v1:0"}
    assert subject_of(event).names == ("Claude 3 Haiku", "anthropic.claude-3-haiku-20240307-v1:0")
    assert not subject_of({"id": "x", "name": "R"}) and not subject_of({"id": "only-an-id"}) and not subject_of("text") and not subject_of(None)
    assert subject_of({"modelName": "o3", "providerModelId": "o3"})              # a short name is a name
    # an advisory is known by the id databases give it, a self-made id names nothing a page prints
    assert subject_of({"id": "CVE-2026-12345", "title": "t"}).external == ("CVE-2026-12345",)
    assert subject_of({"id": "GHSA-abcd-1234-wxyz"}).external == ("GHSA-abcd-1234-wxyz",)
    assert not subject_of({"id": "PYPI-LITELLM-2026-03-24", "title": "PyPI 供應鏈攻擊"})
    assert mentions(subject_of({"id": "CVE-2026-12345"}), "Fixed in 1.84.2 (CVE-2026-12345)") and not mentions(subject_of({"id": "CVE-2026-12345"}), "CVE-2026-1234")
    # a release is about its version
    release = subject_of({"version": "v1.104.0", "channel": "stable", "supported": True})
    assert release.versions == ("1.104.0",) and mentions(release, "LiteLLM v1.104.0-stable is out") and not mentions(release, "LiteLLM v1.104.1 is out")
    assert not subject_of({"version": "v4.3.2", "name": "A", "score": 5}).versions  # (a benchmark's version is not a release)


KNOWN = portal_subjects({
    "data/catalog.json": {"models": [{"id": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5", "vendorModelId": "claude-sonnet-5-5"},
                                     {"id": "gpt-6-sol", "name": "GPT-6 Sol"}, {"id": "grok-4-fast", "name": "Grok 4 Fast"}]},
    "data/lifecycle.json": {"events": [{"id": "a", "modelName": "Claude 3 Haiku", "providerModelId": "anthropic.claude-3-haiku-20240307-v1:0"},
                                       {"id": "b", "modelName": "Claude Haiku 4.5", "providerModelId": "claude-haiku-4-5"}, {"id": "c", "modelName": "o3-mini"}]},
})


def date(day):
    return date_finder(dt.date.fromisoformat(day))


def number(value):
    return number_finder(value)


HAIKU3 = S("Claude 3 Haiku", "anthropic.claude-3-haiku-20240307-v1:0")
HAIKU35 = S("Claude 3.5 Haiku", "anthropic.claude-3-5-haiku-20241022-v1:0")
SONNET = S("Claude Sonnet 5.5", "claude-sonnet-5-5")
ROW_H3 = "Claude 3 Haiku | anthropic.claude-3-haiku-20240307-v1:0 | 2025-12-01 | 2026-09-12"
ROW_H35 = "Claude 3.5 Haiku | anthropic.claude-3-5-haiku-20241022-v1:0 | 2026-02-01 | 2026-12-01"
TABLE = "/bedrock/latest/userguide/model-lifecycle.html\nModel lifecycle - Amazon Bedrock\nModel lifecycle\nLegacy and EOL models"


def test_a_value_belongs_to_the_model_named_on_its_line():
    assert stands_with(HAIKU3, ROW_H3, TABLE, date("2026-09-12"), KNOWN)
    assert not stands_with(HAIKU3, ROW_H35, TABLE, date("2026-12-01"), KNOWN)
    assert not stands_with(HAIKU35, ROW_H3, TABLE, date("2026-09-12"), KNOWN)
    # two rows in one quote: each date stays with its row
    both = ROW_H3 + "\n" + ROW_H35
    assert stands_with(HAIKU3, both, TABLE, date("2026-09-12"), KNOWN) and stands_with(HAIKU35, both, TABLE, date("2026-12-01"), KNOWN)
    assert not stands_with(HAIKU3, both, TABLE, date("2026-12-01"), KNOWN) and not stands_with(HAIKU35, both, TABLE, date("2026-09-12"), KNOWN)
    # a quote that starts in the middle of one row and ends in the next: the date of one, the name of the other
    straddling = "2025-12-01 | 2026-09-12\nClaude 3.5 Haiku | anthropic.claude-3-5-haiku-20241022-v1:0"
    assert not stands_with(HAIKU35, straddling, TABLE, date("2026-09-12"), KNOWN)
    # the value is not there at all
    assert not stands_with(HAIKU3, ROW_H3, TABLE, date("2031-01-01"), KNOWN)
    # nothing to look for (a record that names no model): the value being there is all that can be asked
    assert stands_with(Subject(()), ROW_H35, TABLE, date("2026-12-01"), KNOWN)


def test_on_one_line_a_value_stands_behind_the_name_in_front_of_it():
    line = "o3 retires on 2026-12-01 and o4-mini retires on 2027-03-01."
    o3, o4 = S("o3"), S("o4-mini")
    assert stands_with(o3, line, "", date("2026-12-01"), KNOWN) and stands_with(o4, line, "", date("2027-03-01"), KNOWN)
    assert not stands_with(o3, line, "", date("2027-03-01"), KNOWN) and not stands_with(o4, line, "", date("2026-12-01"), KNOWN)
    # a table whose first column is the date: the date belongs to the first model named after it, not to its replacement
    row = "October 28, 2026 | claude-3-haiku-20240307 | claude-haiku-4-5"
    assert stands_with(S("Claude 3 Haiku", "claude-3-haiku-20240307"), row, "", date("2026-10-28"), KNOWN)
    assert not stands_with(S("Claude Haiku 4.5", "claude-haiku-4-5"), row, "", date("2026-10-28"), KNOWN)
    # the same in a sentence
    assert not stands_with(S("Claude Haiku 4.5"), "Claude 3 Haiku retires on 2026-09-12; move to Claude Haiku 4.5.", "", date("2026-09-12"), KNOWN)
    assert stands_with(HAIKU3, "Claude 3 Haiku retires on 2026-09-12; move to Claude Haiku 4.5.", "", date("2026-09-12"), KNOWN)


SECTION = "/docs/models/deprecations\nModel deprecations\nDeprecations\nClaude 3 Haiku"


def test_a_line_that_names_no_model_belongs_to_the_model_named_last_before_it():
    # ... on an earlier line of the quote
    assert stands_with(HAIKU3, "Claude 3 Haiku\nEOL date: 2026-09-12", TABLE, date("2026-09-12"), KNOWN)
    cards = "Claude 3 Haiku\nEOL date: 2026-09-12\nClaude 3.5 Haiku\nEOL date: 2026-12-01"
    assert stands_with(HAIKU3, cards, TABLE, date("2026-09-12"), KNOWN) and stands_with(HAIKU35, cards, TABLE, date("2026-12-01"), KNOWN)
    assert not stands_with(HAIKU3, cards, TABLE, date("2026-12-01"), KNOWN) and not stands_with(HAIKU35, cards, TABLE, date("2026-09-12"), KNOWN)
    # ... or in the headings the quote stands under
    assert stands_with(HAIKU3, "Retirement date: September 12, 2026", SECTION, date("2026-09-12"), KNOWN)
    assert not stands_with(HAIKU35, "Retirement date: September 12, 2026", SECTION, date("2026-09-12"), KNOWN)
    assert not stands_with(HAIKU3, "Retirement date: September 12, 2026", TABLE, date("2026-09-12"), KNOWN)  # nobody is named anywhere
    # the innermost heading that names a model decides: a section of its own under a page about another model
    nested = "/docs/models/claude-3-haiku\nClaude 3 Haiku - model card\nClaude 3 Haiku\nCompared with Claude 3.5 Haiku"
    assert not stands_with(HAIKU3, "Input: $0.80 / MTok", nested, number(0.8), KNOWN)
    assert stands_with(HAIKU35, "Input: $0.80 / MTok", nested, number(0.8), KNOWN)
    # the address counts too (a model card names its model there, and often nowhere else that is quoted)
    card = "/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-5-5.html\nModel card - Amazon Bedrock\nModel card\nLimits"
    assert stands_with(SONNET, "Maximum output | 128K tokens", card, number(128000.0), KNOWN)
    # a heading that names two models leaves the lines under it to neither
    assert not stands_with(HAIKU3, "EOL date: 2026-09-12", "Lifecycle\nClaude 3 Haiku and Claude 3.5 Haiku", date("2026-09-12"), KNOWN)


def test_the_model_to_move_to_is_named_in_passing():
    """"Retires on …; recommended replacement: X" is a sentence about the model of the section, although it names X."""
    line = "Retirement: September 12, 2026. Recommended replacement: Claude Haiku 4.5."
    assert stands_with(HAIKU3, line, SECTION, date("2026-09-12"), KNOWN)
    assert not stands_with(S("Claude Haiku 4.5"), line, SECTION, date("2026-09-12"), KNOWN)
    assert stands_with(HAIKU3, "將於 2026-09-12 退役，建議改用 Claude Haiku 4.5。", SECTION, date("2026-09-12"), KNOWN)
    # without such a word in between, the name after the value is the row's model (see the date-first table above)
    assert not stands_with(HAIKU3, "2026-09-12 | Claude Haiku 4.5", SECTION, date("2026-09-12"), KNOWN)
    # and what stands behind the replacement's name is the replacement's
    later = "Retirement: September 12, 2026. Recommended replacement: Claude Haiku 4.5, available since October 1, 2025."
    assert not stands_with(HAIKU3, later, SECTION, date("2025-10-01"), KNOWN)
    # a heading may name the replacement the same way
    assert stands_with(HAIKU3, "EOL date: 2026-09-12", "Lifecycle\nClaude 3 Haiku (replaced by Claude Haiku 4.5)", date("2026-09-12"), KNOWN)


def test_a_line_about_a_model_nobody_listed_is_still_about_another_model():
    """The portal cannot list every model there is: a family word followed by a number or a size is somebody's model."""
    for line in ("Mistral Large 3 | $2 / MTok | $6 / MTok", "Llama 4 Maverick | $2 / MTok", "GPT-5 mini: $2 per MTok", "Gemini Pro costs $2 / MTok",
                 "Claude Instant | $2 / MTok", "o4-mini | $2 / MTok", "Nova Lite | $2 / MTok", "Claude Sonnet 5 | $2 / MTok", "Sonnet 4.5 | $2 / MTok"):
        assert names_another(SONNET, line, KNOWN), line
        assert not stands_with(SONNET, line, "Pricing\nClaude Sonnet 5.5", number(2.0), KNOWN), line
    for line in ("Input tokens | $2 / MTok", "Batch API: 50% discount, $2 / MTok", "Context window: 1M tokens ($2 / MTok above 200K)", "us-east-1 | $2 / MTok",
                 "Priority tier 2 | $2 / MTok", "Released October 2026 at $2 / MTok", "See the Claude API docs: $2 / MTok", "The Claude models page lists $2 / MTok"):
        assert not names_another(SONNET, line, KNOWN), line
        assert stands_with(SONNET, line, "Pricing\nClaude Sonnet 5.5", number(2.0), KNOWN), line
    # a name the portal has for another model, of which this model's name is only the beginning
    grok = S("Grok 4")
    assert mentions(grok, "Grok 4 Fast | $0.20 / MTok")                          # (the words alone cannot tell)
    assert not stands_with(grok, "Grok 4 Fast | $0.20 / MTok", "", number(0.2), KNOWN)
    assert stands_with(grok, "Grok 4 | $3 / MTok", "", number(3.0), KNOWN)


def test_a_release_or_an_advisory_may_name_models_on_its_lines():
    release = subject_of({"version": "v1.104.0", "channel": "stable"})
    quote = "v1.104.0-stable\nAdds Claude Sonnet 5.5 and GPT-6 Sol pricing; released 2026-10-04."
    assert stands_with(release, quote, "LiteLLM release notes", date("2026-10-04"), KNOWN)
    assert not stands_with(release, "v1.103.0-stable\nReleased 2026-09-27.", "LiteLLM release notes", date("2026-09-27"), KNOWN)
    advisory = subject_of({"id": "CVE-2026-12345"})
    assert stands_with(advisory, "CVE-2026-12345: fixed in 1.84.2 on 2026-08-01 (affects Claude Sonnet 5.5 routes).", "", date("2026-08-01"), KNOWN)
    assert not stands_with(advisory, "CVE-2026-99999: fixed on 2026-08-01.", "", date("2026-08-01"), KNOWN)


def page(headings, text="x" * 500):
    return Page(id="p1", url="https://platform.claude.com/docs/en/about-claude/pricing", final_url="https://platform.claude.com/docs/en/about-claude/pricing",
                title="Pricing - Claude API Docs", text=text, kind="html", fetched_at="2026-10-02T03:00:00Z", tier=Tier("official", "vendor-doc", "Anthropic"),
                headings=headings)


def test_the_context_of_a_quote_is_what_it_stands_under_not_what_stands_before_it():
    """Three sections, one per model: a quote from the third is not "about" the first two."""
    p = page([(1, "Pricing", 0), (2, "Claude Opus 5.5", 100), (2, "Claude Sonnet 5.5", 200), (3, "Batch", 250), (2, "Claude Haiku 4.5", 300), (3, "Long context", 350)])
    assert context_for(p, 50).split("\n") == ["docs en about-claude pricing", "Pricing - Claude API Docs", "Pricing"]
    assert context_for(p, 150).split("\n")[2:] == ["Pricing", "Claude Opus 5.5"]
    assert context_for(p, 260).split("\n")[2:] == ["Pricing", "Claude Sonnet 5.5", "Batch"]
    assert context_for(p, 320).split("\n")[2:] == ["Pricing", "Claude Haiku 4.5"]              # "Batch" and "Claude Sonnet 5.5" are closed
    assert context_for(p, 400).split("\n")[2:] == ["Pricing", "Claude Haiku 4.5", "Long context"]
    quote = "$1 / MTok | $6 / MTok"
    assert not stands_with(SONNET, quote, context_for(p, 320), number(6.0), KNOWN)
    assert stands_with(S("Claude Haiku 4.5"), quote, context_for(p, 320), number(6.0), KNOWN)
    assert stands_with(SONNET, "$2 / MTok | $10 / MTok", context_for(p, 220), number(10.0), KNOWN)
    # the address keeps its hyphens: "sonnet-5-5" in a path is the model's name, "sonnet 5 5" would be nobody's
    card = page([], "x")
    card.final_url = "https://platform.claude.com/docs/en/models/claude-sonnet-5-5/overview"
    assert context_for(card, 0).split("\n")[0] == "docs en models claude-sonnet-5-5 overview" and mentions(SONNET, context_for(card, 0))
    odd = page([], "x")
    odd.final_url = "https://[broken"
    assert context_for(odd, 0) == "Pricing - Claude API Docs"                                    # an address nobody can read adds nothing
