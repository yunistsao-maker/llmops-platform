"""Route ids, ROUTES_FILE parsing and route.added mapping (no database)."""
from __future__ import annotations

import pytest
from conftest import SAMPLE_ROUTES

from test_orchestrator import routes as rt


def test_sample_routes_file_parses():
    cfg = rt.load_routes_file(str(SAMPLE_ROUTES))
    ids = [r["id"] for r in cfg["routes"]]
    assert "gw:onprem:claude-sonnet-5-5" in ids and "direct:bedrock:us-east-1:claude-sonnet-5-5" in ids
    assert "direct:vertex:global:gemini-3-8-flash" in ids and "direct:azure:eastus2:gpt-6-luna" in ids
    assert len(ids) == len(set(ids))
    assert cfg["provider_defaults"]["vertex"]["project"]


@pytest.mark.parametrize("raw, message", [
    ({"kind": "direct", "provider": "bedrock"}, "model_id"),
    ({"model_id": "m", "provider": "openai"}, "provider"),
    ({"model_id": "m", "kind": "gateway", "provider": "bedrock"}, "provider gateway"),
    ({"model_id": "m", "kind": "gateway", "provider": "gateway", "id": "direct:x:y:m"}, "gw:"),
])
def test_invalid_routes(raw, message):
    with pytest.raises(rt.RouteError, match=message):
        rt.normalise(raw)


def test_route_from_event_per_provider():
    defaults = {"azure": {"endpoint": "https://res.services.ai.azure.com", "api_version": "v1"}}
    bedrock = rt.route_from_event({"model_id": "claude-sonnet-5-5", "provider": "aws", "region": "us-west-2",
                                   "model_name": "us.anthropic.claude-sonnet-5-5", "status": "ga"}, defaults)
    assert bedrock["id"] == "direct:bedrock:us-west-2:claude-sonnet-5-5" and bedrock["enabled"]
    assert bedrock["target"] == {"model": "us.anthropic.claude-sonnet-5-5", "region": "us-west-2"}
    azure = rt.route_from_event({"model_id": "gpt-6-luna", "provider": "azure", "region": "eastus2", "model_name": "luna"}, defaults)
    assert azure["target"]["deployment"] == "luna" and azure["target"]["endpoint"].startswith("https://") and azure["enabled"]
    vertex = rt.route_from_event({"model_id": "gemini-4-argon", "provider": "gcp", "model_name": "gemini-4-argon"}, defaults)
    assert vertex["id"] == "direct:vertex:global:gemini-4-argon" and not vertex["enabled"]  # no project configured
    retired = rt.route_from_event({"model_id": "old", "provider": "bedrock", "model_name": "old", "status": "retired"}, {})
    assert not retired["enabled"]
    gw = rt.route_from_event({"model_id": "m", "provider": "gateway", "model_name": "m-alias"}, {})
    assert gw["id"] == "gw:onprem:m" and gw["target"] == {"model": "m-alias"}
    assert rt.route_from_event({"model_id": "m", "provider": "ibm"}, {}) is None
    assert rt.gateway_of("gw:onprem:m") == "onprem" and rt.gateway_of("direct:bedrock:us-east-1:m") is None
