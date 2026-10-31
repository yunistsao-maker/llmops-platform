"""A page is read the way a person reads it - and a quote is stored as what the page states, not as what was typed.

Round 4 of the review got wrong facts onto the portal through what the page reader left out (a footnote mark read as
part of a price, a struck-out price, the label of a tab, the header of an accordion) and through quotes that were only
a fragment of a row.  Each test here is one of those, next to the honest reading that has to keep working.
"""

import pytest

from helpers import AWS_LIFECYCLE_HTML, add_html, add_quote, make_session
from research.evidence import context_for, record_quote, widen
from research.validate.grounding import MAX_STORED_CHARS
from research.validate.units import cells_of, is_heading, is_row, sentence_spans, units_of
from research.web.html_text import parse_html, parse_text

URL = "https://platform.claude.com/docs/en/about-claude/pricing"


def page_of(body, title="Pricing - Claude API Docs"):
    return parse_html(f"<html><head><title>{title}</title></head><body><main>{body}{'<p>filler text. </p>' * 12}</main></body></html>", URL)


@pytest.fixture
def s():
    return make_session()


def loaded(s, body, url=URL, title="Pricing - Claude API Docs"):
    s.remember_urls([url])
    return add_html(s, url, f"<html><head><title>{title}</title></head><body><main>{body}{'<p>filler text. </p>' * 12}</main></body></html>")


# --------------------------------------------------------------------------- what the page says
def test_a_footnote_mark_is_not_part_of_the_figure_it_stands_behind():
    """"$3<sup>1</sup>" was read as $31, "2026-11-30<sup>2</sup>" as a date that ends in 302."""
    text = page_of("<h1>Pricing</h1><table><tr><th>Model</th><th>Input</th><th>Output<sup>2</sup></th></tr>"
                   "<tr><td>Claude Sonnet 5.5<sup>1</sup></td><td>$3<sup>1</sup></td><td>$15<sup>†</sup></td></tr></table>"
                   "<p>The 1<sup>st</sup> tier of Claude<sup>TM</sup> covers 10<sup>6</sup> tokens.</p>").text
    assert "Claude Sonnet 5.5[1] | $3[1] | $15[†]" in text and "Output[2]" in text
    assert "$31" not in text and "5.51" not in text
    assert "The 1st tier of Claude covers 10[6] tokens." in text  # an ordinal is one word, a trademark sign is nothing


def test_what_is_struck_out_is_not_what_the_page_says():
    text = page_of("<h1>Pricing</h1><p>Claude Sonnet 5.5 costs <del>$4</del> <s>$5</s> <strike>$6</strike> $3 per MTok.</p>").text
    assert "Claude Sonnet 5.5 costs $3 per MTok." in text and not any(x in text for x in ("$4", "$5 ", "$6"))


def test_the_header_of_an_accordion_is_a_heading_although_it_is_a_button():
    page = page_of("""<h1>Pricing</h1><div class="accordion">
<div><h2 class="accordion-header"><button type="button">Claude Opus 5.5</button></h2><table><tr><th>Input</th><th>Output</th></tr><tr><td>$5 / MTok</td><td>$25 / MTok</td></tr></table></div>
<div><h2 class="accordion-header"><button type="button">Claude Sonnet 5.5</button></h2><table><tr><th>Input</th><th>Output</th></tr><tr><td>$3 / MTok</td><td>$15 / MTok</td></tr></table></div></div>
<details><summary><button>Show</button> Claude Haiku 4.5</summary><p>$1 / MTok</p></details><p>Press <button>Copy</button> to copy.</p>""")
    assert [(level, text) for level, text, _at in page.headings] == [(1, "Pricing"), (2, "Claude Opus 5.5"), (2, "Claude Sonnet 5.5")]
    assert "Show Claude Haiku 4.5\n$1 / MTok" in page.text and "Press to copy." in page.text  # a button elsewhere is still not text


