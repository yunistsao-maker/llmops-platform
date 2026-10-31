"""The datasets bucket (contracts/storage-layout.md):

    {SEC-xx}/{dataset_id}/v{N}/cases.jsonl     one case per line
    {SEC-xx}/{dataset_id}/v{N}/manifest.json   written last: a version without it is incomplete
    {SEC-xx}/{dataset_id}/v{N}/assets/<name>   files a scorer needs at run time (extension: zhtw dictionaries)
    index.json                                 {"SEC-01": {"dataset_id": "...", "version": 3}, ...} latest approved

Every version is written once under new keys; index.json is the only object that is overwritten.
"""
from __future__ import annotations

import hashlib
import json
import re

INDEX_KEY = "index.json"
_VERSION = re.compile(r"^(SEC-\d{2})/([^/]+)/v(\d+)/")


def version_prefix(sec_id: str, dataset_id: str, version: int) -> str:
    return f"{sec_id}/{dataset_id}/v{version}/"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def jsonl_bytes(rows: list[dict]) -> bytes:
    return "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows).encode("utf-8")


class DatasetBucket:
    def __init__(self, store, bucket_name: str):
        self.store = store
        self.bucket = bucket_name

    # -- reading ----------------------------------------------------------------------------------
    def index(self) -> dict:
        return self.store.get_json(self.bucket, INDEX_KEY, default={}) or {}

    def versions(self, sec_id: str, dataset_id: str) -> list[int]:
        """Every version number that has any object (complete or not), ascending."""
        seen = set()
        for info in self.store.list(self.bucket, f"{sec_id}/{dataset_id}/v"):
            m = _VERSION.match(info.key)
            if m:
                seen.add(int(m.group(3)))
        return sorted(seen)

    def manifests(self, sec_id: str | None = None, dataset_id: str | None = None) -> list[dict]:
        """Manifests of complete versions, optionally filtered."""
        prefix = f"{sec_id}/" if sec_id else ""
        if sec_id and dataset_id:
            prefix = f"{sec_id}/{dataset_id}/"
        out = []
        for info in self.store.list(self.bucket, prefix):
            m = _VERSION.match(info.key)
            if not m or info.key != version_prefix(m.group(1), m.group(2), int(m.group(3))) + "manifest.json":
                continue
            if dataset_id and m.group(2) != dataset_id:
                continue
            out.append(self.store.get_json(self.bucket, info.key))
        return sorted(out, key=lambda d: (d.get("sec_id", ""), d.get("dataset_id", ""), d.get("version", 0)))

    # -- writing ----------------------------------------------------------------------------------
    def write_version(self, sec_id: str, dataset_id: str, cases: list[dict], manifest: dict,
                      assets: dict[str, bytes] | None = None) -> dict:
        """Write cases (+ assets) then manifest.json as a new version; returns the manifest."""
        existing = self.versions(sec_id, dataset_id)
        version = (existing[-1] + 1) if existing else 1
        prefix = version_prefix(sec_id, dataset_id, version)
        body = jsonl_bytes(cases)
        asset_info = {}
        for name, data in sorted((assets or {}).items()):
            key = prefix + "assets/" + name
            self.store.put_bytes(self.bucket, key, data, "text/plain; charset=utf-8")
            asset_info[name] = {"key": key, "bytes": len(data), "sha256": sha256_hex(data)}
        self.store.put_bytes(self.bucket, prefix + "cases.jsonl", body, "application/x-ndjson; charset=utf-8")
        doc = {"sec_id": sec_id, "dataset_id": dataset_id, "version": version, **manifest, "n": len(cases),
               "sha256": sha256_hex(body), "cases_key": prefix + "cases.jsonl"}
        if asset_info:
            doc["assets"] = asset_info
        self.store.put_json(self.bucket, prefix + "manifest.json", doc)
        return doc

    def activate(self, sec_id: str, dataset_id: str, version: int) -> dict:
        """Point index.json at this version (small pointer file, overwritten atomically by the object store)."""
        index = self.index()
        index[sec_id] = {"dataset_id": dataset_id, "version": int(version)}
        index = dict(sorted(index.items()))
        self.store.put_json(self.bucket, INDEX_KEY, index)
        return index
