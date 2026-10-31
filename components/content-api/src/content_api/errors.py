"""One error shape for every response: {"error": {"code": ..., "message": ...}}."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

CODES = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
         406: "not_acceptable", 413: "too_large", 415: "unsupported_media_type", 422: "invalid_request",
         429: "rate_limited", 500: "internal", 502: "upstream_error", 503: "unavailable", 504: "timeout"}


def error_response(status: int, code: str, message: str, headers: dict | None = None, **extra) -> JSONResponse:
    body = {"error": {"code": code, "message": message}}
    body.update(extra)
    return JSONResponse(body, status_code=status, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        return error_response(exc.status_code, CODES.get(exc.status_code, "error"), str(exc.detail),
                              headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        fields = [{"loc": [str(p) for p in e.get("loc", ())], "msg": e.get("msg")} for e in exc.errors()][:20]
        return error_response(422, "invalid_request", "the request body is not valid", fields=fields)
