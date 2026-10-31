"""A proposal may create one kind of file: the deep dive (data/models/<id>.json) of a model the catalog has.

When a new top model entered the catalog, nobody but a person could give it a deep dive - the model comparison and the
model's page went without one.  Now an operation may create it, and nothing broader: only that file, only when it does
not exist, only for a catalog model (today's, or one an earlier operation of the same proposal adds), only with the
modelId it is named after.  Every value in it is a new value - held to the quote rules, the tiers of the deep dive's
places and the schema - and a new deep dive always waits for a person (the publisher's side: pipeline tests).
"""

import copy

import pytest

from helpers import add_html, add_quote, make_session, op, proposal, source_url
from research.apply import already_applied, apply_saved_proposal, stage_proposal
from research.portal.diff import ADDED
from research.portal.patch import Op, PatchError, apply_op, create_problem
from research.proposal import validate_proposal

M = "claude-sonnet-5-5"
DIVE = f"data/models/{M}.json"
CATALOG = "data/catalog.json"
REDDIT = "https://www.reddit.com/r/LocalLLaMA/comments/x/y"


def html(title, *paragraphs):
    return f"<html><head><title>{title}</title></head><body><main><h1>{title}</h1>" + "".join(f"<p>{p}</p>" for p in paragraphs) + "</main></body></html>"


def said(s, where, title, sentence):
    url = where if "://" in where else source_url(s, where)
    s.remember_urls([url])
    return add_quote(s, add_html(s, url, html(title, sentence)), sentence)


def deep_dive(model_id=M, **changes):
    """A complete DeepDive (every required field), with nothing in it that needs a quote but the model it is about."""
    doc = {
        "schema": "deep-dive/v1", "modelId": model_id, "title": "Claude Sonnet 5.5 技術深度解析", "locale": "zh-TW", "asOf": "2031-01-01",
        "tldr": "平衡型模型，適合程式與 agent 工作。", "whatsNew": [], "breakingChanges": [], "availabilityMatrix": [], "apiCompatibility": [],
        "strengths": ["程式與 agent 工作。"], "weaknesses": [], "bestFor": [], "avoidFor": [], "safetyAndCompliance": [], "enterpriseNotes": [],
        "testPlanFocus": [], "openQuestions": [], "sources": [{"id": "anthropic-sonnet55-overview"}],
        "onboarding": {"audience": "應用團隊", "prerequisites": [], "steps": [], "aliases": [], "parameterGuidance": [], "codeSamples": [],
                       "dataClassification": "內部", "support": "平台團隊"},
    }
    doc.update(changes)
    return doc


@pytest.fixture
def s():
    return make_session()


def overview(s):
    """The vendor's own page about the model (an official source the portal has)."""
    return said(s, "anthropic-sonnet55-overview", "Claude Sonnet 5.5 - overview", "Claude Sonnet 5.5 is our balanced model for coding and agents.")


def check(s, *ops):
    return validate_proposal(s, proposal(list(ops)))


def errors(rep):
    return "\n".join(rep.errors)


# ---------------------------------------------------------------------------------------------- what may be created
def test_a_catalog_model_without_a_deep_dive_gets_one(s):
    summary = s.portal.summary()
    assert summary["deepDives"] == [] and summary["modelsWithoutDeepDive"] == [M]  # what the roles are shown
    ev = overview(s)
    rep = check(s, op(DIVE, "add", "", deep_dive(), [ev.id], rationale="依官方模型頁建立 deep dive。"))
    assert rep.ok, rep.errors
    # it is accepted - and said to wait for a person, whatever else it holds
    assert any(f"{DIVE} is a new deep dive" in w and "always waits for a person" in w for w in rep.warnings), rep.warnings
    saved = rep.proposal["ops"][0]
    assert (saved["op"], saved["path"], saved["file"]) == ("add", "", DIVE) and "before" not in saved
    # the stamp is the harness's: asOf is the day of the research, not what the model wrote ("2031-01-01")
    assert saved["value"]["asOf"] == s.today.isoformat() and saved["value"]["schema"] == "deep-dive/v1"
    assert f"create {DIVE}" in rep.proposal["validation"]["applied"] and DIVE in rep.proposal["validation"]["filesTouched"]
    # a value without `schema` gets the one deep dives are written in
    bare = {k: v for k, v in deep_dive().items() if k not in ("schema", "asOf")}
    rep = check(s, op(DIVE, "add", "", bare, [ev.id]))
    assert rep.ok, rep.errors
    assert rep.proposal["ops"][0]["value"]["schema"] == "deep-dive/v1" and rep.proposal["ops"][0]["value"]["asOf"] == s.today.isoformat()
    # once it is there, the summary no longer lists the model as missing one
    after = s.portal.clone()
    after.docs[DIVE] = rep.proposal["ops"][0]["value"]
    assert after.summary()["deepDives"] == [M] and after.summary()["modelsWithoutDeepDive"] == []


