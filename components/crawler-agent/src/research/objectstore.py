"""Object storage behind the agent's ``s3://bucket/prefix/`` locations.

On AWS the agent used ``boto3.client("s3")`` directly.  On the platform every ``s3://`` location goes through
``llmops_common.storage`` instead, so the same code works against AWS S3 and any S3-compatible store
(``S3_ENDPOINT_URL``, path-style addressing, ``S3_ACCESS_KEY_ID`` / ``S3_SECRET_ACCESS_KEY``; see docs/runtime-contract.md).

Tests (and anything else that needs another store) call ``set_store(MemoryStore())``.
"""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_store: Any = None


def get_store() -> Any:
    """The process-wide object store (an ``llmops_common.storage`` ObjectStore), created from the environment on first use."""
    global _store
    if _store is None:
        with _lock:
            if _store is None:
                from llmops_common.storage import S3Store

                _store = S3Store.from_env()
    return _store


def set_store(store: Any) -> Any:
    """Use ``store`` for every ``s3://`` location from now on (None: back to the environment's store).  Returns the old one."""
    global _store
    with _lock:
        old, _store = _store, store
    return old


def split_uri(uri: str) -> tuple[str, str]:
    """``s3://bucket/some/prefix`` -> ("bucket", "some/prefix/"); the prefix is "" or ends with "/"."""
    if not uri.startswith("s3://"):
        raise ValueError(f"not an s3:// location: {uri!r}")
    bucket, _, prefix = uri[len("s3://"):].partition("/")
    if not bucket:
        raise ValueError(f"no bucket in {uri!r}")
    return bucket, prefix if not prefix or prefix.endswith("/") else prefix + "/"


def not_found(exc: BaseException) -> bool:
    """Is ``exc`` the store's "no such object"?"""
    from llmops_common.storage import NotFound

    return isinstance(exc, NotFound)