def test_a_tab_gives_its_label_to_the_panel_it_opens():
    """Three price tables for one model, told apart by nothing but the tab they are shown under."""
    page = page_of("""<h1>Pricing</h1><h2>Claude Sonnet 5.5</h2>
<div role="tablist"><button role="tab">Batch</button><button role="tab" aria-selected="true">Standard</button></div>
<div role="tabpanel"><table><tr><th>Model</th><th>Input</th></tr><tr><td>Claude Sonnet 5.5</td><td>$1.50 / MTok</td></tr></table></div>
<div role="tabpanel"><table><tr><th>Model</th><th>Input</th></tr><tr><td>Claude Sonnet 5.5</td><td>$3 / MTok</td></tr></table></div>
<h2>By name</h2><div role="tablist"><button role="tab" id="t1" aria-controls="p2">Priority</button><button role="tab" id="t2" aria-controls="p1">Flex</button></div>
<div role="tabpanel" id="p1"><p>flex text</p></div><div role="tabpanel" id="p2"><p>priority text</p></div>
<h2>More tabs than panels</h2><div role="tablist"><button role="tab">A</button><button role="tab">B</button><button role="tab">C</button></div><div role="tabpanel"><p>one of three</p></div>
<h2>No panel at all</h2><div role="tablist"><button role="tab">Batch</button><button role="tab">Standard</button></div><p>Claude Sonnet 5.5 costs $1.50.</p>
<h2>After</h2><p>plain</p>""")
    text = page.text
    assert "### Batch [tab]\n\nModel | Input\nClaude Sonnet 5.5 | $1.50 / MTok" in text
    assert "### Standard [tab]\n\nModel | Input\nClaude Sonnet 5.5 | $3 / MTok" in text
    assert "### Flex [tab]\n\nflex text" in text and "### Priority [tab]\n\npriority text" in text  # by aria-controls, not by order
    # a label that cannot be given to its panel is not given to the wrong one: what follows is "one of" them
    assert "### one of: A · B · C [tab]\n\none of three" in text
    assert "### one of: Batch · Standard [tab]\n\nClaude Sonnet 5.5 costs $1.50." in text
    # a tab is below the heading it stands under, and does not swallow the sections after it
    levels = {t: level for level, t, _at in page.headings}
    assert levels["Batch [tab]"] == levels["Standard [tab]"] == 3 and levels["By name"] == levels["After"] == 2


def test_a_note_in_the_article_is_read_and_the_furniture_around_it_is_not():
    html = """<html><head><title>Gemini 2.5 Flash Image</title></head><body><nav><aside>site navigation</aside></nav><main><article><h1>Gemini 2.5 Flash Image</h1>
<aside class="deprecated"><strong>Deprecated:</strong> gemini-2.5-flash-image will be shut down on October 9, 2026.</aside>
<aside class="sidebar related-links">Related: other models</aside><p>""" + "It supports text and image input. " * 20 + "</p></article></main><aside>page aside</aside></body></html>"
    text = parse_html(html, "https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash-image").text
    assert "Deprecated: gemini-2.5-flash-image will be shut down on October 9, 2026." in text
    assert "site navigation" not in text and "Related: other models" not in text and "page aside" not in text


def test_a_cell_that_spans_rows_is_read_on_each_of_them_and_a_row_knows_its_column_headings():
    page = page_of("""<h1>Schedule</h1><table><tr><th>Retirement date</th><th>Model</th><th>Replacement</th></tr>
<tr><td rowspan="2">2026-11-30</td><td>claude-opus-4-5</td><td>claude-opus-5-5</td></tr>
<tr><td>claude-sonnet-4-5</td><td>claude-sonnet-5-5</td></tr><tr><td>2026-12-15</td><td>gpt-5</td><td>gpt-6</td></tr></table>""")
    lines = page.text.split("\n")
    assert "2026-11-30 | claude-sonnet-4-5 | claude-sonnet-5-5" in lines
    rows = [n for n, line in enumerate(lines) if line.startswith("2026-")]
    assert len(rows) == 3 and all(page.row_heads[n] == "Retirement date | Model | Replacement" for n in rows)
    assert lines.index("Retirement date | Model | Replacement") not in page.row_heads  # the heading row has no heading


