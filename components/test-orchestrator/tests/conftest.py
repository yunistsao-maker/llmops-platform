"""Fixtures for test-orchestrator tests. Database tests need TEST_DATABASE_URL (a throwaway PostgreSQL); they are
skipped without it."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "libs/llmops-common/src"))
sys.path.insert(0, str(ROOT / "components/db-migrate/src"))
sys.path.insert(0, str(ROOT / "components/test-orchestrator/src"))
MIGRATIONS = ROOT / "contracts/db/migrations"
SAMPLE_ROUTES = ROOT / "components/test-orchestrator/config/routes.sample.json"


@pytest.fixture(scope="session")
def database_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    return url


@pytest.fixture()
def db(database_url, monkeypatch):
    """Fresh schema for every test: drop and re-apply all migrations."""
    import psycopg
    from psycopg.rows import dict_row

    from db_migrate.__main__ import migrate
    from llmops_common import db as dbmod

    with psycopg.connect(database_url, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS platform CASCADE; DROP SCHEMA IF EXISTS requests CASCADE; "
                  "DROP SCHEMA IF EXISTS tests CASCADE; DROP TABLE IF EXISTS public.llmops_schema_migrations")
    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as c:
        migrate(c, MIGRATIONS)
    monkeypatch.setenv("DATABASE_URL", database_url)
    dbmod.close_pool()
    yield dbmod
    dbmod.close_pool()


@pytest.fixture()
def store():
    from llmops_common.storage import MemoryStore

    return MemoryStore()


ROUTES = {
    "provider_defaults": {"azure": {"endpoint": "https://res.services.ai.azure.com", "api_version": "2025-04-01-preview"}},
    "routes": [
        {"model_id": "cand", "kind": "gateway", "provider": "gateway", "gateway": "onprem", "target": {"model": "cand"}},
        {"model_id": "cand", "kind": "direct", "provider": "bedrock", "region": "us-east-1",
         "target": {"model": "global.anthropic.cand", "region": "us-east-1"}},
        {"model_id": "prod", "kind": "gateway", "provider": "gateway", "gateway": "onprem", "target": {"model": "prod"}},
        {"model_id": "prod", "kind": "direct", "provider": "bedrock", "region": "us-east-1",
         "target": {"model": "global.anthropic.prod", "region": "us-east-1"}},
        {"model_id": "cand", "kind": "direct", "provider": "anthropic", "region": "global", "enabled": False,
         "target": {"model": "cand"}},
    ],
}


@pytest.fixture()
def routes_file(tmp_path):
    p = tmp_path / "routes.json"
    p.write_text(json.dumps(ROUTES), encoding="utf-8")
    return str(p)


@pytest.fixture()
def orch(db, store, routes_file):
    from test_orchestrator.service import Orchestrator
    from test_orchestrator.settings import Settings

    o = Orchestrator(store, Settings(routes_file=routes_file, route_plan_delay=0, background=False))
    with db.connection() as conn:
        o.sync_routes(conn)
    return o


def approve(store, *sec_ids, dataset_id="ds", version=1):
    index = store.get_json("llmops-datasets", "index.json", default={})
    for sid in sec_ids:
        index[sid] = {"dataset_id": f"{dataset_id}-{sid.lower()}", "version": version}
    store.put_json("llmops-datasets", "index.json", index)
