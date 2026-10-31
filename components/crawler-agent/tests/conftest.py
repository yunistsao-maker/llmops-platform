"""Test configuration: make the component importable and give tests a clean, offline environment.

Nothing here reaches the internet, a cloud or a real model: pages come from httpx.MockTransport, the model is the
scripted test double (or the gateway over httpx.MockTransport), object storage is llmops_common's MemoryStore (or moto's
S3 server on 127.0.0.1 for the endpoint tests).  Database tests need TEST_DATABASE_URL (a throwaway PostgreSQL); they are
skipped without it.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]          # components/crawler-agent
REPO = ROOT.parents[1]                               # the monorepo
for extra in (ROOT / "src", REPO / "libs/llmops-common/src", REPO / "components/db-migrate/src", Path(__file__).resolve().parent):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))
MIGRATIONS = REPO / "contracts/db/migrations"

# nothing in the unit tests may depend on the wall clock, real credentials or a real endpoint
os.environ.setdefault("RESEARCH_NOW", "2026-10-02T03:00:00Z")
os.environ.setdefault("RESEARCH_TODAY", "2026-10-02")
for name, value in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_DEFAULT_REGION": "us-east-1",
                    "AWS_EC2_METADATA_DISABLED": "true"}.items():
    os.environ[name] = value
for name in ("AWS_PROFILE", "AWS_ENDPOINT_URL", "S3_ENDPOINT_URL", "LLM_GATEWAY_URL", "LLM_GATEWAY_KEY", "DATABASE_URL",
             "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "SEARCH_BACKEND", "PORTAL_DATA_URI", "OUTPUT_URI"):
    os.environ.pop(name, None)


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False, help="run tests that fetch real public web pages")


def pytest_configure(config):
    config.addinivalue_line("markers", "live: needs outbound internet access to public sites (run with --live)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="needs --live (real public web access)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture()
def memory_store():
    """Every s3:// location of the agent goes to an in-process store for the duration of the test."""
    from llmops_common.storage import MemoryStore

    from research import objectstore

    store = MemoryStore()
    old = objectstore.set_store(store)
    yield store
    objectstore.set_store(old)


@pytest.fixture(scope="session")
def database_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    return url


@pytest.fixture()
def db(database_url, monkeypatch):
    """Fresh platform schema for every test: drop and re-apply all migrations (libs/llmops-common/tests/conftest.py)."""
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
