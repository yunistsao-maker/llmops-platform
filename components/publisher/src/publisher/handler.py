"""The publisher's work: ``research.proposal.ready`` in, a release (and events) out.

    research.proposal.ready {run_date, run_id, proposal_keys, roles}
        -> publish_batch: replay the proposals on the live release, apply the gates
        -> portal/releases/<id>/ (live) and/or a held release, portal/manifest.json
        -> release.published {release_id, previous_release_id, manifest_key, held, changes} per release made
        -> route.added {model_id, provider, region, model_name, status} per new route of a release that went live
        -> model.status_changed {model_id, model_name, from, to} per model whose platform status moved (live releases)

(On AWS the same steps were the Publish and Notify Lambda functions of a Step Functions state machine; the SNS mails are
replaced by these events - the notifier, view-builder and test-orchestrator subscribe to them.)

Events are published in one transaction after the pointer switched.  If the process dies between the two, the job is
retried: publish_batch recognises the event it already answered (its trigger is recorded in the pointer) and the same
releases are announced again - the dedupe keys keep every event to one.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from llmops_common.config import ConfigError, env, env_float, env_int
from llmops_common.log import kv
from llmops_common.queue import Job, PermanentError, RetryLater, publish

from . import publish as P
from . import releases as R
from .catalog_diff import route_event, status_event
from .lock import LockTimeout, publish_lock
from .storage import PrefixStore

log = logging.getLogger("publisher")

EVENT_IN = "research.proposal.ready"
SOURCE = "publisher"
PORTAL_PREFIX = "portal/"
CONTROL_PREFIX = "pipeline/"
MANIFEST_KEY = PORTAL_PREFIX + R.MANIFEST_KEY
MAX_PROPOSALS = 50
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}")


# ---------------------------------------------------------------------------------------------- settings
def policy_from_env() -> P.Policy:
    """PUBLISH_MODE (review | auto, default review), AUTO_MAX_OPS (60), AUTO_ALLOW_REMOVE (false).

    A setting this code does not understand stops the publisher: guessing would mean publishing automatically when
    somebody wrote "manual" and meant "review".
    """
    mode = (env("PUBLISH_MODE", "review") or "review").strip().lower()  # a deployment that does not say is not one that publishes unseen
    if mode not in ("auto", "review"):
        raise ConfigError(f"PUBLISH_MODE must be auto or review, got {mode!r}")
    allow_remove = (env("AUTO_ALLOW_REMOVE", "false") or "false").strip().lower()
    if allow_remove not in ("true", "false"):
        raise ConfigError(f"AUTO_ALLOW_REMOVE must be true or false, got {allow_remove!r}")
    max_ops = env_int("AUTO_MAX_OPS", 60)
    if max_ops < 1:
        raise ConfigError("AUTO_MAX_OPS must be at least 1")
    return P.Policy(mode=mode, max_ops=max_ops, allow_remove=allow_remove == "true")


@dataclass
class Settings:
    curated_bucket: str
    policy: P.Policy
    lock_timeout: float = 300.0

    @classmethod
    def from_env(cls) -> "Settings":
        from llmops_common.storage import bucket

        return cls(curated_bucket=bucket("curated"), policy=policy_from_env(), lock_timeout=env_float("PUBLISH_LOCK_TIMEOUT", 300.0))


# ---------------------------------------------------------------------------------------------- events
def release_payload(info: P.ReleaseInfo) -> dict:
    """release.published (contracts/events.md)."""
    changes = info.catalog_changes or {}
    return {"release_id": info.release_id, "previous_release_id": info.previous_release_id, "manifest_key": MANIFEST_KEY,
            "held": bool(info.held),
            "changes": {k: list(changes.get(k) or []) for k in ("routes_added", "prices_changed", "retirements")}}


def emit(conn, releases: Iterable[P.ReleaseInfo]) -> list[int]:
    """Publish release.published for each release, and route.added for each new route and model.status_changed for
    each model whose status moved in a live one (all in ``conn``'s transaction).  Returns the ids of the new events."""
    ids: list[int] = []
    for info in releases:
        again = f"@{info.sequence}" if info.repeat_of_live else ""  # a release made live once more (rollback) is news again
        event_id = publish(conn, "release.published", release_payload(info), source=SOURCE,
                           dedupe_key=f"release.published:{info.release_id}{again}")
        if event_id:
            ids.append(event_id)
        if info.held:
            continue  # a held release is not live: its routes are announced when it is approved
        for route in (info.catalog_changes or {}).get("routes_added") or []:
            event_id = publish(conn, "route.added", route_event(route), source=SOURCE,
                               dedupe_key=f"route.added:{info.release_id}{again}:{route['route_id']}")
            if event_id:
                ids.append(event_id)
        for change in (info.catalog_changes or {}).get("status_changes") or []:
            event_id = publish(conn, "model.status_changed", status_event(change), source=SOURCE,
                               dedupe_key=f"model.status_changed:{change['model_id']}:{change['to']}:{info.release_id}{again}")
            if event_id:
                ids.append(event_id)
    return ids


# ---------------------------------------------------------------------------------------------- the worker's handler
class Publisher:
    def __init__(self, store: Any = None, *, settings: Settings | None = None, lock: Callable[[], Any] | None = None,
                 connection: Callable[[], Any] | None = None, clock: Callable[[], dt.datetime] | None = None):
        from llmops_common import db
        from llmops_common.storage import S3Store

        self.settings = settings or Settings.from_env()
        self.store = store if store is not None else S3Store.from_env()
        bucket = self.settings.curated_bucket
        self.portal = PrefixStore(self.store, bucket, PORTAL_PREFIX)
        self.control = PrefixStore(self.store, bucket, CONTROL_PREFIX)
        self.research = PrefixStore(self.store, bucket, "")
        self.lock = lock or (lambda: publish_lock(self.settings.lock_timeout))
        self.connection = connection or db.connection
        self.clock = clock or R.utc_now

    # --- readiness: the database and the curated bucket answer
    def ready(self) -> None:
        from llmops_common import db

        db.ping()
        self.store.ping(self.settings.curated_bucket)

    @staticmethod
    def read_event(job: Job) -> dict:
        if job.event != EVENT_IN:
            raise PermanentError(f"the publisher handles {EVENT_IN}, not {job.event!r}")
        data = job.data
        keys = data.get("proposal_keys") if isinstance(data, dict) else None
        if not isinstance(keys, list) or len(keys) > MAX_PROPOSALS or not all(isinstance(k, str) for k in keys):
            raise PermanentError(f"proposal_keys must be a list of at most {MAX_PROPOSALS} keys")
        run_id = data.get("run_id")
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise PermanentError("run_id is missing or malformed")
        return data

    def items(self, keys: list[str]) -> list[tuple[str, Any]]:
        out: list[tuple[str, Any]] = []
        for key in dict.fromkeys(keys):  # (order kept, duplicates once)
            try:
                out.append((key, P.read_research(self.research, key)))
            except R.ReleaseError as e:
                out.append((key, P.Unreadable(f"提案檔案讀不到：{e}")))
        return out

    def handle(self, job: Job) -> P.BatchOutcome:
        data = self.read_event(job)
        event_id = job.payload.get("event_id") if isinstance(job.payload, dict) else None
        trigger = f"event:{event_id}" if event_id else f"job:{job.id}"
        items = self.items(data["proposal_keys"])
        run = {"run_id": data.get("run_id"), "run_date": data.get("run_date")}
        try:
            with self.lock():
                outcome = P.publish_batch(self.portal, items, policy=self.settings.policy, control=self.control, actor=SOURCE,
                                          now=self.clock(), trigger=trigger, run=run)
        except R.NotBootstrapped as e:
            log.error("no live release yet: bootstrap the portal data first", extra=kv(error=str(e)))
            raise RetryLater(str(e), delay=600) from e
        except LockTimeout as e:
            raise RetryLater(str(e), delay=60) from e
        level = logging.WARNING if any(i.status == "rejected" for i in outcome.items) else logging.INFO
        log.log(level, "proposals replayed", extra=kv(run_id=run["run_id"], trigger=trigger, **outcome.summary()))
        for warning in outcome.warnings:
            log.warning("publish warning", extra=kv(run_id=run["run_id"], warning=warning))
        self.announce(outcome.releases)
        return outcome

    def announce(self, releases: Iterable[P.ReleaseInfo]) -> list[int]:
        releases = list(releases)
        if not releases:
            return []
        with self.connection() as conn:
            ids = emit(conn, releases)
        for info in releases:
            log.info("release announced", extra=kv(release_id=info.release_id, previous_release_id=info.previous_release_id, held=info.held,
                                                   routes_added=len(info.catalog_changes.get("routes_added") or []),
                                                   prices_changed=len(info.catalog_changes.get("prices_changed") or []),
                                                   retirements=len(info.catalog_changes.get("retirements") or []),
                                                   status_changes=len(info.catalog_changes.get("status_changes") or [])))
        return ids
