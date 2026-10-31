"""Helpers shared by the request-api tests (a uniquely named module, so the suites of several components can run
in one pytest session; conftest.py only holds fixtures)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for _path in ("libs/llmops-common/src", "components/db-migrate/src", "components/request-api/src"):
    if str(ROOT / _path) not in sys.path:
        sys.path.insert(0, str(ROOT / _path))
MIGRATIONS = ROOT / "contracts/db/migrations"

from llmops_common.auth import Principal  # noqa: E402

REQUESTER = Principal(sub="u-amy", email="amy@example.com", name="Amy", groups=("staff",))
OTHER = Principal(sub="u-bob", email="bob@example.com", name="Bob", groups=("staff",))
G2 = Principal(sub="u-legal", email="legal@example.com", groups=("legal-reviewers",))
G8 = Principal(sub="u-beta", email="beta@example.com", groups=("beta-owners",))
ADMIN = Principal(sub="u-ops", email="ops@example.com", groups=("llmops-admins",))
ROLE_ENV = {"ROLE_ADMIN": "llmops-admins", "ROLE_REVIEWER_G2": "legal-reviewers", "ROLE_REVIEWER_G8": "beta-owners"}


def valid_request(**over) -> dict:
    body = {"team": "智慧製造處", "use_case": "客服摘要", "models": ["claude-sonnet-5-5"], "data_class": "sec-c",
            "rpd": 5000, "tokens_in": 3000, "tokens_out": 500, "target_date": "2026-12-15"}
    body.update(over)
    return body


def reset_schema(database_url: str) -> None:
    """Drop and re-apply all migrations (the conftest pattern of libs/llmops-common)."""
    import psycopg
    from psycopg.rows import dict_row

    from db_migrate.__main__ import migrate

    with psycopg.connect(database_url, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS platform CASCADE; DROP SCHEMA IF EXISTS requests CASCADE; "
                  "DROP SCHEMA IF EXISTS tests CASCADE; DROP TABLE IF EXISTS public.llmops_schema_migrations")
    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as c:
        migrate(c, MIGRATIONS)


class Api:
    """TestClient wrapper that calls the API as a given Principal (overrides the auth dependency)."""

    def __init__(self, app):
        from fastapi.testclient import TestClient

        self.app = app
        self.client = TestClient(app)

    def call(self, user, method: str, url: str, **kw):
        self.app.dependency_overrides[self.app.state.current_user] = lambda: user
        try:
            return self.client.request(method, url, **kw)
        finally:
            self.app.dependency_overrides.clear()

    def post(self, user, url, **kw):
        return self.call(user, "POST", url, **kw)

    def get(self, user, url, **kw):
        return self.call(user, "GET", url, **kw)


def events(db, event_type: str | None = None) -> list[dict]:
    with db.connection() as c:
        rows = c.execute("SELECT type, payload, dedupe_key FROM platform.events ORDER BY id").fetchall()
    return [r for r in rows if event_type is None or r["type"] == event_type]


def jobs(db, queue: str) -> list[dict]:
    with db.connection() as c:
        return c.execute("SELECT id, payload, status, dedupe_key FROM platform.jobs WHERE queue = %s ORDER BY id",
                         (queue,)).fetchall()
