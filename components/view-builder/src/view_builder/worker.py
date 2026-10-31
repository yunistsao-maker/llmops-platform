"""Queue worker: rebuild the view on release.published, security.results.published and editorial.updated."""
from __future__ import annotations

import contextlib
import datetime as dt
import threading
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from llmops_common.config import ConfigError, env, env_float, env_int
from llmops_common.log import get_logger, kv
from llmops_common.queue import Job, PermanentError

from .compile import BuildError
from .store_io import BuildResult, NoRelease, build_and_publish

log = get_logger("view_builder.worker")

QUEUE = "view-builder"
HANDLED = frozenset({"release.published", "security.results.published", "editorial.updated"})
LOCK_ID = 727_101_041  # advisory lock: one build at a time across replicas (db-migrate uses 727_101_031)


def portal_tz() -> ZoneInfo:
    name = env("PORTAL_TZ", "Asia/Taipei")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"PORTAL_TZ={name!r} is not a known time zone") from exc


class Builder:
    """Serialised builds. With a connection factory the lock is a PostgreSQL advisory lock (all replicas)."""

    def __init__(self, store, bucket: str, *, tz: ZoneInfo, news_window_days: int = 30, keep_views: int = 30,
                 connection_factory=None):
        self.store = store
        self.bucket = bucket
        self.tz = tz
        self.news_window_days = news_window_days
        self.keep_views = keep_views
        self.connection = connection_factory
        self._local = threading.Lock()

    @classmethod
    def from_env(cls, store, bucket: str, connection_factory=None) -> "Builder":
        return cls(store, bucket, tz=portal_tz(), news_window_days=env_int("NEWS_WINDOW_DAYS", 30),
                   keep_views=env_int("KEEP_VIEWS", 30), connection_factory=connection_factory)

    def today(self) -> dt.date:
        return dt.datetime.now(self.tz).date()

    @contextlib.contextmanager
    def _serialised(self):
        with self._local:
            if self.connection is None:
                yield
                return
            with self.connection() as conn:
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
                yield  # the lock is released when this transaction ends

    def build(self, reason: str) -> BuildResult | None:
        with self._serialised():
            try:
                return build_and_publish(self.store, self.bucket, today=self.today(),
                                         news_window_days=self.news_window_days, keep_views=self.keep_views)
            except NoRelease as exc:
                log.warning("nothing to build yet", extra=kv(reason=reason, error=str(exc)))
                return None

    def handle(self, job: Job) -> None:
        if job.event not in HANDLED:
            raise PermanentError(f"view-builder does not handle {job.event!r}")
        data = job.data or {}
        log.info("event received", extra=kv(event=job.event, event_id=job.payload.get("event_id"),
                                            release_id=data.get("release_id"), run_id=data.get("run_id"),
                                            held=data.get("held")))
        try:
            self.build(job.event)
        except (BuildError, ValueError) as exc:  # malformed inputs: the same state never builds; a new event will
            raise PermanentError(str(exc)) from exc


def start_periodic(builder: Builder, interval: float, stop: threading.Event) -> threading.Thread | None:
    """Rebuild every `interval` seconds so date-relative fields (news window, built) stay current.

    A rebuild whose content did not change writes nothing.
    """
    if interval <= 0:
        return None

    def loop():
        while not stop.wait(interval):
            try:
                builder.build("periodic")
            except Exception:  # never let the timer die
                log.exception("periodic rebuild failed")

    t = threading.Thread(target=loop, name="periodic-rebuild", daemon=True)
    t.start()
    return t


def run_worker(store, bucket: str) -> None:
    from llmops_common import db
    from llmops_common.queue import Worker

    builder = Builder.from_env(store, bucket, connection_factory=db.connection)

    def ready():
        db.ping()
        store.ping(bucket)

    stop = threading.Event()
    start_periodic(builder, env_float("REBUILD_INTERVAL_SECONDS", 3600.0), stop)
    worker = Worker(QUEUE, builder.handle, poll_seconds=env_float("POLL_SECONDS", 2.0),
                    health_port=env_int("HEALTH_PORT", 8081), ready_check=ready)
    try:
        worker.run()
    finally:
        stop.set()
        db.close_pool()