def test_a_markdown_copy_of_a_page_has_headings_and_table_heads_too():
    md = ("# Model deprecations\n\nIntro.\n\n## Claude Sonnet 4.5\n\nRetirement date: November 30, 2026\n\n| Model | Input |\n|---|---|\n"
          "| Claude Sonnet 4.5 | $3 |\n\n## Claude Opus 4.5\n\nRetirement date: November 24, 2026\n")
    page = parse_text(md)
    assert page.title == "Model deprecations"
    assert [(level, text) for level, text, _at in page.headings] == [(1, "Model deprecations"), (2, "Claude Sonnet 4.5"), (2, "Claude Opus 4.5")]
    row = md.split("\n").index("| Claude Sonnet 4.5 | $3 |")
    assert page.row_heads == {row: "| Model | Input |"}
    # a text without a heading has no title: the address says what was asked for, not what came back
    assert parse_text("retires 2026-11-30\nno heading here").title == ""


# --------------------------------------------------------------------------- the units of a page
def test_a_row_a_heading_and_a_sentence_are_told_apart():
    assert is_row("Claude 3 Haiku | 2025-12-01 | 2026-09-12") and is_row("| Claude 3 Haiku | $3 |") and not is_row("a or b")
    assert is_heading("## Claude Sonnet 5.5") and not is_heading("#hashtag") and not is_row("Retires on 2026-09-12.")
    row = "| Claude Sonnet 4.5 | $3 |"
    assert [row[a:b] for a, b in cells_of(row)] == ["Claude Sonnet 4.5", "$3"]
    assert [u.kind for u in units_of("## Sept 28\n\n- We've launched it. It is fast.\nA | B")] == ["heading", "prose", "prose", "row"]


def test_a_sentence_ends_where_a_reader_would_say_it_ends():
    def cut(text):
        return [text[a:b] for a, b in sentence_spans(text)]

    assert cut("Claude Sonnet 5 costs $3. Claude Sonnet 5.5 costs $2.") == ["Claude Sonnet 5 costs $3.", "Claude Sonnet 5.5 costs $2."]
    # not behind an abbreviation, a single letter or the number of a name; never inside a name
    assert cut("Available in N. Virginia, e.g. us-east-1. Use v1.104.0 there.") == ["Available in N. Virginia, e.g. us-east-1.", "Use v1.104.0 there."]
    assert cut("它於 2026-11-30 退役。之後呼叫會失敗！") == ["它於 2026-11-30 退役。", "之後呼叫會失敗！"]
    # the stop behind the day of a German date is no full stop ("am 30. November 2026" used to be cut in two, and the date was gone)
    assert cut("claude-sonnet-4-5 wird am 30. November 2026 eingestellt. Danach nicht mehr.") == [
        "claude-sonnet-4-5 wird am 30. November 2026 eingestellt.", "Danach nicht mehr."]


# --------------------------------------------------------------------------- what a quote is stored as
ROW = "Claude 3 Haiku | anthropic.claude-3-haiku-20240307-v1:0 | 2025-12-01 | 2026-09-12"


def test_a_quote_is_the_whole_row_it_touches(s):
    """"anthropic.claude-3-haiku-20240307-v1:0 | 2025-12-01" out of the Claude 3 Haiku row and "2026-12-01" out of the
    next one could be typed as one passage; four cells of five are not a shorter statement but another one."""
    page = add_html(s, "https://docs.aws.amazon.com/bedrock/latest/userguide/model-lifecycle-legacy.html", AWS_LIFECYCLE_HTML)
    ev = add_quote(s, page, "2025-12-01 | 2026-09-12")
    assert ev.quote == ROW and ev.header == "Model | Model ID | Legacy date | EOL date"
    # a passage that reaches into the next row is both rows, whole
    both = add_quote(s, page, "2026-09-12\nClaude 3.5 Haiku")
    assert both.quote == ROW + "\nClaude 3.5 Haiku | anthropic.claude-3-5-haiku-20241022-v1:0 | 2026-02-01 | 2026-12-01"
    assert both.heads == ("Model | Model ID | Legacy date | EOL date",) * 2
    # the same passage recorded twice is one piece of evidence
    assert add_quote(s, page, "Claude 3 Haiku | anthropic.claude-3-haiku-20240307-v1:0").id == ev.id


def test_a_quote_out_of_a_sentence_is_the_whole_sentence(s):
    page = loaded(s, "<h1>News</h1><p>Claude Sonnet 5 stays. Claude Sonnet 5.5 will not be retired before November 30, 2026. More later.</p>")
    ev = add_quote(s, page, "retired before November 30, 2026")
    assert ev.quote == "Claude Sonnet 5.5 will not be retired before November 30, 2026."
    text = "One. Two three four. Five."
    assert widen(text, text.index("three"), text.index("three") + 5) == (5, 20)


