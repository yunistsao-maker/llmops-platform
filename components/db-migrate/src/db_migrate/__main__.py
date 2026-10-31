"""db-migrate: apply contracts/db/migrations to PostgreSQL, and (local/test only) create the object storage buckets.

    python -m db_migrate migrate          apply pending *.sql in order (default)
    python -m db_migrate status           list applied / pending migrations
    python -m db_migrate ensure-buckets   create BUCKET_RAW/CURATED/DATASETS/RUNS if missing

Each migration runs in its own transaction under a PostgreSQL advisory lock, so two copies of the job
cannot race. An applied file whose checksum changed is an error: write a new migration instead.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from llmops_common.config import env
from llmops_common.log import get_logger, kv, setup_logging

log = get_logger("db_migrate")

LOCK_ID = 727_101_031  # arbitrary, fixed advisory lock key
TABLE = "public.llmops_schema_migrations"


def migrations_dir() -> Path:
    return Path(env("MIGRATIONS_DIR", "/app/migrations"))


def load_migrations(directory: Path) -> list[tuple[str, str, str]]:
    files = sorted(p for p in directory.glob("*.sql") if p.is_file())
    out = []
    for path in files:
        sql = path.read_text(encoding="utf-8")
        out.append((path.stem, hashlib.sha256(sql.encode("utf-8")).hexdigest(), sql))
    return out


def _applied(conn) -> dict[str, str]:
    conn.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} (version text PRIMARY KEY, checksum text NOT NULL, "
                 "applied_at timestamptz NOT NULL DEFAULT now())")
    return {r["version"]: r["checksum"] for r in conn.execute(f"SELECT version, checksum FROM {TABLE}").fetchall()}


def migrate(conn, directory: Path) -> list[str]:
    """Apply pending migrations; returns the versions applied. `conn` must be autocommit."""
    applied_now: list[str] = []
    conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
    try:
        done = _applied(conn)
        for version, checksum, sql in load_migrations(directory):
            if version in done:
                if done[version] != checksum:
                    raise SystemExit(f"migration {version} changed after it was applied; add a new migration instead")
                continue
            with conn.transaction():
                conn.execute(sql)
                conn.execute(f"INSERT INTO {TABLE} (version, checksum) VALUES (%s, %s)", (version, checksum))
            applied_now.append(version)
            log.info("migration applied", extra=kv(version=version))
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
    return applied_now


def status(conn, directory: Path) -> list[tuple[str, str]]:
    done = _applied(conn)
    return [(v, "applied" if v in done else "pending") for v, _, _ in load_migrations(directory)]


def ensure_buckets() -> list[str]:
    from llmops_common.storage import BUCKET_KINDS, S3Store, bucket

    store = S3Store.from_env()
    names = [bucket(kind) for kind in BUCKET_KINDS]
    for name in names:
        store.ensure_bucket(name)
        log.info("bucket ready", extra=kv(bucket=name))
    return names


def main(argv: list[str] | None = None) -> int:
    setup_logging(os.environ.get("COMPONENT", "db-migrate"))
    args = list(sys.argv[1:] if argv is None else argv) or ["migrate"]
    cmd = args[0]
    if cmd == "ensure-buckets":
        ensure_buckets()
        return 0
    from llmops_common import db

    with db.connect(autocommit=True) as conn:
        if cmd == "migrate":
            applied = migrate(conn, migrations_dir())
            log.info("migrations up to date", extra=kv(applied=applied))
            return 0
        if cmd == "status":
            for version, state in status(conn, migrations_dir()):
                print(f"{version}\t{state}")
            return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
