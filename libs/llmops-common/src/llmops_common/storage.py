"""Object storage through the S3 API only (AWS S3, Ceph RGW, versitygw, SeaweedFS ... anything S3-compatible).

Settings:
    S3_ENDPOINT_URL       empty for AWS S3; e.g. http://object-store:7070 for a self-hosted gateway
    S3_REGION             default us-east-1
    S3_ACCESS_KEY_ID      optional; without it the default AWS credential chain is used
    S3_SECRET_ACCESS_KEY  optional (or S3_SECRET_ACCESS_KEY_FILE)
    S3_FORCE_PATH_STYLE   default true when S3_ENDPOINT_URL is set
    BUCKET_RAW, BUCKET_CURATED, BUCKET_DATASETS, BUCKET_RUNS   bucket names (defaults llmops-<name>)
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Iterator, Protocol

from .config import env, env_bool

__all__ = ["NotFound", "ObjectStore", "S3Store", "MemoryStore", "ObjectInfo", "bucket", "BUCKET_KINDS", "store_from_env"]

BUCKET_KINDS = ("raw", "curated", "datasets", "runs")


class NotFound(KeyError):
    """The object does not exist."""


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int
    etag: str
    last_modified: str | None = None


class ObjectStore(Protocol):
    def get_bytes(self, bucket: str, key: str) -> bytes: ...
    def put_bytes(self, bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> str: ...
    def head(self, bucket: str, key: str) -> ObjectInfo: ...
    def list(self, bucket: str, prefix: str = "") -> Iterator[ObjectInfo]: ...
    def delete(self, bucket: str, key: str) -> None: ...
    def ensure_bucket(self, bucket: str) -> None: ...


def bucket(kind: str) -> str:
    """Bucket name for one of BUCKET_KINDS."""
    if kind not in BUCKET_KINDS:
        raise ValueError(f"unknown bucket kind {kind!r}")
    return env(f"BUCKET_{kind.upper()}", f"llmops-{kind}")


class _JsonMixin:
    def get_json(self, bucket_name: str, key: str, default=NotFound):
        try:
            return json.loads(self.get_bytes(bucket_name, key).decode("utf-8"))
        except NotFound:
            if default is NotFound:
                raise
            return default

    def put_json(self, bucket_name: str, key: str, obj) -> str:
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=False).encode("utf-8")
        return self.put_bytes(bucket_name, key, data, "application/json; charset=utf-8")

    def exists(self, bucket_name: str, key: str) -> bool:
        try:
            self.head(bucket_name, key)
            return True
        except NotFound:
            return False

    def put_jsonl(self, bucket_name: str, key: str, rows) -> str:
        body = "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows)
        return self.put_bytes(bucket_name, key, body.encode("utf-8"), "application/x-ndjson; charset=utf-8")

    def get_jsonl(self, bucket_name: str, key: str) -> list:
        text = self.get_bytes(bucket_name, key).decode("utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]


class S3Store(_JsonMixin):
    def __init__(self, client):
        self.client = client

    @classmethod
    def from_env(cls) -> "S3Store":
        import boto3
        from botocore.config import Config

        endpoint = env("S3_ENDPOINT_URL", None)
        path_style = env_bool("S3_FORCE_PATH_STYLE", bool(endpoint))
        cfg = Config(
            s3={"addressing_style": "path" if path_style else "auto"},
            retries={"max_attempts": 5, "mode": "standard"},
            connect_timeout=5,
            read_timeout=60,
        )
        kwargs = {"region_name": env("S3_REGION", "us-east-1"), "config": cfg}
        if endpoint:
            kwargs["endpoint_url"] = endpoint
        access, secret = env("S3_ACCESS_KEY_ID", None), env("S3_SECRET_ACCESS_KEY", None)
        if access and secret:
            kwargs.update(aws_access_key_id=access, aws_secret_access_key=secret)
        return cls(boto3.client("s3", **kwargs))

    @staticmethod
    def _missing(exc) -> bool:
        code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
        return code in ("NoSuchKey", "404", "NotFound", "NoSuchBucket")

    def get_bytes(self, bucket_name: str, key: str) -> bytes:
        from botocore.exceptions import ClientError

        try:
            return self.client.get_object(Bucket=bucket_name, Key=key)["Body"].read()
        except ClientError as exc:
            if self._missing(exc):
                raise NotFound(f"{bucket_name}/{key}") from exc
            raise

    def put_bytes(self, bucket_name: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        resp = self.client.put_object(Bucket=bucket_name, Key=key, Body=data, ContentType=content_type)
        return resp.get("ETag", "").strip('"')

    def head(self, bucket_name: str, key: str) -> ObjectInfo:
        from botocore.exceptions import ClientError

        try:
            h = self.client.head_object(Bucket=bucket_name, Key=key)
        except ClientError as exc:
            if self._missing(exc):
                raise NotFound(f"{bucket_name}/{key}") from exc
            raise
        lm = h.get("LastModified")
        return ObjectInfo(key=key, size=int(h.get("ContentLength", 0)), etag=h.get("ETag", "").strip('"'),
                          last_modified=lm.isoformat() if lm else None)

    def list(self, bucket_name: str, prefix: str = "") -> Iterator[ObjectInfo]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
            for o in page.get("Contents", []) or []:
                lm = o.get("LastModified")
                yield ObjectInfo(key=o["Key"], size=int(o.get("Size", 0)), etag=o.get("ETag", "").strip('"'),
                                 last_modified=lm.isoformat() if lm else None)

    def delete(self, bucket_name: str, key: str) -> None:
        self.client.delete_object(Bucket=bucket_name, Key=key)

    def ensure_bucket(self, bucket_name: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self.client.head_bucket(Bucket=bucket_name)
        except ClientError:
            self.client.create_bucket(Bucket=bucket_name)

    def ping(self, bucket_name: str) -> None:
        self.client.head_bucket(Bucket=bucket_name)


class MemoryStore(_JsonMixin):
    """In-process store with the same interface, for unit tests."""

    def __init__(self):
        self._data: dict[tuple[str, str], tuple[bytes, str]] = {}
        self._buckets: set[str] = set()
        self._lock = threading.Lock()

    def get_bytes(self, bucket_name: str, key: str) -> bytes:
        with self._lock:
            try:
                return self._data[(bucket_name, key)][0]
            except KeyError as exc:
                raise NotFound(f"{bucket_name}/{key}") from exc

    def put_bytes(self, bucket_name: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        import hashlib

        with self._lock:
            self._buckets.add(bucket_name)
            self._data[(bucket_name, key)] = (bytes(data), content_type)
        return hashlib.md5(data).hexdigest()  # noqa: S324 - S3-style ETag, not security

    def head(self, bucket_name: str, key: str) -> ObjectInfo:
        import hashlib

        data = self.get_bytes(bucket_name, key)
        return ObjectInfo(key=key, size=len(data), etag=hashlib.md5(data).hexdigest())  # noqa: S324

    def list(self, bucket_name: str, prefix: str = "") -> Iterator[ObjectInfo]:
        with self._lock:
            keys = sorted(k for (b, k) in self._data if b == bucket_name and k.startswith(prefix))
        for k in keys:
            yield self.head(bucket_name, k)

    def delete(self, bucket_name: str, key: str) -> None:
        with self._lock:
            self._data.pop((bucket_name, key), None)

    def ensure_bucket(self, bucket_name: str) -> None:
        with self._lock:
            self._buckets.add(bucket_name)

    def ping(self, bucket_name: str) -> None:
        return None


def store_from_env() -> S3Store:
    return S3Store.from_env()
