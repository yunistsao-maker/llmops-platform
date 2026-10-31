"""HTTP API (admin role) plus the in-process background work.

    POST /api/tests/plans          {model_id?, route_ids?, sec_ids?, trigger: manual|schedule, purpose?} -> 201 plan
    GET  /api/tests/plans/{id}     plan row with verdict counts
    GET  /api/tests/runs/{run_id}  plan row with every tests.results row of the run
    GET  /api/tests/routes         every route (enabled or not)
    GET  /healthz, /readyz         probes (readyz: database reachable)

One process: uvicorn serves the API on PORT; the lifespan starts a Worker thread on queue `test-orchestrator` and the
gateway watcher thread, and stops both (finishing the current job) on SIGTERM.
"""
from __future__ import annotations

import threading
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from llmops_common.auth import Authenticator, Principal
from llmops_common.fastapi_support import install_access_log, install_health, role_dependency
from llmops_common.log import get_logger, kv
from llmops_common.queue import Worker

from .planner import PlanError
from .routes import list_routes
from .service import QUEUE, Orchestrator
from .settings import Settings
from .watcher import Watcher

log = get_logger("test_orchestrator.api")


class PlanRequest(BaseModel):
    model_id: str | None = Field(default=None, max_length=200)
    route_ids: list[str] | None = Field(default=None, max_length=50)
    sec_ids: list[str] | None = Field(default=None, max_length=40)
    trigger: Literal["manual", "schedule"] = "manual"
    purpose: Literal["pre-release", "regression", "adhoc"] | None = None


class Background:
    def __init__(self, orch: Orchestrator):
        self.orch = orch
        self.worker = Worker(QUEUE, orch, connection_factory=orch.connection, poll_seconds=orch.settings.poll_seconds)
        self.watcher = Watcher(orch)
        self.thread = threading.Thread(target=self.worker.run, name="worker", daemon=True)

    def start(self) -> None:
        self.thread.start()
        self.watcher.start()

    def stop(self, timeout: float = 20.0) -> None:
        self.worker.stop()
        self.watcher.stop()
        self.thread.join(timeout)
        self.watcher.join(2.0)


def create_app(orch: Orchestrator | None = None, authenticator: Authenticator | None = None,
               background: bool | None = None, store_factory=None) -> FastAPI:
    authenticator = authenticator or Authenticator.from_env()
    holder: dict = {"orch": orch}

    def orchestrator() -> Orchestrator:
        if holder["orch"] is None:
            from llmops_common.storage import S3Store

            holder["orch"] = Orchestrator((store_factory or S3Store.from_env)(), Settings.from_env())
        return holder["orch"]

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        o = orchestrator()
        try:
            with o.connection() as conn:
                o.sync_routes(conn)
        except Exception:  # noqa: BLE001 - serve anyway; /readyz reports the database
            log.exception("route sync at startup failed")
        bg = None
        if o.settings.background if background is None else background:
            bg = Background(o)
            bg.start()
            log.info("background started", extra=kv(queue=QUEUE))
        try:
            yield
        finally:
            if bg:
                bg.stop()

    app = FastAPI(title="test-orchestrator", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    install_access_log(app)

    def ready() -> None:
        with orchestrator().connection() as conn:
            conn.execute("SELECT 1")

    install_health(app, ready)
    admin = role_dependency(authenticator, "admin")

    @app.post("/api/tests/plans", status_code=201)
    def create_plan(req: PlanRequest, principal: Principal = Depends(admin)):
        o = orchestrator()
        try:
            with o.connection() as conn:
                result = o.plan(conn, trigger=req.trigger, model_id=req.model_id, route_ids=req.route_ids, sec_ids=req.sec_ids,
                                purpose=req.purpose, trigger_detail={"requested_by": principal.alias})
        except PlanError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        log.info("plan requested", extra=kv(plan_id=result["plan_id"], run_id=result["run_id"], by=principal.alias))
        return result

    def _plan_view(conn, plan: dict) -> dict:
        counts = conn.execute("SELECT verdict, count(*) AS n FROM tests.results WHERE run_id = %s GROUP BY verdict",
                              (plan["run_id"],)).fetchall()
        return {**plan, "id": str(plan["id"]), "verdicts": {r["verdict"]: r["n"] for r in counts}}

    @app.get("/api/tests/plans/{plan_id}")
    def get_plan(plan_id: uuid.UUID, principal: Principal = Depends(admin)):
        with orchestrator().connection() as conn:
            plan = conn.execute("SELECT * FROM tests.plans WHERE id = %s", (str(plan_id),)).fetchone()
            if not plan:
                raise HTTPException(status_code=404, detail="plan not found")
            return _plan_view(conn, plan)

    @app.get("/api/tests/runs/{run_id}")
    def get_run(run_id: str, principal: Principal = Depends(admin)):
        with orchestrator().connection() as conn:
            plan = conn.execute("SELECT * FROM tests.plans WHERE run_id = %s", (run_id,)).fetchone()
            if not plan:
                raise HTTPException(status_code=404, detail="run not found")
            results = conn.execute("SELECT * FROM tests.results WHERE run_id = %s ORDER BY sec_id, route_id", (run_id,)).fetchall()
            return {"plan": _plan_view(conn, plan), "results": list(results)}

    @app.get("/api/tests/routes")
    def get_routes(principal: Principal = Depends(admin)):
        with orchestrator().connection() as conn:
            return {"routes": list_routes(conn, enabled_only=False)}

    app.state.orchestrator = orchestrator
    return app
