"""Tiny health server for workers and jobs (services built on FastAPI use fastapi_support instead).

GET /healthz -> 200 while the process runs.
GET /readyz  -> 200 when ready_check() returns without raising, else 503.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

__all__ = ["start_health_server"]


def start_health_server(port: int, ready_check: Callable[[], None] | None = None) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802 - http.server API
            if self.path == "/healthz":
                return self._send(200, {"status": "ok"})
            if self.path == "/readyz":
                try:
                    if ready_check:
                        ready_check()
                    return self._send(200, {"status": "ready"})
                except Exception as exc:  # report, never crash the probe
                    return self._send(503, {"status": "not-ready", "error": type(exc).__name__})
            return self._send(404, {"status": "not-found"})

        def log_message(self, *args):  # silence default stderr access log
            return

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104 - probes come from the kubelet
    thread = threading.Thread(target=server.serve_forever, name="health", daemon=True)
    thread.start()
    return server
