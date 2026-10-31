"""HTTP API for model access requests. Routes live under /api/requests (portal-web forwards /api/* here).

    POST /api/requests                         any signed-in user           submit (201)
    GET  /api/requests?mine=1                  any signed-in user           own requests
    GET  /api/requests[?status=&next_gate=]    reviewer-g2/g8, admin        all requests (keyset pagination)
    GET  /api/requests/{id}                    owner, reviewers, admin      detail with decisions and history
    POST /api/requests/{id}/decisions          reviewer of that gate, admin {gate, decision, comment}
    POST /api/requests/{id}/withdraw           owner                        in_review / changes_requested only
    POST /api/requests/{id}/resubmit           owner                        after changes were requested (patch)
    POST /api/requests/{id}/reprovision        admin                        retry key provisioning after `failed`
    GET    /api/subscriptions                  any signed-in user (own rows) models the user follows
    PUT    /api/subscriptions/{model_id}       any signed-in user            follow (201 new, 200 existing)
    DELETE /api/subscriptions/{model_id}       any signed-in user            unfollow (204, also when absent)
Every write is one transaction: request row + decision + audit rows + published events.
"""
from __future__ import annotations

import threading
import uuid
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import Body, Depends, FastAPI, HTTPException, Path, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from llmops_common import db
from llmops_common.auth import Authenticator, Principal
from llmops_common.fastapi_support import install_access_log, install_health, user_dependency
from llmops_common.log import get_logger, kv
from llmops_common.queue import Worker

from . import domain, events, store
from .settings import Settings
from .validation import RequestValidator, ValidationFailed, document_from_row
from .worker import QUEUE, KeyEventHandler

log = get_logger("request_api")
SYSTEM_ACTOR = "request-api"


# -- request bodies -------------------------------------------------------------------------------------
class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gate: Literal["G2", "G8", "G9"]
    decision: Literal["approve", "reject", "request_changes"]
    comment: str | None = Field(default=None, max_length=4000)


class WithdrawIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=1000)


