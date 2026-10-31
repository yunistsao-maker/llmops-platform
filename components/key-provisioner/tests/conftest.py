"""key-provisioner test fixtures. Database tests need TEST_DATABASE_URL (a throwaway PostgreSQL database that the
tests may wipe); they are skipped without it. Shared helpers live in kp_fakes.py (not here), so several components'
suites can run in one pytest session."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
for path in ("libs/llmops-common/src", "components/db-migrate/src", "components/key-provisioner/src",
             "components/key-provisioner/tests"):
    if str(ROOT / path) not in sys.path:
        sys.path.insert(0, str(ROOT / path))
MIGRATIONS = ROOT / "contracts/db/migrations"

from kp_fakes import FakeLiteLLM  # noqa: E402


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
def fake_litellm():
    return FakeLiteLLM()


@pytest.fixture()
def make_provisioner(tmp_path, fake_litellm):
    from key_provisioner.litellm import LiteLLMAdmin
    from key_provisioner.provisioner import Provisioner
    from key_provisioner.settings import Settings
    from key_provisioner.sinks import FileSink

    def factory(sink=None, **settings):
        base = {"litellm_url": "http://litellm.test", "secret_sink": "file", "gateway_public_url": "https://llm.example"}
        base.update(settings)
        admin = LiteLLMAdmin("http://litellm.test", fake_litellm.master_key, transport=fake_litellm.transport())
        return Provisioner(Settings(**base), admin, sink or FileSink(tmp_path / "secrets"))

    return factory
