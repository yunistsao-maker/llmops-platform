"""Object storage for the publisher: one prefix of a bucket, behind the small interface the publishing code uses.

    PrefixStore(store, bucket, "portal/")     the portal data (manifest.json, releases/<id>/...)
    PrefixStore(store, bucket, "pipeline/")   the publisher's own records (rejected proposals, decisions)
    PrefixStore(store, bucket, "")            the research results the crawler wrote (research/proposals/...)

``store`` is an ``llmops_common.storage`` ObjectStore: ``S3Store.from_env()`` on the platform (AWS S3 or any
S3-compatible endpoint), ``MemoryStore`` in tests.

Conditional writes.  On AWS the pipeline relied on S3 conditional writes (If-Match / If-None-Match); S3-compatible
stores differ in whether and how they support them.  Here a conditional write is read - compare - write, and that is
safe because every writer of these prefixes is a publisher holding the publish lock (``publisher.lock``: a PostgreSQL
advisory lock).  ``Conflict`` keeps its meaning: somebody else changed (or created) the object first.  The version
token ("etag") of an object is the sha256 of its content, so it means the same on every store.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Protocol

# one or more "/"-separated segments of safe characters; no "..", no leading "/" or ".", no empty segments
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@+=-]*(/[A-Za-z0-9][A-Za-z0-9._@+=-]*)*")  # used with fullmatch


class StorageError(Exception):
    """A storage operation failed for a reason the caller cannot fix by retrying blindly."""


class NotFound(StorageError):
    """The object does not exist."""


class Conflict(StorageError):
    """A conditional write was refused: somebody else changed (or created) the object first."""


def check_key(key: str) -> str:
    if not isinstance(key, str) or len(key) > 512 or not _KEY.fullmatch(key):
        raise StorageError(f"unsafe object key: {key!r}")
    return key


def check_prefix(prefix: str) -> str:
    if prefix == "":
        return prefix
    check_key(prefix.rstrip("/"))
    return prefix


def check_etag(etag: str) -> str:
    """The version token of a compare-and-swap; an empty one would turn the write into an unconditional one."""
    token = str(etag).strip().strip('"').strip()
    if not token:
        raise StorageError("a conditional write needs the version (ETag) that was read; got an empty one")
    return token


def version_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Blob:
    data: bytes
    etag: str  # opaque version token: pass it back as ``if_match``


class Store(Protocol):
    def get(self, key: str) -> Blob: ...

    def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream", cache_control: str | None = None,
            if_match: str | None = None, if_none_match: bool = False) -> str: ...

    def list(self, prefix: str = "") -> list[str]: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...

    def uri(self, key: str = "") -> str: ...


class PrefixStore:
    """``prefix`` inside ``bucket`` of an llmops_common ObjectStore."""

    concurrent = True  # every call is a round trip on S3; boto3 clients and MemoryStore are thread-safe

    def __init__(self, store: Any, bucket: str, prefix: str = ""):
        if not bucket:
            raise StorageError("no bucket given")
        self.store = store
        self.bucket = bucket
        self.prefix = prefix if not prefix or prefix.endswith("/") else prefix + "/"
        if self.prefix:
            check_key(self.prefix.rstrip("/"))

    def _key(self, key: str) -> str:
        return self.prefix + check_key(key)

    @staticmethod
    def _missing(exc: BaseException) -> bool:
        from llmops_common.storage import NotFound as Missing

        return isinstance(exc, Missing)

    def get(self, key: str) -> Blob:
        try:
            data = self.store.get_bytes(self.bucket, self._key(key))
        except Exception as e:
            if self._missing(e):
                raise NotFound(self.uri(key)) from e
            raise
        return Blob(data, version_of(data))

    def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream", cache_control: str | None = None,
            if_match: str | None = None, if_none_match: bool = False) -> str:
        """``cache_control`` is accepted for the publishing code's sake and not used: releases are read inside the cluster."""
        full = self._key(key)
        expected = check_etag(if_match) if if_match is not None else None
        if if_none_match and self.exists(key):
            raise Conflict(f"{self.uri(key)} already exists")
        if expected is not None:
            try:
                current = self.get(key)
            except NotFound as e:
                raise Conflict(f"{self.uri(key)} was removed since it was read") from e
            if current.etag != expected:
                raise Conflict(f"{self.uri(key)} was changed by someone else")
        self.store.put_bytes(self.bucket, full, data, content_type)
        return version_of(data)

    def list(self, prefix: str = "") -> list[str]:
        check_prefix(prefix)
        return sorted(o.key[len(self.prefix):] for o in self.store.list(self.bucket, self.prefix + prefix))

    def exists(self, key: str) -> bool:
        try:
            self.store.head(self.bucket, self._key(key))
        except Exception as e:
            if self._missing(e):
                return False
            raise
        return True

    def delete(self, key: str) -> None:
        self.store.delete(self.bucket, self._key(key))

    def uri(self, key: str = "") -> str:
        return f"s3://{self.bucket}/{self.prefix}{key}"
