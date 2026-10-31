"""Event handling for queue `test-orchestrator` (contracts/events.md).

    route.added               upsert the direct route (configured routes in ROUTES_FILE win), then plan the model.
                              Planning is delayed by ROUTE_PLAN_DELAY_SECONDS through an internal job on our own queue,
                              so the several route.added events of one new model end up in one plan.
    release.published         optional route sync: reload ROUTES_FILE and add the release's routes_added (no plan)
    gateway.version_changed   plan the runnable platform-layer items plus a smoke set (SMOKE_SEC_IDS at SMOKE_N x
                              SMOKE_TRIALS) on that gateway's routes
    test.job.completed        update the plan counters; finalise the run when every job has ended

Event-triggered plans record trigger_detail.source_key; a re-delivered event finds it and plans nothing twice.
"""
from __future__ import annotations

import threading
import time

from llmops_common import db as dbmod
from llmops_common.log import get_logger, kv
from llmops_common.queue import Job, PermanentError, enqueue

from . import catalog as cat
from .finalize import maybe_finalize
from .planner import PlanError, create_plan
from .routes import load_routes_file, route_from_event, upsert_routes
from .settings import Settings

log = get_logger("test_orchestrator")
QUEUE = "test-orchestrator"
PLAN_MODEL = "test-orchestrator.plan_model"  # internal job type (never published as an event)


