"""Request limits: body size (ASGI middleware), per-user rate limit, and a cap on concurrent gateway calls."""
from __future__ import annotations

import collections
import json
import threading
import time


class BodyLimit:
    """Reject request bodies over `max_bytes` with 413, whether or not Content-Length is sent."""

    class _TooLarge(Exception):
        pass

    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def _reject(self, send) -> None:
        body = json.dumps({"error": {"code": "too_large", "message": f"request body over {self.max_bytes} bytes"}}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for name, value in scope.get("headers") or []:
            if name == b"content-length":
                try:
                    if int(value) > self.max_bytes:
                        return await self._reject(send)
                except ValueError:
                    return await self._reject(send)
        seen = 0
        started = False

        async def limited_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    raise BodyLimit._TooLarge()
            return message

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BodyLimit._TooLarge:
            if not started:
                await self._reject(send)


class RateLimiter:
    """Sliding window: at most `limit` calls per `window` seconds per key (the signed-in user)."""

    def __init__(self, limit: int, window: float = 60.0, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self._hits: dict[str, collections.deque] = {}
        self._lock = threading.Lock()
        self._calls = 0

    def check(self, key: str) -> float:
        """Record a call. Returns 0 when allowed, else the seconds to wait."""
        if self.limit <= 0:
            return 0.0
        now = self.clock()
        with self._lock:
            self._calls += 1
            if self._calls % 1000 == 0:  # forget idle users
                for k in [k for k, q in self._hits.items() if not q or now - q[-1] > self.window]:
                    del self._hits[k]
            q = self._hits.setdefault(key, collections.deque())
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.limit:
                return max(0.5, self.window - (now - q[0]))
            q.append(now)
            return 0.0


class Concurrency:
    """At most `limit` gateway calls at once; callers wait up to `wait` seconds for a slot."""

    def __init__(self, limit: int, wait: float):
        self._sem = threading.BoundedSemaphore(max(1, limit))
        self.wait = wait

    def acquire(self) -> bool:
        return self._sem.acquire(timeout=self.wait)

    def release(self) -> None:
        self._sem.release()