def test_the_same_words_in_two_places_have_to_be_pointed_at(s):
    """"$3 / MTok | $15 / MTok" under "Claude Sonnet 5" and under "Claude Sonnet 5.5": the first place used to be taken,
    whatever the model meant - and with it the headings of the other model."""
    page = loaded(s, """<h1>Pricing</h1><h2>Claude Sonnet 5</h2><table><tr><th>Input</th><th>Output</th></tr><tr><td>$3 / MTok</td><td>$15 / MTok</td></tr></table>
<h2>Claude Sonnet 5.5</h2><table><tr><th>Input</th><th>Output</th></tr><tr><td>$3 / MTok</td><td>$15 / MTok</td></tr></table>""")
    ev, problem = record_quote(s, page, "$3 / MTok | $15 / MTok")
    assert ev is None and problem.code == "ambiguous" and len(problem.suggestions) == 2
    assert "Claude Sonnet 5.5" in problem.suggestions[1] and "line=" in problem.message
    line = int(problem.suggestions[1].split(" ")[0].lstrip("L"))
    ev = add_quote(s, page, "$3 / MTok | $15 / MTok", line=line)
    assert ev.context.split("\n")[-1] == "Claude Sonnet 5.5" and ev.header == "Input | Output"
    other = add_quote(s, page, "$3 / MTok | $15 / MTok", line=int(problem.suggestions[0].split(" ")[0].lstrip("L")))
    assert other.id != ev.id and other.context.split("\n")[-1] == "Claude Sonnet 5"
    # the same words under the same headings, in rows that read the same, are one statement: nothing to choose
    twice = loaded(s, "<h1>Notes</h1><p>Retires on 2026-11-30.</p><p>Retires on 2026-11-30.</p>", url="https://platform.claude.com/docs/en/x")
    assert record_quote(s, twice, "Retires on 2026-11-30.")[0] is not None


def test_what_is_stored_is_bounded_whatever_the_page_holds_between_two_words(s):
    """The limit on a quote was a limit on what is typed; a text page with a long run of blanks inside the passage made a
    stored quote of megabytes (and a proposal the publisher then refused for its size)."""
    s.remember_urls(["https://platform.claude.com/notes.txt"])
    page = add_html(s, "https://platform.claude.com/notes.txt", "Claude 3 Haiku retires" + " " * 200_000 + "on 2026-09-12 for good", content_type="text/plain")
    ev, problem = record_quote(s, page, "Claude 3 Haiku retires on 2026-09-12")
    assert ev is None and problem.code == "too_long" and str(MAX_STORED_CHARS) in problem.message


def test_the_context_of_a_quote_is_the_headings_it_stands_under(s):
    page = add_html(s, "https://docs.aws.amazon.com/bedrock/latest/userguide/model-lifecycle-legacy.html", AWS_LIFECYCLE_HTML)
    ev = add_quote(s, page, ROW)
    lines = ev.context.split("\n")
    assert lines[-2:] == ["Model lifecycle", "Legacy and EOL models"] and ev.outer == 2  # the address and the title come first
    assert context_for(page, page.text.index("Claude 3 Haiku")) == ev.context
    # a page whose title is its own address (a text file) is not "titled" by that address a second time
    s.remember_urls(["https://platform.claude.com/claude-opus-4-5.txt"])
    plain = add_html(s, "https://platform.claude.com/claude-opus-4-5.txt", "Retires on 2026-11-24 for everybody.", content_type="text/plain")
    ev = add_quote(s, plain, "Retires on 2026-11-24 for everybody.")
    assert plain.title == plain.url and ev.context == "claude-opus-4-5.txt" and ev.outer == 1


def test_the_dates_a_quote_is_said_to_hold_are_not_made_of_the_figures_of_a_name(s):
    page = loaded(s, "<h1>Plans</h1><p>Planned retirements: Claude Haiku 4.5 October 2026 (exact day to be announced).</p><p>GPT-5 March 2027 is the plan.</p>")
    assert add_quote(s, page, "Planned retirements: Claude Haiku 4.5 October 2026 (exact day to be announced).").dates == []
    assert add_quote(s, page, "GPT-5 March 2027 is the plan.").dates == []
