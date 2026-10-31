"""The catalog diff behind release.published's `changes`, route.added and model.status_changed."""

from __future__ import annotations

import copy
import json

import pytest
from conftest import snapshot_tree

from publisher import catalog_diff as D

PORTAL_CATALOG = None  # the real portal catalog is richer than the bundled sample; the tests build what they need


def offering(model_id, cloud, service, status="ga", **extra):
    off = {"id": f"{model_id}@{service}", "cloud": cloud, "service": service, "status": status, "providerModelId": extra.pop("pmid", model_id),
           "inferenceTargets": extra.pop("targets", []), "pricing": extra.pop("pricing", []), "lifecycle": extra.pop("lifecycle", {})}
    off.update(extra)
    return off


def catalog(*models):
    return {"schema": "catalog/v1", "models": list(models)}


def model(model_id, *offerings, status="evaluating", name=None):
    return {"id": model_id, "name": name or model_id.title(), "platformStatus": status, "offerings": list(offerings)}


SONNET = model("claude-sonnet-5-5",
               offering("claude-sonnet-5-5", "aws", "bedrock", pmid="anthropic.claude-sonnet-5-5",
                        targets=[{"scope": "geo", "id": "eu.anthropic.claude-sonnet-5-5"}, {"scope": "geo", "id": "us.anthropic.claude-sonnet-5-5"},
                                 {"scope": "global", "id": "global.anthropic.claude-sonnet-5-5"}],
                        pricing=[{"scope": "global", "currency": "USD", "unit": "per_1m_tokens", "input": 3.0, "output": 15.0, "cacheRead": 0.3}]),
               offering("claude-sonnet-5-5", "gcp", "agent-platform", targets=[{"scope": "global", "id": "global"}]),
               offering("claude-sonnet-5-5", "azure", "foundry", status="preview", pmid="claude-sonnet-5-5（version 1）"),
               offering("claude-sonnet-5-5", "vendor-direct", "anthropic-api"),
               offering("claude-sonnet-5-5", "vendor-direct", "openai-api"),
               status="ga", name="Claude Sonnet 5.5")


def test_routes_are_the_callable_offerings_on_the_platform_s_providers():
    routes = D.routes(catalog(SONNET))
    assert sorted(routes) == ["direct:anthropic:global:claude-sonnet-5-5", "direct:azure:global:claude-sonnet-5-5",
                              "direct:bedrock:us-east-1:claude-sonnet-5-5", "direct:vertex:global:claude-sonnet-5-5"]
    bedrock = routes["direct:bedrock:us-east-1:claude-sonnet-5-5"]
    assert bedrock == {"route_id": "direct:bedrock:us-east-1:claude-sonnet-5-5", "model_id": "claude-sonnet-5-5", "provider": "bedrock",
                       "region": "us-east-1", "model_name": "global.anthropic.claude-sonnet-5-5", "status": "ga"}
    assert routes["direct:azure:global:claude-sonnet-5-5"]["model_name"] == "claude-sonnet-5-5"  # ("（version 1）" is prose)
    assert routes["direct:azure:global:claude-sonnet-5-5"]["status"] == "preview"
    assert D.route_event(bedrock) == {"model_id": "claude-sonnet-5-5", "provider": "bedrock", "region": "us-east-1",
                                      "model_name": "global.anthropic.claude-sonnet-5-5", "status": "ga"}


def test_a_bedrock_route_without_a_global_profile_uses_the_region_s_geography():
    opus = model("claude-opus-5-5", offering("claude-opus-5-5", "aws", "bedrock", pmid="anthropic.claude-opus-5-5",
                                             targets=[{"scope": "geo", "id": "jp.anthropic.claude-opus-5-5"},
                                                      {"scope": "geo", "id": "us.anthropic.claude-opus-5-5"}]))
    assert D.routes(catalog(opus))["direct:bedrock:us-east-1:claude-opus-5-5"]["model_name"] == "us.anthropic.claude-opus-5-5"
    tokyo = D.routes(catalog(opus), D.route_regions({"ROUTE_REGION_BEDROCK": "ap-northeast-1"}))
    assert tokyo["direct:bedrock:ap-northeast-1:claude-opus-5-5"]["model_name"] == "jp.anthropic.claude-opus-5-5"
    bare = model("m", offering("m", "aws", "bedrock", pmid="vendor.m-v1:0"))
    assert D.routes(catalog(bare))["direct:bedrock:us-east-1:m"]["model_name"] == "vendor.m-v1:0"