@pytest.mark.parametrize("file, value, said_", [
    ("data/models/gpt-9-ultra.json", deep_dive("gpt-9-ultra"), "'gpt-9-ultra' is not the id of a model in data/catalog.json"),
    (DIVE, deep_dive("claude-sonnet-5"), "modelId must be 'claude-sonnet-5-5'"),
    (DIVE, deep_dive(**{"modelId": None}), "modelId must be 'claude-sonnet-5-5'"),
    (DIVE, [deep_dive()], "a deep dive is one object"),
    ("data/models/.claude-sonnet-5-5.json", deep_dive(".claude-sonnet-5-5"), "an operation creates no file but a model's deep dive"),
    ("data/models/-x.json", deep_dive("-x"), "an operation creates no file but a model's deep dive"),
    ("data/models/sub/claude-sonnet-5-5.json", deep_dive(), "an operation creates no file but a model's deep dive"),
    ("data/models/claude-sonnet-5-5.yaml", deep_dive(), "an operation creates no file but a model's deep dive"),
    ("data/open-models-2.json", deep_dive(), "an operation creates no file but a model's deep dive"),
    ("data/digests/2026-10-02.json", deep_dive(), "an operation creates no file but a model's deep dive"),
    ("runs/compat/new.json", deep_dive(), "an operation creates no file but a model's deep dive"),
    ("data/lifecycle.json", deep_dive(), "refusing to replace a whole file"),
    ("data/sources.json", deep_dive(), "cannot be edited by operations"),
])
def test_nothing_else_is_created(s, file, value, said_):
    ev = overview(s)
    rep = check(s, op(file, "add", "", value, [ev.id]))
    assert not rep.ok and said_ in errors(rep), (file, rep.errors)
    assert file not in s.portal.docs or file in ("data/lifecycle.json", "data/sources.json")


def test_a_deep_dive_that_exists_is_not_created_again(s):
    s.portal.docs[DIVE] = dict(deep_dive(), asOf="2026-10-01")
    s._baseline = None
    ev = overview(s)
    rep = check(s, op(DIVE, "add", "", deep_dive(tldr="另一份內容。"), [ev.id]))
    assert not rep.ok and f"{DIVE} already exists: a deep dive is created once" in errors(rep)
    # ... and is changed the way every file is: field by field
    assert check(s, op(DIVE, "add", "/strengths/-", "長文件。", [ev.id])).ok


def test_a_model_gets_no_second_deep_dive_under_its_own_name(s):
    """A deep dive filed under another name that says it is about the model: the model has one (health reports the odd
    name, a person renames it) - the summary does not list the model as missing one, and none is created beside it."""
    s.portal.docs["data/models/sonnet.json"] = dict(deep_dive(), asOf="2026-10-01")
    s._baseline = None
    assert s.portal.summary()["modelsWithoutDeepDive"] == []
    ev = overview(s)
    rep = check(s, op(DIVE, "add", "", deep_dive(), [ev.id]))
    assert not rep.ok and f"data/models/sonnet.json is a deep dive of '{M}' already" in errors(rep) and DIVE not in s.portal.docs


def test_create_is_still_not_an_operation(s):
    ev = overview(s)
    rep = check(s, op(DIVE, "create", "", deep_dive(), [ev.id]))
    assert not rep.ok and "'create' is not available" in errors(rep) and "`add` with the path \"\" on data/models/" in errors(rep)


