"""Fixtures for results-publisher tests. Database tests need TEST_DATABASE_URL (a throwaway PostgreSQL); they are
skipped without it."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "libs/llmops-common/src"))
sys.path.insert(0, str(ROOT / "components/db-migrate/src"))
sys.path.insert(0, str(ROOT / "components/results-publisher/src"))
MIGRATIONS = ROOT / "contracts/db/migrations"


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
    monkeypatch.delenv("JUDGE_LABEL", raising=False)
    monkeypatch.delenv("CURRENT_PURPOSES", raising=False)
    dbmod.close_pool()
    yield dbmod
    dbmod.close_pool()


@pytest.fixture()
def store():
    from llmops_common.storage import MemoryStore

    return MemoryStore()