class BodyLimitMiddleware:
    """413 for bodies over MAX_BODY_BYTES (checks Content-Length and counts streamed chunks)."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self.max_bytes <= 0:
            return await self.app(scope, receive, send)
        for name, value in scope.get("headers") or []:
            if name == b"content-length" and value.isdigit() and int(value) > self.max_bytes:
                return await JSONResponse({"detail": "request body too large"}, status_code=413)(scope, receive, send)
        seen = 0

        async def limited_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body") or b"")
                if seen > self.max_bytes:
                    raise HTTPException(status_code=413, detail="request body too large")
            return message

        return await self.app(scope, limited_receive, send)


# -- permissions ----------------------------------------------------------------------------------------
def _is_admin(p: Principal) -> bool:
    return Authenticator.has_role(p, "admin")


def _has_gate_role(p: Principal, gate: str) -> bool:
    return _is_admin(p) or Authenticator.has_role(p, domain.GATE_ROLE[gate])


def _is_reviewer(p: Principal) -> bool:
    return _is_admin(p) or any(Authenticator.has_role(p, r) for r in sorted(set(domain.GATE_ROLE.values())))


def _owns(p: Principal, row: dict) -> bool:
    return row["requester_sub"] == p.sub


def _can_view(p: Principal, row: dict) -> bool:
    return _owns(p, row) or _is_reviewer(p)


def _actor(p: Principal) -> str:
    return p.email or p.sub


def _default_alias(p: Principal) -> str:
    return (p.alias or p.sub)[:64]


# -- views ----------------------------------------------------------------------------------------------
def _iso(value):
    return value.isoformat() if value is not None else None


def _stage(row: dict) -> str | None:
    return domain.stage_of(row["gates"]) if row["status"] in ("provisioning", "trial", "active", "failed") else None


def result_view(row: dict) -> dict:
    return {"id": str(row["id"]), "ref": domain.ref(row["id"]), "status": row["status"], "gates": row["gates"],
            "next_gate": domain.next_gate(row["status"], row["gates"]), "stage": _stage(row)}


def summary_view(row: dict) -> dict:
    out = result_view(row)
    out.update({
        "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"]),
        "team": row["team"], "alias": row["alias"], "requester_email": row["requester_email"],
        "models": list(row["models"] or []), "unsure": row["unsure"], "data_class": row["data_class"],
        "data_class_other": row["data_class_other"], "cloud": row["cloud"], "rpd": row["rpd"],
        "tokens_in": row["tokens_in"], "tokens_out": row["tokens_out"], "target_date": _iso(row["target_date"]),
        "external": row["external"], "taiwan_only": row["taiwan_only"],
        "key_alias": row["key_alias"], "secret_ref": row["secret_ref"],
    })
    return out


class RequestApp:
    """Route handlers bound to one Settings / Authenticator / schema."""

    def __init__(self, settings: Settings, authenticator: Authenticator, validator: RequestValidator):
        self.settings = settings
        self.auth = authenticator
        self.validator = validator

    # -- helpers ------------------------------------------------------------------------------------------
    def _load_for_update(self, conn, request_id: uuid.UUID, user: Principal) -> dict:
        row = store.get_request(conn, request_id, lock=True)
        if row is None or not _can_view(user, row):
            raise HTTPException(404, "request not found")
        return row

    def _launch(self, conn, row: dict, stage: str, attempt: int = 1, action: str = "launch", actor: str = SYSTEM_ACTOR) -> dict:
        limits = domain.compute_limits(row, stage, self.settings.limits)
        store.add_audit(conn, row["id"], action, actor, {"stage": stage, "limits": limits, "attempt": attempt})
        events.approved(conn, row, stage, limits, attempt)
        log.info("request approved", extra=kv(request_id=str(row["id"]), stage=stage, budget_usd=limits["budget_usd"],
                                              rpm=limits["rpm"], tpm=limits["tpm"], attempt=attempt))
        return limits

    def _gates_and_status(self, fields: dict) -> tuple[list[dict], str, str | None]:
        gates = domain.build_gates(fields)
        if domain.pending_review_gate(gates) is None:           # nothing to review: launch at once
            gates, stage = domain.launch(gates)
            return gates, "provisioning", stage
        return gates, "in_review", None

    def permissions(self, user: Principal, row: dict) -> dict:
        own = _owns(user, row)
        gate = domain.next_gate(row["status"], row["gates"])
        may_decide = bool(gate) and _has_gate_role(user, gate) and (self.settings.allow_self_approval or not own)
        return {"withdraw": own and row["status"] in domain.OPEN_STATUSES,
                "resubmit": own and row["status"] == "changes_requested",
                "decide": [gate] if may_decide else [],
                "reprovision": _is_admin(user) and row["status"] == "failed"}

    def limits_view(self, conn, row: dict) -> dict | None:
        """Limits granted (provisioning/active/failed) or proposed for the next launch (open requests, trial)."""
        status = row["status"]
        if status in ("provisioning", "active", "failed"):
            granted = conn.execute(
                "SELECT detail FROM requests.audit WHERE request_id = %s AND action IN ('launch', 'reprovision') "
                "ORDER BY created_at DESC, id DESC LIMIT 1", (row["id"],)).fetchone()
            if granted:
                return {"stage": granted["detail"].get("stage"), "proposed": False, **granted["detail"].get("limits", {})}
        if status in domain.OPEN_STATUSES:
            stage = "trial" if "G8" in domain.gate_ids(row["gates"]) else "full"
        elif status == "trial":
            stage = "full"
        else:
            return None
        return {"stage": stage, "proposed": True, **domain.compute_limits(row, stage, self.settings.limits)}

    # -- handlers -----------------------------------------------------------------------------------------
    def submit(self, body: Any, user: Principal) -> JSONResponse:
        fields = self.validator.normalize(body)
        if not fields.get("alias"):
            fields["alias"] = _default_alias(user)
        gates, status, stage = self._gates_and_status(fields)
        request_id = uuid.uuid4()
        with db.connection() as conn:
            limit = self.settings.max_open_per_user
            if limit > 0 and store.count_open(conn, user.sub) >= limit:
                raise HTTPException(429, f"you already have {limit} open requests; withdraw or finish one first")
            row = store.insert_request(conn, request_id, {**fields, "requester_sub": user.sub,
                                                          "requester_email": user.email, "status": status, "gates": gates})
            store.add_audit(conn, request_id, "submit", _actor(user), {"status": status, "gates": domain.gate_ids(gates)})
            events.submitted(conn, row, revision=1)
            if stage:
                self._launch(conn, row, stage)
        log.info("request submitted", extra=kv(request_id=str(request_id), status=status, gates=domain.gate_ids(gates)))
        return JSONResponse(result_view(row), status_code=201, headers={"Location": f"/api/requests/{request_id}"})

    def list(self, user: Principal, mine: bool, status: str | None, next_gate: str | None, limit: int | None,
             cursor: str | None) -> dict:
        if not mine and not _is_reviewer(user):
            raise HTTPException(403, "reviewer or admin role required; use mine=1 for your own requests")
        statuses = [s.strip() for s in status.split(",") if s.strip()] if status else None
        unknown = [s for s in statuses or [] if s not in domain.STATUSES]
        if unknown:
            raise HTTPException(422, f"unknown status {unknown[0]!r}; one of {', '.join(domain.STATUSES)}")
        try:
            with db.connection() as conn:
                rows, next_cursor = store.list_requests(
                    conn, requester_sub=user.sub if mine else None, statuses=statuses, next_gate=next_gate,
                    limit=limit or self.settings.page_default, cursor=cursor)
        except store.BadCursor as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"items": [summary_view(r) for r in rows], "next_cursor": next_cursor}

    def get(self, request_id: uuid.UUID, user: Principal) -> dict:
        with db.connection() as conn:
            row = store.get_request(conn, request_id)
            if row is None or not _can_view(user, row):
                raise HTTPException(404, "request not found")
            decisions = store.decisions(conn, request_id)
            history = store.history(conn, request_id)
            key = store.latest_audit(conn, request_id, "key_provisioned") if row["key_alias"] else None
            limits = self.limits_view(conn, row)
        out = summary_view(row)
        out.update({
            "use_case": row["use_case"], "note": row["note"], "limits": limits,
            "key": ({"key_alias": row["key_alias"], "secret_ref": row["secret_ref"],
                     "stage": key["detail"].get("stage"), "expires_at": key["detail"].get("expires_at")} if key else None),
            "decisions": [{"id": d["id"], "gate": d["gate"], "decision": d["decision"], "comment": d["comment"],
                           "actor": d["actor_email"] or d["actor_sub"], "created_at": _iso(d["created_at"])} for d in decisions],
            "history": [{"action": h["action"], "actor": h["actor"], "at": _iso(h["created_at"]), "detail": h["detail"]}
                        for h in history],
            "permissions": self.permissions(user, row),
        })
        return out

    def decide(self, request_id: uuid.UUID, body: DecisionIn, user: Principal) -> dict:
        comment = (body.comment or "").strip() or None
        if body.decision in ("reject", "request_changes") and not comment:
            raise HTTPException(422, "a comment is required to reject or request changes")
        if body.gate == "G9" and body.decision == "request_changes":
            raise HTTPException(422, "the launch decision (G9) is approve or reject")
        with db.connection() as conn:
            row = self._load_for_update(conn, request_id, user)
            if not _has_gate_role(user, body.gate):
                raise HTTPException(403, f"role {domain.GATE_ROLE[body.gate]} or admin required for {body.gate}")
            if _owns(user, row) and not self.settings.allow_self_approval:
                raise HTTPException(403, "you cannot decide on your own request")
            expected = domain.next_gate(row["status"], row["gates"])
            if body.gate != expected:
                raise HTTPException(409, f"request is {row['status']}; waiting for {expected or 'no decision'}")
            gates, stage = row["gates"], None
            if body.decision == "approve":
                gates = domain.with_gate(gates, body.gate, "approved")
                if body.gate == "G9" or domain.pending_review_gate(gates) is None:
                    gates, stage = domain.launch(gates)
                    status = "provisioning"
                else:
                    status = "in_review"
            elif body.decision == "reject":
                gates, status = domain.with_gate(gates, body.gate, "rejected"), "rejected"
            else:
                status = "changes_requested"
            row = store.update_request(conn, request_id, status=status, gates=gates)
            decision = store.add_decision(conn, request_id, body.gate, body.decision, comment, user.sub, user.email)
            store.add_audit(conn, request_id, "decision", _actor(user),
                            {"gate": body.gate, "decision": body.decision, "status": status, "decision_id": decision["id"]})
            events.decided(conn, row, decision["id"], body.gate, body.decision, comment, _actor(user))
            if stage:
                self._launch(conn, row, stage)
        log.info("decision recorded", extra=kv(request_id=str(request_id), gate=body.gate, decision=body.decision,
                                               status=status))
        return {**result_view(row), "decision_id": decision["id"]}

    def withdraw(self, request_id: uuid.UUID, body: WithdrawIn | None, user: Principal) -> dict:
        with db.connection() as conn:
            row = self._load_for_update(conn, request_id, user)
            if not _owns(user, row):
                raise HTTPException(403, "only the requester can withdraw a request")
            if row["status"] not in domain.OPEN_STATUSES:
                raise HTTPException(409, f"request is {row['status']}; only open requests can be withdrawn")
            row = store.update_request(conn, request_id, status="withdrawn")
            store.add_audit(conn, request_id, "withdraw", _actor(user), {"reason": body.reason if body else None})
        log.info("request withdrawn", extra=kv(request_id=str(request_id)))
        return result_view(row)

    def resubmit(self, request_id: uuid.UUID, patch: Any, user: Principal) -> dict:
        patch = {} if patch is None else patch
        if not isinstance(patch, dict):
            raise ValidationFailed([{"loc": ["body"], "msg": "request body must be a JSON object", "type": "type"}])
        unknown = sorted(set(patch) - self.validator.properties)
        if unknown:
            raise ValidationFailed([{"loc": ["body", k], "msg": f"unknown field {k!r}", "type": "additionalProperties"}
                                    for k in unknown])
        with db.connection() as conn:
            row = self._load_for_update(conn, request_id, user)
            if not _owns(user, row):
                raise HTTPException(403, "only the requester can resubmit a request")
            if row["status"] != "changes_requested":
                raise HTTPException(409, f"request is {row['status']}; resubmit only after changes were requested")
            before = document_from_row(row)
            fields = self.validator.normalize({**before, **patch})
            if not fields.get("alias"):
                fields["alias"] = _default_alias(user)
            after = document_from_row(fields)
            changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
            gates, status, stage = self._gates_and_status(fields)
            row = store.update_request(conn, request_id, **{k: fields[k] for k in domain.FIELDS}, status=status, gates=gates)
            revision = store.count_audit(conn, request_id, ("submit", "resubmit")) + 1
            store.add_audit(conn, request_id, "resubmit", _actor(user), {"changed": changed, "status": status,
                                                                          "revision": revision})
            events.submitted(conn, row, revision=revision)
            if stage:
                self._launch(conn, row, stage)
        log.info("request resubmitted", extra=kv(request_id=str(request_id), revision=revision, changed=changed))
        return result_view(row)

    # -- subscriptions ------------------------------------------------------------------------------------
    def subscriptions(self, user: Principal) -> dict:
        with db.connection() as conn:
            rows = store.list_subscriptions(conn, user.sub)
        return {"items": [{"model_id": r["model_id"], "created_at": _iso(r["created_at"])} for r in rows]}

    def subscribe(self, model_id: str, user: Principal) -> JSONResponse:
        cap = self.settings.max_subscriptions_per_user
        try:
            with db.connection() as conn:
                row, created = store.subscribe(conn, user.sub, user.email, model_id, cap)
        except store.SubscriptionLimit:
            raise HTTPException(429, f"you already follow {cap} models; unfollow one first") from None
        if created:
            log.info("model followed", extra=kv(model_id=model_id))
        body = {"model_id": row["model_id"], "created_at": _iso(row["created_at"])}
        if created:
            return JSONResponse(body, status_code=201, headers={"Location": f"/api/subscriptions/{model_id}"})
        return JSONResponse(body, status_code=200)

    def unsubscribe(self, model_id: str, user: Principal) -> Response:
        with db.connection() as conn:
            removed = store.unsubscribe(conn, user.sub, model_id)
        if removed:
            log.info("model unfollowed", extra=kv(model_id=model_id))
        return Response(status_code=204)

    def reprovision(self, request_id: uuid.UUID, user: Principal) -> dict:
        if not _is_admin(user):
            raise HTTPException(403, "role admin required")
        with db.connection() as conn:
            row = self._load_for_update(conn, request_id, user)
            if row["status"] != "failed":
                raise HTTPException(409, f"request is {row['status']}; only failed provisioning can be retried")
            stage = domain.stage_of(row["gates"])
            attempt = store.count_audit(conn, request_id, ("reprovision",)) + 2
            row = store.update_request(conn, request_id, status="provisioning")
            self._launch(conn, row, stage, attempt=attempt, action="reprovision", actor=_actor(user))
        return result_view(row)


def create_app(settings: Settings | None = None, authenticator: Authenticator | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    authenticator = authenticator or Authenticator.from_env()
    validator = RequestValidator.load(settings.schema_path)
    handlers = RequestApp(settings, authenticator, validator)
    # catalog model id pattern, from the same schema as the request form (models[].pattern)
    model_id_pattern = validator.schema["properties"]["models"]["items"]["pattern"]
    current_user = user_dependency(authenticator)
    background: dict = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if settings.run_worker:
            worker = Worker(QUEUE, KeyEventHandler(), poll_seconds=settings.worker_poll_seconds)
            thread = threading.Thread(target=worker.run, name="request-api-worker", daemon=True)
            thread.start()
            background.update(worker=worker, thread=thread)
        try:
            yield
        finally:
            if background.get("worker"):
                background["worker"].stop()
                background["thread"].join(timeout=10)
            db.close_pool()

    app = FastAPI(title="request-api", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url="/api/requests/openapi.json")
    app.state.settings = settings
    app.state.current_user = current_user      # tests override this dependency to switch users
    app.state.handlers = handlers
    install_health(app, db.ping)
    # Added before the access log so it runs inside it: the access log (BaseHTTPMiddleware) reads `receive` in an
    # anyio task group, which would wrap our 413 in an ExceptionGroup that FastAPI turns into a 400.
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    install_access_log(app)

    @app.exception_handler(ValidationFailed)
    async def _invalid(_request, exc: ValidationFailed):
        return JSONResponse({"detail": exc.errors}, status_code=422)

    @app.post("/api/requests", status_code=201)
    def submit_request(body: Any = Body(...), user: Principal = Depends(current_user)):
        return handlers.submit(body, user)

    @app.get("/api/requests")
    def list_requests(mine: bool = False, status: str | None = Query(default=None, max_length=300),
                      next_gate: str | None = Query(default=None, pattern=r"^G(2|8|9)$"),
                      limit: int | None = Query(default=None, ge=1, le=settings.page_max),
                      cursor: str | None = Query(default=None, max_length=300),
                      user: Principal = Depends(current_user)):
        return handlers.list(user, mine, status, next_gate, limit, cursor)

    @app.get("/api/requests/{request_id}")
    def get_request(request_id: uuid.UUID, user: Principal = Depends(current_user)):
        return handlers.get(request_id, user)

    @app.post("/api/requests/{request_id}/decisions")
    def decide(request_id: uuid.UUID, body: DecisionIn, user: Principal = Depends(current_user)):
        return handlers.decide(request_id, body, user)

    @app.post("/api/requests/{request_id}/withdraw")
    def withdraw(request_id: uuid.UUID, body: WithdrawIn | None = None, user: Principal = Depends(current_user)):
        return handlers.withdraw(request_id, body, user)

    @app.post("/api/requests/{request_id}/resubmit")
    def resubmit(request_id: uuid.UUID, body: Any = Body(default=None), user: Principal = Depends(current_user)):
        return handlers.resubmit(request_id, body, user)

    @app.post("/api/requests/{request_id}/reprovision")
    def reprovision(request_id: uuid.UUID, user: Principal = Depends(current_user)):
        return handlers.reprovision(request_id, user)

    @app.get("/api/subscriptions")
    def list_subscriptions(user: Principal = Depends(current_user)):
        return handlers.subscriptions(user)

    @app.put("/api/subscriptions/{model_id}", status_code=201)
    def subscribe(model_id: str = Path(pattern=model_id_pattern), user: Principal = Depends(current_user)):
        return handlers.subscribe(model_id, user)

    @app.delete("/api/subscriptions/{model_id}", status_code=204)
    def unsubscribe(model_id: str = Path(pattern=model_id_pattern), user: Principal = Depends(current_user)):
        return handlers.unsubscribe(model_id, user)

    return app