def test_nothing_follows_a_new_deep_dive_into_its_file(s):
    """Every operation is about the data as it is today: the new file's whole content is the one operation that creates it."""
    ev = overview(s)
    rep = check(s, op(DIVE, "add", "", deep_dive(), [ev.id]), op(DIVE, "add", "/strengths/-", "長文件。", [ev.id]))
    assert not rep.ok and "ops[1]" in errors(rep) and "an earlier operation of this proposal creates it" in errors(rep)
    rep = check(s, op(DIVE, "add", "", deep_dive(), [ev.id]), op(DIVE, "add", "", deep_dive(), [ev.id]))
    assert not rep.ok and "is changed by more than one operation" in errors(rep)


# ---------------------------------------------------------------------------------------------- every value in it is new
def test_every_value_of_a_new_deep_dive_is_held_to_the_quote_rules(s):
    ev = overview(s)
    # a date and an amount nobody quoted, in prose
    rep = check(s, op(DIVE, "add", "", deep_dive(tldr="2026-09-28 發布，價格 $9 / MTok。"), [ev.id]))
    assert not rep.ok and "/tldr mentions the date 2026-09-28 in prose" in errors(rep) and "/tldr mentions $9 in prose" in errors(rep)
    rep = check(s, op(DIVE, "add", "", deep_dive(whatsNew=[{"title": "新版", "detail": "3 月 31 日起提供。", "sources": [{"id": "anthropic-sonnet55-overview"}]}]),
                      [ev.id]))
    assert not rep.ok and "/whatsNew/0/detail mentions a day without a year" in errors(rep)
    # ... quoted, the same text is fine
    dated = said(s, "anthropic-sonnet55-whatsnew", "What's new in Claude Sonnet 5.5", "Claude Sonnet 5.5 was released on 2026-09-28 at $3 / MTok input.")
    assert check(s, op(DIVE, "add", "", deep_dive(tldr="2026-09-28 發布，輸入 $3 / MTok。",
                                                  sources=[{"id": "anthropic-sonnet55-overview"}, {"id": "anthropic-sonnet55-whatsnew"}]),
                      [ev.id, dated.id])).ok
    # the quotes must be about this model
    other = said(s, "anthropic-opus55-whatsnew", "What's new in Claude Opus 5.5", "Claude Opus 5.5 is our most capable model.")
    rep = check(s, op(DIVE, "add", "", deep_dive(sources=[{"id": "anthropic-opus55-whatsnew"}]), [other.id]))
    assert not rep.ok and "none of the cited quotes" in errors(rep) and M in errors(rep)
    # a source it cites is a page cited as evidence for it
    rep = check(s, op(DIVE, "add", "", deep_dive(sources=[{"id": "anthropic-sonnet55-overview"}, {"id": "aws-bedrock-sonnet55-card"}]), [ev.id]))
    assert not rep.ok and "cites source 'aws-bedrock-sonnet55-card'" in errors(rep)


def test_the_places_of_a_new_deep_dive_need_the_tier_they_always_need(s):
    """A deep dive is written from the vendor's pages or an independent one, never a forum post; its onboarding guide,
    API compatibility, availability, breaking changes, comparison and compliance from the vendor's or the cloud's own
    page - in a new file as much as in one that is edited."""
    post = said(s, REDDIT, "thread", "Claude Sonnet 5.5 is great for coding and agents.")
    rep = check(s, op(DIVE, "add", "", deep_dive(sources=[]), [post.id]))
    assert not rep.ok and "needs evidence from an independent page" in errors(rep) and "community-tier" in errors(rep)
    aa = said(s, "aa-sonnet55", "Claude Sonnet 5.5 | Artificial Analysis", "Claude Sonnet 5.5 scores 57 on the Artificial Analysis Intelligence Index.")
    rep = check(s, op(DIVE, "add", "", deep_dive(sources=[{"id": "aa-sonnet55"}]), [aa.id]))
    assert not rep.ok and "/onboarding/audience needs evidence from an official page" in errors(rep) and "independent-tier" in errors(rep)
    breaking = deep_dive(sources=[{"id": "aa-sonnet55"}], breakingChanges=[{"title": "x", "detail": "y", "migration": "z", "severity": "low",
                                                                            "sources": [{"id": "aa-sonnet55"}]}])
    breaking["onboarding"] = dict(breaking["onboarding"], audience="", dataClassification="", support="")
    rep = check(s, op(DIVE, "add", "", breaking, [aa.id]))
    assert not rep.ok and "/breakingChanges/0/title needs evidence from an official page" in errors(rep) and "/onboarding" not in errors(rep)
    # what an independent page may back: the rest of the deep dive
    del breaking["breakingChanges"][0]
    assert check(s, op(DIVE, "add", "", breaking, [aa.id])).ok