@pytest.mark.parametrize("status", ["announced", "retired"])
def test_an_offering_that_cannot_be_called_is_not_a_route(status):
    assert D.routes(catalog(model("m", offering("m", "aws", "bedrock", status=status)))) == {}


def test_the_diff_names_new_routes_changed_prices_and_retirements():
    old = catalog(SONNET)
    new = copy.deepcopy(old)
    m = new["models"][0]
    m["offerings"][0]["pricing"][0].update(input=2.5, cacheRead=0.25)                            # price cut
    m["offerings"][0]["pricing"].append({"scope": "geo", "input": 3.3, "output": 16.5})           # a new price sheet
    m["offerings"][1]["status"] = "deprecated"                                                    # on its way out
    m["offerings"][1]["lifecycle"] = {"retirementDate": "2027-09-28"}
    del m["offerings"][3]                                                                         # anthropic-api offering gone
    haiku = model("claude-haiku-5-5", offering("claude-haiku-5-5", "aws", "bedrock", pmid="anthropic.claude-haiku-5-5",
                                               targets=[{"scope": "global", "id": "global.anthropic.claude-haiku-5-5"}]),
                  offering("claude-haiku-5-5", "gcp", "agent-platform", status="announced"))
    new["models"].append(haiku)
    diff = D.diff_catalogs(old, new)
    assert [r["route_id"] for r in diff["routes_added"]] == ["direct:bedrock:us-east-1:claude-haiku-5-5"]
    assert diff["prices_changed"] == [
        {"model_id": "claude-sonnet-5-5", "offering": "claude-sonnet-5-5@bedrock", "provider": "bedrock", "scope": "global",
         "before": {"input": 3.0, "cacheRead": 0.3}, "after": {"input": 2.5, "cacheRead": 0.25}},
        {"model_id": "claude-sonnet-5-5", "offering": "claude-sonnet-5-5@bedrock", "provider": "bedrock", "scope": "geo",
         "before": None, "after": {"input": 3.3, "output": 16.5}}]
    assert [(r["offering"], r["change"], r["before"], r["after"]) for r in diff["retirements"]] == [
        ("claude-sonnet-5-5@agent-platform", "status", "ga", "deprecated"),
        ("claude-sonnet-5-5@agent-platform", "retirementDate", None, "2027-09-28"),
        ("claude-sonnet-5-5@anthropic-api", "removed", "ga", None)]
    # a new model enters as evaluating ("watch" before: it was not in the catalog)
    assert diff["status_changes"] == [{"model_id": "claude-haiku-5-5", "model_name": "Claude-Haiku-5-5", "from": "watch", "to": "evaluating"}]
    assert D.diff_catalogs(old, old) == {"routes_added": [], "prices_changed": [], "retirements": [], "status_changes": []}


@pytest.mark.parametrize("before, after, expect", [
    ("evaluating", "beta", ("evaluating", "trial")),
    ("beta", "ga", ("trial", "available")),
    ("ga", "restricted", ("available", "paused")),
    ("ga", "removed", ("available", "retired")),
    ("evaluating", "not-planned", ("evaluating", "paused")),
    ("ga", "deprecating", None),            # still requestable: nothing to tell
    ("evaluating", "something-new", None),  # a status nobody can be told the meaning of
])
def test_platform_status_moves_in_the_contract_s_terms(before, after, expect):
    old, new = catalog(model("m", status=before, name="Model M")), catalog(model("m", status=after, name="Model M"))
    changes = D.status_changes(old, new)
    if expect is None:
        assert changes == []
    else:
        assert changes == [{"model_id": "m", "model_name": "Model M", "from": expect[0], "to": expect[1]}]
        assert D.status_event(changes[0]) == changes[0]
    assert set(D.PLATFORM_STATUS.values()) <= {"watch", "evaluating", "trial", "available", "paused", "retired"}


def test_the_diff_of_two_releases_reads_their_catalog_files():
    files = snapshot_tree()
    first = D.diff_release_files(None, files)  # the first release: every route is new
    assert first["routes_added"] and all(r["route_id"].startswith("direct:") for r in first["routes_added"])
    assert first["prices_changed"] == [] and first["retirements"] == []
    assert D.diff_release_files(files, files) == {"routes_added": [], "prices_changed": [], "retirements": [], "status_changes": []}
    broken = {**files, "data/catalog.json": b"not json"}
    assert D.diff_release_files(files, broken)["routes_added"] == []  # (an unreadable catalog has no routes)
    assert json.dumps(first, ensure_ascii=False)  # JSON all the way (it goes into events and release.json)
