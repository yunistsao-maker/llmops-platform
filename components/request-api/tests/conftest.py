"""request-api test fixtures. Database tests need TEST_DATABASE_URL (a throwaway PostgreSQL database that the
tests may wipe); they are skipped without it. Same pattern as libs/llmops-common/tests/conftest.py.
Shared helpers live in ra_support.py (not here), so several components' suites can run in one session."""
from __future__ import annotations

import os

import pytest
from ra_support import ROLE_ENV, Api, reset_schema


@pytest.fixture(scope="session")
def database_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    return url


@pytest.fixture()
def db(database_url, monkeypatch):
    """Fresh schema for every test: drop and re-apply all migrations."""
    from llmops_common import db as dbmod

    reset_schema(database_url)
    monkeypatch.setenv("DATABASE_URL", database_url)
    dbmod.close_pool()
    yield dbmod
    dbmod.close_pool()


@pytest.fixture()
def role_env(monkeypatch):
    for k, v in ROLE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("REQUEST_API_WORKER", "false")
    monkeypatch.setenv("ALLOW_SELF_APPROVAL", "false")
    for k in ("BUDGET_MODEL_PRICES", "FULL_KEY_DURATION", "MAX_OPEN_REQUESTS_PER_USER", "MAX_BODY_BYTES"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture()
def api(db, role_env):
    from request_api.app import create_app

    return Api(create_app())
