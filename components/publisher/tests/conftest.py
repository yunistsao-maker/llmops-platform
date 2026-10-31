"""Shared fixtures: a portal checkout, and the curated bucket on llmops_common's MemoryStore and on (moto's) S3.

Nothing here reaches a real store, a cloud or the wall clock.  Database tests need TEST_DATABASE_URL (a throwaway
PostgreSQL); they are skipped without it.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]           # components/publisher
REPO = ROOT.parents[1]
# The shared portal data model (research.*) is the crawler-agent's vendored copy: the same code validates on both sides.
for extra in (ROOT / "src", REPO / "components/crawler-agent/src", REPO / "libs/llmops-common/src", REPO / "components/db-migrate/src",
              Path(__file__).resolve().parent):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))
MIGRATIONS = REPO / "contracts/db/migrations"

# nothing here may reach real AWS or depend on the wall clock
for name, value in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_SESSION_TOKEN": "testing",
                    "AWS_DEFAULT_REGION": "us-east-1", "AWS_EC2_METADATA_DISABLED": "true"}.items():
    os.environ[name] = value
for name in ("AWS_PROFILE", "AWS_ENDPOINT_URL", "S3_ENDPOINT_URL", "DATABASE_URL", "PUBLISH_MODE", "AUTO_MAX_OPS", "AUTO_ALLOW_REMOVE",
             "BUCKET_CURATED", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
    os.environ.pop(name, None)

from llmops_common.storage import MemoryStore  # noqa: E402

from publisher import publish as P  # noqa: E402
from publisher.storage import PrefixStore  # noqa: E402

SNAPSHOT = REPO / "components/crawler-agent/src/research/portal/snapshot"
FIXTURES = Path(__file__).parent / "fixtures"
PORTAL_FILES = FIXTURES / "portal"
COMPAT, PERF, INVENTORY = "runs/compat/example-mock.json", "runs/perf/example-mock.json", "data/internal/platform-inventory.example.json"
BUCKET = "llmops-curated"
#: the fixture run in fixtures/accepted_run.json was researched against the release made from snapshot_tree() at this time
T0 = dt.datetime(2026, 10, 2, 1, 0, 0, tzinfo=dt.timezone.utc)
T1 = dt.datetime(2026, 10, 2, 3, 30, 0, tzinfo=dt.timezone.utc)
T2 = dt.datetime(2026, 10, 3, 9, 0, 0, tzinfo=dt.timezone.utc)


def squash(text: str) -> str:
    return "".join(str(text).replace("┆", "").split())


def snapshot_tree() -> dict[str, bytes]:
    """A small but real portal checkout: the agent's bundled sample data plus the files only people and tools own
    (fixtures/portal: the portal repo's own example test runs, inventory example and gateway config)."""
    files = {p.relative_to(SNAPSHOT).as_posix(): p.read_bytes() for p in sorted(SNAPSHOT.rglob("*"))
             if p.is_file() and p.name != "snapshot_manifest.json"}
    files.update({p.relative_to(PORTAL_FILES).as_posix(): p.read_bytes() for p in sorted(PORTAL_FILES.rglob("*")) if p.is_file()})
    return files


def tool_file(rel: str, **changes) -> bytes:
    """A valid test run / inventory, made from one of the example files with a few fields changed."""
    doc = json.loads((PORTAL_FILES / rel).read_text(encoding="utf-8"))
    doc.update(changes)
    return (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_tree(root: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


#: The fixture proposal's id.  It is spelled out in the tests; this is the one place that notices when the fixture was
#: regenerated with another id.
PROPOSAL_ID = "prop-20261002T030000Z-7dd4e9c6"


def accepted_run() -> dict:
    record = json.loads((FIXTURES / "accepted_run.json").read_text(encoding="utf-8"))
    assert record["proposal"]["id"] == PROPOSAL_ID, f"fixtures/accepted_run.json now holds {record['proposal']['id']}: replace {PROPOSAL_ID}"
    return record


RUN = accepted_run()["runId"]


class Plane:
    """The curated bucket as the publisher sees it: portal/, pipeline/ (its own records) and research/ (the crawler's)."""

    def __init__(self, store, kind):
        self.store, self.kind = store, kind
        self.data = PrefixStore(store, BUCKET, "portal/")
        self.control = PrefixStore(store, BUCKET, "pipeline/")
        self.research = PrefixStore(store, BUCKET, "research/")
        self.root = PrefixStore(store, BUCKET, "")

    def bootstrap(self, tree=None, now=T0):
        outcome = P.publish_tree(self.data, tree or snapshot_tree(), bootstrap=True, actor="test", now=now)
        assert outcome.status == "published", outcome.reasons
        return outcome

    def save_run(self, record: dict) -> str:
        """Store a run record where the crawler would (research/runs/<runId>.json); returns its curated key."""
        key = f"runs/{record['runId']}.json"
        self.research.put(key, json.dumps(record, ensure_ascii=False).encode("utf-8"), content_type="application/json")
        return "research/" + key

    def save_proposal(self, proposal: dict) -> str:
        """Store a proposal as the crawler does (research/proposals/<id>.json); returns its curated key."""
        key = f"proposals/{proposal['id']}.json"
        self.research.put(key, json.dumps(proposal, ensure_ascii=False).encode("utf-8"), content_type="application/json")
        return "research/" + key


@pytest.fixture(params=["memory", "s3"])
def plane(request):
    if request.param == "memory":
        yield Plane(MemoryStore(), "memory")
        return
    moto = pytest.importorskip("moto")
    import boto3

    from llmops_common.storage import S3Store

    with moto.mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        yield Plane(S3Store(s3), "s3")


@pytest.fixture()
def folder_plane():
    """(The AWS tests had a folder-backed plane; the platform's in-process store is MemoryStore.)"""
    return Plane(MemoryStore(), "memory")


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