def test_a_new_deep_dive_is_schema_checked(s):
    ev = overview(s)
    missing = {k: v for k, v in deep_dive().items() if k != "tldr"}
    rep = check(s, op(DIVE, "add", "", missing, [ev.id]))
    assert not rep.ok and f"portal validation: {DIVE}: (root): 'tldr' is a required property" in errors(rep)
    rep = check(s, op(DIVE, "add", "", deep_dive(rating="5 stars"), [ev.id]))
    assert not rep.ok and "portal validation" in errors(rep) and "rating" in errors(rep)
    rep = check(s, op(DIVE, "add", "", deep_dive(schema="deep-dive/v9"), [ev.id]))
    assert not rep.ok and "portal validation" in errors(rep) and "deep-dive/v1" in errors(rep)
    rep = check(s, op(DIVE, "add", "", deep_dive(strengths=[7]), [ev.id]))
    assert not rep.ok and "portal validation" in errors(rep)


HAIKU = "claude-haiku-5-5"
HAIKU_URL = "https://platform.claude.com/docs/en/models/haiku-5-5/overview"


def haiku(s):
    """A model the catalog does not have yet, every value of it on one official page, and a proposal's source for it."""
    sentence = ("Claude Haiku 5.5 is our fastest model, released on 2026-10-01 with a 200,000 token context window and 64,000 max "
                "output tokens.")
    ev = said(s, HAIKU_URL, "Claude Haiku 5.5 - overview", sentence)
    model = {"id": HAIKU, "name": "Claude Haiku 5.5", "vendor": "anthropic", "family": "Claude Haiku", "tier": "efficient",
             "releaseDate": "2026-10-01", "platformStatus": "evaluating", "modalities": {"input": ["text"], "output": ["text"]},
             "contextWindow": 200000, "maxOutputTokens": 64000, "reasoning": {"supported": False}, "summary": "Anthropic 最快的模型。",
             "strengths": [], "weaknesses": [], "bestFor": [], "avoidFor": [],
             "safety": {"classifications": [], "behaviours": [], "programmes": [], "sources": []}, "offerings": [], "benchmarks": [],
             "performanceReference": [], "litellm": {"status": "untested", "recommendedVersion": "", "providerStrings": [], "caveats": [], "sources": []},
             "tags": [], "sources": [{"id": "anthropic-haiku55-overview"}], "lastVerified": s.today.isoformat()}
    dive = deep_dive(HAIKU, title="Claude Haiku 5.5 技術深度解析", tldr="最快的模型。", strengths=[], sources=[{"id": "anthropic-haiku55-overview"}])
    return ev, op(CATALOG, "add", "/models/-", model, [ev.id]), op(f"data/models/{HAIKU}.json", "add", "", dive, [ev.id])


def test_a_model_and_its_deep_dive_come_in_one_proposal(s):
    """The deep dive may follow the operation that adds its model to the catalog - and only follow it: the catalog it
    is checked against is the catalog as the operations before it left it."""
    ev, add_model, create = haiku(s)
    new_source = [{"id": "anthropic-haiku55-overview", "url": HAIKU_URL}]
    rep = validate_proposal(s, proposal([add_model, create], newSources=new_source))
    assert rep.ok, rep.errors
    assert any(f"data/models/{HAIKU}.json is a new deep dive" in w for w in rep.warnings), rep.warnings
    rep = validate_proposal(s, proposal([create, add_model], newSources=new_source))
    assert not rep.ok and f"'{HAIKU}' is not the id of a model in data/catalog.json (as it stands at this operation)" in errors(rep)
    # the other way round: a deep dive of a model that a later operation takes out of the catalog is a deep dive of no model
    rep = check(s, op(DIVE, "add", "", deep_dive(), [overview(s).id]), op(CATALOG, "remove", f"/models/{M}", evidence=[ev.id]))
    assert not rep.ok and f"modelId '{M}' not in catalog" in errors(rep)


