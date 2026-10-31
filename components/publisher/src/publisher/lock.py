"""One publisher at a time: a PostgreSQL advisory lock around everything that writes portal/ (and pipeline/).

On AWS the pipeline made concurrent publishes safe with S3 conditional writes.  Self-hosted S3-compatible stores do not
all support those, so on the platform the publishers (worker replicas and the operator commands) take this lock instead;
the conditional writes in publisher.storage are then plain compare-and-write under it.  The lock is held on a dedicated
connection and is released when the block ends - or by PostgreSQL when the process dies.
"""

from __future__ import annotations

import contextlib
import threading
import time
from typing import Callable, Iterator

LOCK_ID = 727_101_032  # db-migrate uses 727_101_031


class LockTimeout(Exception):
    """Another publisher held the lock for longer than we were willing to wait."""


@contextlib.contextmanager
def publish_lock(timeout: float = 300.0, *, stop: threading.Event | None = None, connect: Callable | None = None,
                 poll: float = 1.0) -> Iterator[None]:
    from llmops_common import db

    conn = (connect or (lambda: db.connect(autocommit=True)))()
    try:
        deadline = time.monotonic() + timeout
        while not conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (LOCK_ID,)).fetchone()["ok"]:
            if time.monotonic() > deadline or (stop is not None and stop.is_set()):
                raise LockTimeout(f"another publisher holds the publish lock (waited {timeout:.0f}s)")
            time.sleep(poll)
        try:
            yield
        finally:
            try:
                conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
            except Exception:  # noqa: BLE001 - closing the connection releases it as well
                pass
    finally:
        conn.close()


@contextlib.contextmanager
def no_lock() -> Iterator[None]:
    """For tests and single-process tools that know nobody else publishes."""
    yield
