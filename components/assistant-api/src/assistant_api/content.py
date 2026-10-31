"""The current view's search index, rebuilt when portal/views/current.json points somewhere new (polled)."""
from __future__ import annotations

import json
import re
import threading

from llmops_common.log import get_logger, kv
from llmops_common.storage import NotFound

from .retrieval import Index

log = get_logger("assistant_api.content")

VIEW_POINTER = "portal/views/current.json"
VIEW_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ContentIndex:
    def __init__(self, store, bucket: str, poll_seconds: float = 30.0):
        self.store, self.bucket, self.poll_seconds = store, bucket, poll_seconds
        self._index: Index | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def index(self) -> Index | None:
        return self._index

    def refresh(self) -> bool:
        """Load the view the pointer names if it is not the one indexed. Returns True when the index changed."""
        pointer = self.store.get_json(self.bucket, VIEW_POINTER, None)
        view_id = pointer.get("current") if isinstance(pointer, dict) else None
        if not isinstance(view_id, str) or not VIEW_ID.match(view_id):
            return False
        current = self._index
        if current is not None and current.view_id == view_id:
            return False
        with self._lock:
            if self._index is not None and self._index.view_id == view_id:
                return False
            raw = self.store.get_bytes(self.bucket, f"portal/views/{view_id}/portal.json")
            index = Index(json.loads(raw.decode("utf-8")), view_id)
            self._index = index
        log.info("index rebuilt", extra=kv(view_id=view_id, docs=len(index.docs)))
        return True

    def safe_refresh(self) -> None:
        try:
            self.refresh()
        except NotFound:
            log.warning("pointer names a missing view")
        except Exception as exc:  # keep serving the old index
            log.warning("index refresh failed", extra=kv(error=f"{type(exc).__name__}: {exc}"))

    def start(self) -> None:
        """Load the index and keep it current in a background thread (start-up never waits on the store)."""
        if self._thread is not None:
            return

        def loop():
            self.safe_refresh()
            while not self._stop.wait(self.poll_seconds):
                self.safe_refresh()

        self._thread = threading.Thread(target=loop, name="index-poll", daemon=True)
        self._thread.start()

    def ensure(self) -> Index | None:
        """The index, loading it now only when no background poller is running (tests, one-off use)."""
        if self._index is None and self._thread is None:
            self.safe_refresh()
        return self._index

    def stop(self) -> None:
        self._stop.set()

    def ping(self) -> None:
        try:
            self.store.head(self.bucket, VIEW_POINTER)
        except NotFound:
            return None
