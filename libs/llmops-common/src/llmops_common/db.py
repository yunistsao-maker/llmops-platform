"""PostgreSQL access (psycopg 3). Settings: DATABASE_URL (or DATABASE_URL_FILE), DB_POOL_MAX (default 5).

Rows come back as dicts. `connection()` gives a pooled connection inside a transaction that commits on
success and rolls back on error.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from .config import env, env_int

__all__ = ["database_url", "connection", "connect", "ping", "close_pool"]

_pool = None
_pool_lock = threading.Lock()


def database_url() -> str:
    return env("DATABASE_URL")


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                from psycopg.rows import dict_row
                from psycopg_pool import ConnectionPool

                _pool = ConnectionPool(
                    conninfo=database_url(),
                    min_size=1,
                    max_size=env_int("DB_POOL_MAX", 5),
                    kwargs={"row_factory": dict_row},
                    open=True,
                    timeout=10,
                )
    return _pool


@contextmanager
def connection() -> Iterator["object"]:
    """Pooled connection; the block is one transaction (commit on success, rollback on exception)."""
    with _get_pool().connection() as conn:
        yield conn


def connect(autocommit: bool = False):
    """A dedicated (non-pooled) connection, e.g. for migrations or long-lived listeners."""
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(database_url(), row_factory=dict_row, autocommit=autocommit)


def ping() -> None:
    with connection() as conn:
        conn.execute("SELECT 1")


def close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None
