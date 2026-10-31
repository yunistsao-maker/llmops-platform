"""FastAPI helpers shared by the HTTP services: health routes, auth dependencies, access log, uvicorn runner.

Only services import this module (FastAPI is not a dependency of workers).
"""
from __future__ import annotations

import time
import uuid
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .auth import AuthError, Authenticator, Principal
from .config import env_int
from .log import get_logger, kv

__all__ = ["install_health", "install_access_log", "user_dependency", "role_dependency", "run"]

log = get_logger("http")


def install_health(app: FastAPI, ready_check: Callable[[], None] | None = None) -> None:
    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz():
        try:
            if ready_check:
                ready_check()
        except Exception as exc:  # report, never crash the probe
            return JSONResponse({"status": "not-ready", "error": type(exc).__name__}, status_code=503)
        return {"status": "ready"}


def install_access_log(app: FastAPI) -> None:
    @app.middleware("http")
    async def access_log(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("request failed", extra=kv(rid=rid, method=request.method, path=request.url.path))
            raise
        if request.url.path not in ("/healthz", "/readyz"):
            log.info("request", extra=kv(rid=rid, method=request.method, path=request.url.path,
                                         status=response.status_code, ms=int((time.monotonic() - started) * 1000)))
        response.headers["x-request-id"] = rid
        return response


def user_dependency(authenticator: Authenticator):
    def current_user(request: Request) -> Principal:
        try:
            return authenticator.authenticate(request.headers.get("authorization"))
        except AuthError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    return current_user


def role_dependency(authenticator: Authenticator, role: str):
    current_user = user_dependency(authenticator)

    def require(principal: Principal = Depends(current_user)) -> Principal:
        if not (authenticator.has_role(principal, role) or authenticator.has_role(principal, "admin")):
            raise HTTPException(status_code=403, detail=f"role {role} required")
        return principal

    return require


def run(app_path: str) -> None:
    """Start uvicorn on PORT (default 8080) with our JSON logging left in place."""
    import uvicorn

    uvicorn.run(app_path, host="0.0.0.0", port=env_int("PORT", 8080), log_config=None,  # noqa: S104
                access_log=False, proxy_headers=True, forwarded_allow_ips="*", timeout_graceful_shutdown=25)
