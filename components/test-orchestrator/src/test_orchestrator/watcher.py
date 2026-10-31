"""Gateway version watcher (+ the periodic reaper for runs whose last job died).

Every GW_POLL_SECONDS it asks the gateway (GW_URL) for its LiteLLM version with GatewayClient.gateway_version()
(v1.105 reports it only on the authenticated /health/readiness/details, so GW_KEY must be a key that may call it: the
master key or an admin key). The last gateway.version_changed event of that gateway in platform.events is the state;
no table of our own. A different version (or the first one ever seen) publishes
    gateway.version_changed {gateway, previous_version, version}
with dedupe key gateway.version_changed:<gateway>:<previous event id>:<version>, so replicas polling at the same
time publish it once, and A -> B -> A is still recorded.
"""
from __future__ import annotations

import threading
import time

from llmops_common.llm import GatewayClient
from llmops_common.log import get_logger, kv
from llmops_common.queue import publish

log = get_logger("test_orchestrator.watcher")
SOURCE = "test-orchestrator"


def check_gateway(conn, client, gateway: str) -> int | None:
    """Poll once; returns the new event id when the version changed."""
    version = client.gateway_version()
    if not version:
        log.warning("gateway version unavailable", extra=kv(gateway=gateway))
        return None
    last = conn.execute("SELECT id, payload FROM platform.events WHERE type = 'gateway.version_changed' "
                        "AND payload->>'gateway' = %s ORDER BY id DESC LIMIT 1", (gateway,)).fetchone()
    previous = (last["payload"] or {}).get("version") if last else None
    if previous == version:
        return None
    event_id = publish(conn, "gateway.version_changed", {"gateway": gateway, "previous_version": previous, "version": version},
                       source=SOURCE, dedupe_key=f"gateway.version_changed:{gateway}:{last['id'] if last else 0}:{version}")
    if event_id:
        log.info("gateway version changed", extra=kv(gateway=gateway, previous_version=previous, version=version))
    return event_id


class Watcher(threading.Thread):
    def __init__(self, orchestrator, client: GatewayClient | None = None):
        super().__init__(name="gateway-watcher", daemon=True)
        self.orch = orchestrator
        s = orchestrator.settings
        url = (s.gw_url or "").rstrip("/")
        url = url[:-3] if url.endswith("/v1") else url  # GatewayClient adds the paths itself
        self.client = client or (GatewayClient(url, s.gw_key, timeout=15, max_retries=0) if url else None)
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def tick(self, now: float, schedule: dict) -> None:
        s = self.orch.settings
        if self.client and now >= schedule.get("poll", 0):
            schedule["poll"] = now + s.gw_poll_seconds
            try:
                with self.orch.connection() as conn:
                    check_gateway(conn, self.client, s.gateway_name)
            except Exception:  # noqa: BLE001 - keep watching
                log.exception("gateway check failed", extra=kv(gateway=s.gateway_name))
        if now >= schedule.get("reap", 0):
            schedule["reap"] = now + s.reap_seconds
            try:
                finished = self.orch.reap()
                if finished:
                    log.info("runs finalised by the reaper", extra=kv(count=finished))
            except Exception:  # noqa: BLE001
                log.exception("reaper failed")

    def run(self) -> None:
        schedule: dict = {}
        log.info("watcher started", extra=kv(gateway=self.orch.settings.gateway_name, url_set=bool(self.client),
                                             poll_seconds=self.orch.settings.gw_poll_seconds))
        while not self._stop.is_set():
            self.tick(time.monotonic(), schedule)
            self._stop.wait(1.0)
