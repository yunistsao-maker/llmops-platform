"""content-api routes.

GET /api/content/portal.json                 current view (pointer cached POINTER_TTL_SECONDS); ETag = view id,
                                             If-None-Match -> 304, Cache-Control: no-cache
GET /api/content/views/{view_id}/portal.json one immutable view, long cache
GET /api/content/manifest                    release manifest + current view pointer
All three need a signed-in user (llmops_common.auth.Authenticator). Read-only: nothing is ever written.
"""
from __future__ import annotations

import gzip
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from llmops_common.auth import Authenticator, Principal
from llmops_common.config import env_float, env_int
from llmops_common.fastapi_support import install_access_log, install_health, user_dependency
from llmops_common.log import get_logger, kv
from llmops_common.storage import NotFound

from .errors import error_response, install_error_handlers

log = get_logger("content_api")

VIEW_POINTER = "portal/views/current.json"
MANIFEST = "portal/manifest.json"
VIEW_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
JSON_TYPE = "application/json; charset=utf-8"
CC_CURRENT = "no-cache"
CC_IMMUTABLE = "private, max-age=31536000, immutable"


def view_key(view_id: str) -> str:
    return f"portal/views/{view_id}/portal.json"


@dataclass(frozen=True)
class Body:
    raw: bytes
    gz: bytes


class Unavailable(Exception):
    """The store cannot be reached and nothing usable is cached."""


class ContentSource:
    """Pointer + manifest cached for `ttl` seconds (stale copies are served while the store is down);
    view bodies are immutable, so the last few are kept in memory together with their gzip form."""

    def __init__(self, store, bucket: str, *, ttl: float = 30.0, max_views: int = 4, clock=time.monotonic):
        self.store, self.bucket, self.ttl, self.max_views, self.clock = store, bucket, ttl, max_views, clock
        self._small: dict[str, tuple[float, object]] = {}
        self._views: OrderedDict[str, Body] = OrderedDict()
        self._lock = threading.Lock()

    def _cached_json(self, key: str):
        now = self.clock()
        with self._lock:
            hit = self._small.get(key)
        if hit and now - hit[0] < self.ttl:
            return hit[1]
        try:
            value = self.store.get_json(self.bucket, key, None)
        except Exception as exc:
            if hit:
                log.warning("store unreachable, serving cached copy", extra=kv(key=key, error=type(exc).__name__))
                return hit[1]
            raise Unavailable(type(exc).__name__) from exc
        with self._lock:
            self._small[key] = (now, value)
        return value

    def pointer(self) -> dict | None:
        p = self._cached_json(VIEW_POINTER)
        return p if isinstance(p, dict) and isinstance(p.get("current"), str) and VIEW_ID.match(p["current"]) else None

    def manifest(self) -> dict | None:
        m = self._cached_json(MANIFEST)
        return m if isinstance(m, dict) else None

    def view(self, view_id: str) -> Body:
        with self._lock:
            body = self._views.get(view_id)
            if body is not None:
                self._views.move_to_end(view_id)
                return body
        try:
            raw = self.store.get_bytes(self.bucket, view_key(view_id))
        except NotFound:
            raise
        except Exception as exc:
            raise Unavailable(type(exc).__name__) from exc
        body = Body(raw, gzip.compress(raw, compresslevel=6, mtime=0))
        with self._lock:
            self._views[view_id] = body
            while len(self._views) > self.max_views:
                self._views.popitem(last=False)
        return body

    def ping(self) -> None:
        try:
            self.store.head(self.bucket, VIEW_POINTER)
        except NotFound:
            return None  # reachable, nothing built yet


def etag_matches(header: str | None, etag: str) -> bool:
    """If-None-Match uses the weak comparison (a W/ prefix and gzip variants still match)."""
    if not header:
        return False
    for part in header.split(","):
        tag = part.strip()
        if tag == "*":
            return True
        if tag.startswith("W/"):
            tag = tag[2:]
        if tag.strip('"') == etag.strip('"'):
            return True
    return False


def _accepts_gzip(request: Request) -> bool:
    for part in request.headers.get("accept-encoding", "").split(","):
        name, _, params = part.strip().partition(";")
        if name.strip().lower() in ("gzip", "*") and params.replace(" ", "") not in ("q=0", "q=0.0", "q=0.00", "q=0.000"):
            return True
    return False


def _view_response(request: Request, view_id: str, body: Body, cache_control: str) -> Response:
    etag = f'"{view_id}"'
    headers = {"ETag": etag, "Cache-Control": cache_control, "Vary": "Accept-Encoding", "X-View-Id": view_id}
    if etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    if _accepts_gzip(request):
        headers["Content-Encoding"] = "gzip"
        return Response(body.gz, media_type=JSON_TYPE, headers=headers)
    return Response(body.raw, media_type=JSON_TYPE, headers=headers)


def create_app(*, store=None, bucket: str | None = None, authenticator: Authenticator | None = None,
               pointer_ttl: float | None = None, clock=time.monotonic) -> FastAPI:
    if store is None:
        from llmops_common.storage import S3Store

        store = S3Store.from_env()
    if bucket is None:
        from llmops_common.storage import bucket as bucket_name

        bucket = bucket_name("curated")
    authenticator = authenticator or Authenticator.from_env()
    ttl = env_float("POINTER_TTL_SECONDS", 30.0) if pointer_ttl is None else pointer_ttl
    src = ContentSource(store, bucket, ttl=ttl, max_views=env_int("VIEW_CACHE_SIZE", 4), clock=clock)

    app = FastAPI(title="content-api", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
    install_error_handlers(app)
    install_access_log(app)
    install_health(app, src.ping)
    app.state.content = src
    current_user = user_dependency(authenticator)

    def unavailable(code: str, message: str) -> JSONResponse:
        return error_response(503, code, message, headers={"Retry-After": "30", "Cache-Control": "no-store"})

    @app.get("/api/content/portal.json")
    def current_view(request: Request, user: Principal = Depends(current_user)):
        try:
            pointer = src.pointer()
            if pointer is None:
                return unavailable("no_view", "the portal view has not been built yet")
            view_id = pointer["current"]
            body = src.view(view_id)
        except NotFound:
            log.error("pointer names a missing view", extra=kv(view_id=pointer["current"]))
            return unavailable("no_view", "the current portal view is missing")
        except Unavailable as exc:
            return unavailable("store_unavailable", f"object storage is not reachable ({exc})")
        return _view_response(request, view_id, body, CC_CURRENT)

    @app.get("/api/content/views/{view_id}/portal.json")
    def one_view(view_id: str, request: Request, user: Principal = Depends(current_user)):
        if not VIEW_ID.match(view_id):
            return error_response(404, "not_found", "no such view")
        try:
            body = src.view(view_id)
        except NotFound:
            return error_response(404, "not_found", "no such view")
        except Unavailable as exc:
            return unavailable("store_unavailable", f"object storage is not reachable ({exc})")
        return _view_response(request, view_id, body, CC_IMMUTABLE)

    @app.get("/api/content/manifest")
    def manifest(user: Principal = Depends(current_user)):
        try:
            m = src.manifest()
            pointer = src.pointer()
        except Unavailable as exc:
            return unavailable("store_unavailable", f"object storage is not reachable ({exc})")
        if m is None:
            return unavailable("no_release", "no release has been published yet")
        body = {"current": m.get("current"), "history": m.get("history") or [], "view": pointer}
        return JSONResponse(body, headers={"Cache-Control": CC_CURRENT})

    return app
