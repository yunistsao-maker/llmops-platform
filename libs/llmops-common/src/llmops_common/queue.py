"""Events and work queues in PostgreSQL (schema `platform`, see contracts/db/migrations/0001_platform.sql).

publish(conn, type, payload)  writes one row to platform.events and, for every subscriber in SUBSCRIPTIONS,
                              one job to platform.jobs (queue = the subscriber's component name).
Worker(queue, handler)        claims jobs with FOR UPDATE SKIP LOCKED, retries with backoff, parks a job as
                              'dead' after max_attempts (or at once on PermanentError).

The subscription table is part of the contract (contracts/events.md). Change both together.
"""
from __future__ import annotations

import os
import signal
import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .log import get_logger, kv

__all__ = [
    "SUBSCRIPTIONS", "EVENT_TYPES", "Job", "PermanentError", "RetryLater",
    "publish", "enqueue", "claim", "complete", "fail", "Worker", "backoff_seconds",
]

log = get_logger(__name__)

# event type -> queues (component names) that receive a job for it
SUBSCRIPTIONS: dict[str, tuple[str, ...]] = {
    "research.proposal.ready": ("publisher",),
    "release.published": ("view-builder", "test-orchestrator", "notifier"),
    "route.added": ("test-orchestrator",),
    "gateway.version_changed": ("test-orchestrator", "notifier"),
    "test.job.completed": ("test-orchestrator",),
    "test.run.completed": ("results-publisher", "notifier"),
    "security.results.published": ("view-builder",),
    "editorial.updated": ("view-builder",),
    "request.submitted": ("notifier",),
    "request.decided": ("notifier",),
    "request.approved": ("key-provisioner", "notifier"),
    "key.provisioned": ("request-api", "notifier"),
    "key.failed": ("request-api", "notifier"),
    "model.status_changed": ("notifier",),
}
EVENT_TYPES = frozenset(SUBSCRIPTIONS)


class PermanentError(Exception):
    """The job can never succeed; park it as dead without retrying."""


class RetryLater(Exception):
    """Retry after `delay` seconds without counting it as a handler bug."""

    def __init__(self, message: str = "", delay: float | None = None):
        super().__init__(message)
        self.delay = delay


@dataclass
class Job:
    id: int
    queue: str
    payload: dict
    attempts: int
    max_attempts: int
    dedupe_key: str | None = None

    @property
    def event(self) -> str | None:
        return self.payload.get("event")

    @property
    def data(self) -> dict:
        """The event payload when the job came from publish(); otherwise the raw job payload."""
        return self.payload.get("payload", self.payload) if "event" in self.payload else self.payload


def _jsonb(value):
    from psycopg.types.json import Jsonb

    return Jsonb(value)


def publish(conn, event_type: str, payload: dict, *, source: str, dedupe_key: str | None = None) -> int | None:
    """Record an event and fan it out to subscriber queues in the caller's transaction.

    Returns the event id, or None when dedupe_key was already used (the event was published before).
    """
    if event_type not in SUBSCRIPTIONS:
        raise ValueError(f"unknown event type {event_type!r}; add it to SUBSCRIPTIONS and contracts/events.md")
    row = conn.execute(
        "INSERT INTO platform.events (type, payload, source, dedupe_key) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (dedupe_key) DO NOTHING RETURNING id",
        (event_type, _jsonb(payload), source, dedupe_key),
    ).fetchone()
    if row is None:
        return None
    event_id = row["id"]
    for queue in SUBSCRIPTIONS[event_type]:
        enqueue(conn, queue, {"event": event_type, "event_id": event_id, "payload": payload},
                dedupe_key=f"evt:{event_id}:{queue}")
    return event_id


def enqueue(conn, queue: str, payload: dict, *, run_after_seconds: float = 0, dedupe_key: str | None = None,
            max_attempts: int = 5) -> int | None:
    row = conn.execute(
        "INSERT INTO platform.jobs (queue, payload, run_after, dedupe_key, max_attempts) "
        "VALUES (%s, %s, now() + make_interval(secs => %s), %s, %s) "
        "ON CONFLICT (dedupe_key) DO NOTHING RETURNING id",
        (queue, _jsonb(payload), float(run_after_seconds), dedupe_key, max_attempts),
    ).fetchone()
    return row["id"] if row else None


