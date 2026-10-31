"""End-to-end through PostgreSQL: publish the three events, let the queue worker build. Needs TEST_DATABASE_URL."""
import os

import pytest

from conftest import MIGRATIONS
from llmops_common import queue
from llmops_common.storage import MemoryStore
from view_builder.store_io import VIEW_POINTER, seed
from view_builder.worker import QUEUE, Builder, portal_tz

B = "llmops-curated"


@pytest.fixture()
def db(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    import psycopg
    from psycopg.rows import dict_row

    from db_migrate.__main__ import migrate
    from llmops_common import db as dbmod

    with psycopg.connect(url, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS platform CASCADE; DROP SCHEMA IF EXISTS requests CASCADE; "
                  "DROP SCHEMA IF EXISTS tests CASCADE; DROP TABLE IF EXISTS public.llmops_schema_migrations")
    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as c:
        migrate(c, MIGRATIONS)
    monkeypatch.setenv("DATABASE_URL", url)
    dbmod.close_pool()
    yield dbmod
    dbmod.close_pool()


def test_events_drive_builds(db, mini_data):
    store = MemoryStore()
    seed(store, B, mini_data, security_run=None, matrix=None, editorial=None, release_id="r1", source="mini")
    builder = Builder(store, B, tz=portal_tz(), connection_factory=db.connection)
    with db.connection() as c:
        queue.publish(c, "release.published", {"release_id": "r1", "previous_release_id": None, "manifest_key":
                                               "portal/manifest.json", "held": False, "changes": {}},
                      source="test", dedupe_key="release.published:r1")
        queue.publish(c, "editorial.updated", {"key": "portal/editorial/current.json"}, source="test")
        queue.publish(c, "security.results.published", {"run_id": "x", "key": "portal/security/x.json"}, source="test")
        queue.publish(c, "request.submitted", {"request_id": "q"}, source="test")  # not ours: goes to notifier only
    w = queue.Worker(QUEUE, builder.handle, poll_seconds=0.01)
    assert w.run_once() and w.run_once() and w.run_once() and not w.run_once()
    with db.connection() as c:
        rows = c.execute("SELECT status FROM platform.jobs WHERE queue = %s ORDER BY id", (QUEUE,)).fetchall()
    assert [r["status"] for r in rows] == ["done", "done", "done"]
    assert store.get_json(B, VIEW_POINTER)["release_id"] == "r1"