# ---------------------------------------------------------------------------------------------- the shared replay
def saved(*ops, today="2026-10-02"):
    return {"schema": "llmops-research-proposal/v1", "id": "prop-20261002T030000Z-deepdive", "createdAt": f"{today}T03:00:00Z", "today": today,
            "summary": "test", "ops": list(ops), "newSources": [], "touchSources": [], "digest": None, "asOfFiles": [], "evidence": {}}


def new_model(portal, model_id="claude-sonnet-5-5-mini"):
    """A catalog model that is not in the bundled sample (the sample's one model, renamed; no offerings to clash with)."""
    model = copy.deepcopy(portal.docs[CATALOG]["models"][0])
    model.update(id=model_id, name="Claude Sonnet 5.5 Mini", vendorModelId=model_id, offerings=[])
    return model


def test_a_deep_dive_may_follow_its_model_into_the_catalog():
    """The model the deep dive is about may be added by an earlier operation of the same proposal - not by a later one,
    and the replay of a saved proposal (the publisher, apply_proposal.py) answers exactly as the validator does."""
    portal = make_session().portal
    model = new_model(portal)
    dive = f"data/models/{model['id']}.json"
    add_model = {"file": CATALOG, "op": "add", "path": "/models/-", "value": model}
    create = {"file": dive, "op": "add", "path": "", "value": deep_dive(model["id"])}
    effects: list = []
    staged, problems = apply_saved_proposal(portal, saved(add_model, create), effects)
    assert problems == [], problems
    assert staged.docs[dive]["modelId"] == model["id"] and staged.docs[dive]["asOf"] == "2026-10-02"  # stamped with the proposal's day
    assert dive not in portal.docs
    # what the operation did: the whole file, added
    assert [(c.file, c.path, c.kind) for c in effects[1]] == [(dive, "", ADDED)] and effects[1][0].after == staged.docs[dive]
    # the other way round, the catalog does not have the model yet when the deep dive is created
    staged, problems = apply_saved_proposal(portal, saved(create, add_model))
    assert any(f"'{model['id']}' is not the id of a model in data/catalog.json" in p for p in problems) and dive not in staged.docs
    # a proposal that is live already is recognised as such (the stamp it got is the one it would get again) ...
    applied, _ = apply_saved_proposal(portal, saved(add_model, create))
    assert already_applied(applied, saved(add_model, create)) is True
    # ... but another file of the same name is not that proposal
    other = applied.clone()
    other.docs[dive] = dict(other.docs[dive], tldr="另一份內容。")
    assert already_applied(other, saved(add_model, create)) is False
    staged, problems = apply_saved_proposal(other, saved(create))
    assert any(f"{dive} already exists" in p for p in problems)


def test_the_rules_hold_wherever_a_proposal_is_staged():
    portal = make_session().portal
    docs = copy.deepcopy(portal.docs)
    assert create_problem(docs, Op(DIVE, "add", "", deep_dive())) is None
    for file, value, said_ in (("data/models/x.json", deep_dive("x"), "is not the id of a model"), (DIVE, deep_dive("y"), "modelId must be"),
                               ("data/catalog-2.json", deep_dive(), "creates no file but a model's deep dive")):
        with pytest.raises(PatchError, match=said_):
            apply_op(docs, Op(file, "add", "", value))
        assert file not in docs
    assert apply_op(docs, Op(DIVE, "add", "", deep_dive())) == f"create {DIVE}" and docs[DIVE]["modelId"] == M
    with pytest.raises(PatchError, match="already exists"):
        apply_op(docs, Op(DIVE, "add", "", deep_dive()))
    # staging stamps asOf and records the whole file as added
    effects: list = []
    staged, applied, errs, touched = stage_proposal(portal, ops=[Op(DIVE, "add", "", deep_dive())], new_sources=[], digest=None, as_of_files=[],
                                                    today_iso="2026-10-02", now_iso="2026-10-02T03:00:00Z", effects=effects)
    assert errs == [] and applied == [f"create {DIVE}"] and touched == {DIVE} and staged.docs[DIVE]["asOf"] == "2026-10-02"
    assert [(c.path, c.kind) for c in effects[0]] == [("", ADDED)]