def claim(conn, queue: str, worker_id: str, lease_seconds: int = 900) -> Job | None:
    """Take the oldest runnable job. A 'running' job whose lease expired (crashed worker) is taken again."""
    row = conn.execute(
        """
        UPDATE platform.jobs SET status = 'running', locked_by = %(w)s, locked_at = now(), attempts = attempts + 1
        WHERE id = (
            SELECT id FROM platform.jobs
            WHERE queue = %(q)s AND (
                (status = 'queued' AND run_after <= now())
                OR (status = 'running' AND locked_at < now() - make_interval(secs => %(lease)s))
            )
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        RETURNING id, queue, payload, attempts, max_attempts, dedupe_key
        """,
        {"w": worker_id, "q": queue, "lease": lease_seconds},
    ).fetchone()
    if not row:
        return None
    return Job(id=row["id"], queue=row["queue"], payload=row["payload"], attempts=row["attempts"],
               max_attempts=row["max_attempts"], dedupe_key=row["dedupe_key"])


def complete(conn, job_id: int) -> None:
    conn.execute(
        "UPDATE platform.jobs SET status = 'done', finished_at = now(), locked_by = NULL, last_error = NULL WHERE id = %s",
        (job_id,),
    )


def backoff_seconds(attempts: int) -> float:
    return float(min(3600, 10 * (2 ** max(0, attempts - 1))))


def fail(conn, job: Job, error: str, *, retry: bool = True, delay: float | None = None) -> str:
    """Record a failure. Returns the new status ('queued' for a retry or 'dead')."""
    status = "queued" if retry and job.attempts < job.max_attempts else "dead"
    wait = backoff_seconds(job.attempts) if delay is None else delay
    conn.execute(
        "UPDATE platform.jobs SET status = %s, locked_by = NULL, last_error = %s, "
        "run_after = now() + make_interval(secs => %s), finished_at = CASE WHEN %s = 'dead' THEN now() END "
        "WHERE id = %s",
        (status, error[:4000], wait, status, job.id),
    )
    return status


class Worker:
    """Poll one queue and run `handler(job)` for each job.

    The handler does its own work (and its own transactions); the worker marks the job done or failed.
    Stops cleanly on SIGTERM/SIGINT after the current job.
    """

    def __init__(self, queue: str, handler: Callable[[Job], None], *, connection_factory=None,
                 worker_id: str | None = None, poll_seconds: float = 2.0, lease_seconds: int = 900,
                 health_port: int | None = None, ready_check: Callable[[], None] | None = None):
        from . import db

        self.queue = queue
        self.handler = handler
        self.connection = connection_factory or db.connection
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds
        self.health_port = health_port
        self.ready_check = ready_check
        self._stop = threading.Event()

    def stop(self, *_):
        self._stop.set()

    def run_once(self) -> bool:
        """Process at most one job. Returns True when a job was processed."""
        with self.connection() as conn:
            job = claim(conn, self.queue, self.worker_id, self.lease_seconds)
        if job is None:
            return False
        started = time.monotonic()
        try:
            self.handler(job)
        except PermanentError as exc:
            with self.connection() as conn:
                fail(conn, job, f"permanent: {exc}", retry=False)
            log.error("job failed permanently", extra=kv(job_id=job.id, queue=self.queue, error=str(exc)))
        except RetryLater as exc:
            with self.connection() as conn:
                status = fail(conn, job, f"retry: {exc}", retry=True, delay=exc.delay)
            log.warning("job deferred", extra=kv(job_id=job.id, queue=self.queue, status=status, error=str(exc)))
        except Exception as exc:  # any other error: retry with backoff
            with self.connection() as conn:
                status = fail(conn, job, f"{type(exc).__name__}: {exc}")
            log.exception("job failed", extra=kv(job_id=job.id, queue=self.queue, status=status, attempts=job.attempts))
        else:
            with self.connection() as conn:
                complete(conn, job.id)
            log.info("job done", extra=kv(job_id=job.id, queue=self.queue, event=job.event,
                                          ms=int((time.monotonic() - started) * 1000)))
        return True

    def run(self) -> None:
        if self.health_port:
            from .health import start_health_server

            start_health_server(self.health_port, self.ready_check)
        try:
            signal.signal(signal.SIGTERM, self.stop)
            signal.signal(signal.SIGINT, self.stop)
        except ValueError:  # not in the main thread (tests)
            pass
        log.info("worker started", extra=kv(queue=self.queue, worker_id=self.worker_id))
        while not self._stop.is_set():
            try:
                busy = self.run_once()
            except Exception:  # database hiccup: wait and retry
                log.exception("worker loop error", extra=kv(queue=self.queue))
                busy = False
            if not busy:
                self._stop.wait(self.poll_seconds)
        log.info("worker stopped", extra=kv(queue=self.queue))
