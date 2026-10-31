"""Gateway version watcher (platform.events as state) and the HTTP API."""
from __future__ import annotations

import httpx
import pytest
from conftest import approve

from llmops_common.auth import Authenticator
from llmops_common.llm import GatewayClient
from test_orchestrator.watcher import Watcher, check_gateway


class Versions:
    """A fake LiteLLM: /health/readiness/details needs the admin key and carries litellm_version (v1.105 behaviour)."""

    def __init__(self, *versions):
        self.versions = list(versions)
        self.paths = []

    def __call__(self, request):
        self.paths.append(request.url.path)
        if request.url.path == "/health/readiness/details":
            if request.headers.get("authorization") != "Bearer sk-admin":
                return httpx.Response(401, json={"error": "auth"})
            return httpx.Response(200, json={"status": "healthy", "litellm_version": self.versions[0]})
        return httpx.Response(200, json={"status": "healthy", "db": "connected"})

    def client(self, key="sk-admin"):
        return GatewayClient("http://gw:4000", key, transport=httpx.MockTransport(self), max_retries=0)


def events(db):
    with db.connection() as c:
        return [r["payload"] for r in c.execute("SELECT payload FROM platform.events WHERE type = 'gateway.version_changed' ORDER BY id")]


def test_watcher_publishes_first_and_changed_versions_only(db):
    gw = Versions("1.104.0")
    client = gw.client()
    for _ in range(2):
        with db.connection() as c:
            check_gateway(c, client, "onprem")
    gw.versions = ["1.105.2"]
    with db.connection() as c:
        assert check_gateway(c, client, "onprem")
    gw.versions = ["1.104.0"]  # rolled back: A -> B -> A is recorded too
    with db.connection() as c:
        assert check_gateway(c, client, "onprem")
    assert [(e["previous_version"], e["version"]) for e in events(db)] == [(None, "1.104.0"), ("1.104.0", "1.105.2"), ("1.105.2", "1.104.0")]
    with db.connection() as c:
        queues = [r["queue"] for r in c.execute("SELECT queue FROM platform.jobs ORDER BY id")]
    assert queues.count("test-orchestrator") == 3 and queues.count("notifier") == 3


def test_watcher_without_admin_key_publishes_nothing(db):
    gw = Versions("1.105.2")
    with db.connection() as c:
        assert check_gateway(c, gw.client(key="vk-plain"), "onprem") is None
    assert events(db) == [] and "/health/readiness" in gw.paths


def test_watcher_thread_tick_polls_and_reaps(db, orch):
    gw = Versions("1.105.2")
    w = Watcher(orch, client=gw.client())
    schedule = {}
    w.tick(0.0, schedule)
    w.tick(1.0, schedule)  # inside the poll interval: no second request
    assert events(db)[0]["version"] == "1.105.2" and gw.paths.count("/health/readiness/details") == 1
    assert schedule["poll"] == orch.settings.gw_poll_seconds and schedule["reap"] == orch.settings.reap_seconds


@pytest.fixture()
def client(db, store, orch, monkeypatch):
    from fastapi.testclient import TestClient

    from test_orchestrator.api import create_app

    monkeypatch.setenv("ROLE_ADMIN", "llmops-admins")
    app = create_app(orch, Authenticator(mode="dev", dev_groups=("llmops-admins",)), background=False)
    with TestClient(app) as c:
        yield c


def test_api_plan_lifecycle(client, store):
    approve(store, "SEC-04")
    r = client.post("/api/tests/plans", json={"model_id": "cand", "sec_ids": ["SEC-04"], "trigger": "manual"})
    assert r.status_code == 201, r.text
    plan = r.json()
    assert plan["jobs_total"] == 2 and plan["purpose"] == "adhoc"
    got = client.get(f"/api/tests/plans/{plan['plan_id']}").json()
    assert got["run_id"] == plan["run_id"] and got["status"] == "running" and got["trigger_detail"]["requested_by"] == "dev"
    run = client.get(f"/api/tests/runs/{plan['run_id']}").json()
    assert run["plan"]["id"] == plan["plan_id"] and run["results"] == []
    routes = client.get("/api/tests/routes").json()["routes"]
    assert {r["id"] for r in routes} >= {"gw:onprem:cand", "direct:anthropic:global:cand"}
    assert client.get("/healthz").json() == {"status": "ok"} and client.get("/readyz").status_code == 200


def test_api_errors(client):
    assert client.post("/api/tests/plans", json={"model_id": "cand", "sec_ids": ["SEC-99"]}).status_code == 400
    assert client.post("/api/tests/plans", json={"route_ids": ["gw:onprem:nope"]}).status_code == 404
    assert client.post("/api/tests/plans", json={"model_id": "cand", "trigger": "route.added"}).status_code == 422
    assert client.get("/api/tests/plans/2b1c8f3e-0000-4000-8000-000000000000").status_code == 404
    assert client.get("/api/tests/runs/nope").status_code == 404


def test_api_requires_admin(db, store, orch, monkeypatch):
    from fastapi.testclient import TestClient

    from test_orchestrator.api import create_app

    monkeypatch.setenv("ROLE_ADMIN", "llmops-admins")
    app = create_app(orch, Authenticator(mode="dev", dev_groups=("someone-else",)), background=False)
    with TestClient(app) as c:
        assert c.get("/api/tests/routes").status_code == 403
        assert c.post("/api/tests/plans", json={"model_id": "cand"}).status_code == 403


def test_background_threads_start_and_stop(db, store, orch, monkeypatch):
    from fastapi.testclient import TestClient

    from test_orchestrator.api import create_app

    monkeypatch.setenv("ROLE_ADMIN", "llmops-admins")
    app = create_app(orch, Authenticator(mode="dev", dev_groups=("llmops-admins",)), background=True)
    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200
        import threading

        names = {t.name for t in threading.enumerate()}
        assert {"worker", "gateway-watcher"} <= names
    assert not any(t.name == "worker" and t.is_alive() for t in threading.enumerate())
