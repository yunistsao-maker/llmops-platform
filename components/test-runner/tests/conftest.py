"""Fixtures for test-runner tests. Database tests need TEST_DATABASE_URL (a throwaway PostgreSQL); they are skipped
without it. No test reaches a cloud or the internet: gateways use httpx.MockTransport, Bedrock a botocore Stubber."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "libs/llmops-common/src"))
sys.path.insert(0, str(ROOT / "components/db-migrate/src"))
sys.path.insert(0, str(ROOT / "components/test-runner/src"))
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
    dbmod.close_pool()
    yield dbmod
    dbmod.close_pool()


@pytest.fixture(autouse=True)
def _quiet_env(monkeypatch):
    for name in ("TARGET_GATEWAY_URL", "JUDGE_GATEWAY_URL", "ANTHROPIC_API_KEY", "AZURE_ACCESS_TOKEN", "AZURE_TENANT_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PROVIDER_BACKOFF_SECONDS", "0")


@pytest.fixture()
def store():
    from llmops_common.storage import MemoryStore

    return MemoryStore()


def put_dataset(store, sec_id: str, dataset_id: str, version: int, cases: list[dict], assets: dict | None = None) -> None:
    """Write a dataset version the way dataset-manager does (cases.jsonl, assets, manifest.json with sha256)."""
    import hashlib

    prefix = f"{sec_id}/{dataset_id}/v{version}/"
    body = "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases).encode()
    for name, data in (assets or {}).items():
        store.put_bytes("llmops-datasets", prefix + "assets/" + name, data)
    store.put_bytes("llmops-datasets", prefix + "cases.jsonl", body)
    store.put_json("llmops-datasets", prefix + "manifest.json", {"sec_id": sec_id, "dataset_id": dataset_id, "version": version,
                                                                  "n": len(cases), "sha256": hashlib.sha256(body).hexdigest()})