class Orchestrator:
    def __init__(self, store, settings: Settings | None = None, catalog: dict | None = None, connection=None):
        self.store = store
        self.settings = settings or Settings.from_env()
        self.catalog = catalog or cat.load_catalog()
        self.connection = connection or dbmod.connection
        self._routes_cfg: dict | None = None
        self._lock = threading.Lock()

    # -- configuration ------------------------------------------------------------------------------
    @property
    def routes_config(self) -> dict:
        with self._lock:
            if self._routes_cfg is None:
                self._routes_cfg = load_routes_file(self.settings.routes_file)
            return self._routes_cfg

    def baseline_model(self) -> str | None:
        return self.settings.baseline_model_id or self.routes_config.get("baseline_model_id")

    def sync_routes(self, conn) -> int:
        """(Re)load ROUTES_FILE into tests.routes."""
        with self._lock:
            self._routes_cfg = load_routes_file(self.settings.routes_file)
            routes = self._routes_cfg["routes"]
        changed = upsert_routes(conn, routes, overwrite=True)
        log.info("routes synced", extra=kv(file=self.settings.routes_file, routes=len(routes), changed=changed))
        return changed

    # -- planning -------------------------------------------------------------------------------------
    def plan(self, conn, **kwargs) -> dict:
        result = create_plan(conn, self.store, self.catalog, self.settings, baseline_model_id=self.baseline_model(), **kwargs)
        result["status"] = "running"
        if result["jobs_total"] == 0:  # nothing to send: finish now so the run is not left open
            result["status"] = maybe_finalize(conn, self.store, self.catalog, result["plan_id"]) or "running"
        return result

    @staticmethod
    def _already_planned(conn, source_key: str) -> bool:
        return conn.execute("SELECT 1 FROM tests.plans WHERE trigger_detail->>'source_key' = %s LIMIT 1", (source_key,)).fetchone() is not None

    # -- queue handler --------------------------------------------------------------------------------
    def __call__(self, job: Job) -> None:
        self.handle(job)

    def handle(self, job: Job) -> None:
        handlers = {"route.added": self.on_route_added, "release.published": self.on_release_published,
                    "gateway.version_changed": self.on_gateway_version_changed, "test.job.completed": self.on_job_completed,
                    PLAN_MODEL: self.on_plan_model}
        handler = handlers.get(job.event or "")
        if handler is None:
            log.warning("job ignored: unknown event", extra=kv(job_id=job.id, event=job.event))
            return
        handler(job)

    def on_route_added(self, job: Job) -> None:
        data = job.data
        route = route_from_event(data, self.routes_config.get("provider_defaults") or {})
        model_id = data.get("model_id")
        with self.connection() as conn:
            if route:
                upsert_routes(conn, [route], overwrite=False)
            if model_id and self.settings.plan_on_route_added:
                delay = max(0.0, self.settings.route_plan_delay)
                window = int(time.time() // delay) if delay else f"e{job.payload.get('event_id')}"
                enqueue(conn, QUEUE, {"event": PLAN_MODEL, "payload": {"model_id": model_id, "event_id": job.payload.get("event_id"),
                                                                       "route": data}},
                        run_after_seconds=delay, dedupe_key=f"{PLAN_MODEL}:{model_id}:{window}")
        log.info("route added", extra=kv(model_id=model_id, route_id=route["id"] if route else None,
                                         enabled=route["enabled"] if route else None))

    def on_plan_model(self, job: Job) -> None:
        data = job.data
        model_id = data["model_id"]
        source_key = job.dedupe_key or f"job:{job.id}"
        with self.connection() as conn:
            if self._already_planned(conn, source_key):
                return
            delay = self.settings.route_plan_delay
            if delay > 0 and conn.execute(
                    "SELECT 1 FROM tests.plans WHERE model_id = %s AND trigger = 'route.added' "
                    "AND created_at > now() - make_interval(secs => %s) LIMIT 1", (model_id, float(delay))).fetchone():
                log.info("plan skipped: model planned moments ago", extra=kv(model_id=model_id))
                return
            try:
                self.plan(conn, trigger="route.added", model_id=model_id,
                          trigger_detail={"source_key": source_key, "event_id": data.get("event_id"), "route": data.get("route")})
            except PlanError as exc:
                if exc.status == 404:
                    log.warning("no enabled route to test", extra=kv(model_id=model_id))
                    return
                raise PermanentError(str(exc)) from exc

    def on_release_published(self, job: Job) -> None:
        if not self.settings.sync_routes_on_release:
            return
        added = 0
        with self.connection() as conn:
            self.sync_routes(conn)
            defaults = self.routes_config.get("provider_defaults") or {}
            for entry in (job.data.get("changes") or {}).get("routes_added") or []:
                if isinstance(entry, dict):
                    route = route_from_event(entry, defaults)
                    if route:
                        added += upsert_routes(conn, [route], overwrite=False)
        log.info("routes synced after release", extra=kv(release_id=job.data.get("release_id"), routes_added=added))

    def gateway_plan_items(self) -> tuple[list[str], dict]:
        items = self.catalog["items"]
        by_id = cat.items_by_id(self.catalog)
        platform = [i["sec_id"] for i in items if i.get("runnable") and i.get("layer") == "platform"]
        smoke = [s for s in self.settings.smoke_sec_ids if s in by_id and by_id[s].get("runnable") and s not in platform]
        overrides = {s: {"n": self.settings.smoke_n, "trials": self.settings.smoke_trials} for s in smoke}
        order = {i["sec_id"]: k for k, i in enumerate(items)}
        return sorted(platform + smoke, key=order.get), overrides

    def on_gateway_version_changed(self, job: Job) -> None:
        if not self.settings.plan_on_gateway_change:
            return
        data = job.data
        gateway = data.get("gateway") or self.settings.gateway_name
        source_key = f"evt:{job.payload.get('event_id', job.id)}"
        from .routes import list_routes

        with self.connection() as conn:
            if self._already_planned(conn, source_key):
                return
            routes = list_routes(conn, gateway=gateway)
            if not routes:
                log.warning("gateway changed but it has no enabled route", extra=kv(gateway=gateway))
                return
            sec_ids, overrides = self.gateway_plan_items()
            self.plan(conn, trigger="gateway.version_changed", trigger_detail={"source_key": source_key, **data}, routes=routes,
                      sec_ids=sec_ids, overrides=overrides, gateway_version=data.get("version"), purpose="regression")

    def on_job_completed(self, job: Job) -> None:
        plan_id = job.data.get("plan_id")
        if not plan_id:
            raise PermanentError("test.job.completed without plan_id")
        with self.connection() as conn:
            maybe_finalize(conn, self.store, self.catalog, plan_id)

    def reap(self) -> int:
        """Finalise running plans whose last jobs died without an event (checked periodically)."""
        finished = 0
        with self.connection() as conn:
            ids = [r["id"] for r in conn.execute("SELECT id FROM tests.plans WHERE status = 'running' "
                                                 "AND created_at < now() - interval '1 minute'").fetchall()]
        for plan_id in ids:
            with self.connection() as conn:
                if maybe_finalize(conn, self.store, self.catalog, str(plan_id)):
                    finished += 1
        return finished
