"""Read-only view of the portal's data files (schema + data/*.json).

The agent only ever *reads* portal data.  Sources, in order of preference:

* ``s3://bucket/prefix/``  -- the portal data the UI is served from (on the platform: ``s3://<curated bucket>/portal/``,
  read through ``research.objectstore``, i.e. any S3-compatible store).  When ``manifest.json`` at that location is a
  release pointer (written by the publisher component: ``{"current": <release id>, "history": [...]}``, or the older
  ``portal-manifest/v1`` pointer), the release it names is read (``releases/<id>/schema`` and ``releases/<id>/data``);
  otherwise the files are expected directly under the prefix;
* a local directory       -- the same two layouts on disk: a pipeline data folder, or a checkout of the portal repo;
* ``bundled``             -- the snapshot shipped with the agent (a SAMPLE until you run
  ``scripts/sync_portal_data.py``); proposals made against it say so.

``data/internal/`` (platform inventory) and ``runs/`` are never loaded: they may hold internal
information and the research agent has no business editing them.
"""

from __future__ import annotations

import copy
import fnmatch
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_PATH = "schema/llmops.schema.json"
DATA_GLOBS = [
    "data/sources.json", "data/catalog.json", "data/lifecycle.json", "data/litellm.json",
    "data/requirements.json", "data/digests/*.json", "data/models/*.json", "data/open-models.json",
]
BUNDLED_DIR = Path(__file__).parent / "snapshot"
#: written next to the synced files by ``scripts/sync_portal_data.py``: which portal commit the copy came from
SYNC_MANIFEST = "sync_manifest.json"
#: written by the publishing pipeline at the root of the data location: names the immutable release in use
RELEASE_POINTER = "manifest.json"
RELEASE_POINTER_SCHEMA = "portal-manifest/v1"
_RELEASE_BASE = re.compile(r"^releases/[A-Za-z0-9][A-Za-z0-9._-]{0,99}/\Z")
_RELEASE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")


def is_data_path(rel: str) -> bool:
    return any(fnmatch.fnmatch(rel, g) for g in DATA_GLOBS)


def sync_info(raw: Any) -> dict:
    """The few fields of a ``sync_manifest.json`` worth carrying into a proposal (whitelisted, length-limited)."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("commit", "syncedAt"):
        if isinstance(raw.get(key), str) and raw[key]:
            out[key] = raw[key][:40]
    if isinstance(raw.get("dirty"), bool):
        out["dirty"] = raw["dirty"]
    return out


def release_base(raw: Any) -> tuple[str, dict]:
    """``(basePath, info)`` when ``raw`` is a release pointer, ``("", {})`` when it is anything else.

    The platform's pointer is ``{"current": <release id>, "history": [...]}`` (contracts/storage-layout.md); the older
    ``portal-manifest/v1`` pointer (``basePath``) is still understood.  A pointer that names a release but is malformed
    raises: silently falling back to other files under the same prefix could make the agent research against stale data.
    """
    if isinstance(raw, dict) and "current" in raw and raw.get("schema") != RELEASE_POINTER_SCHEMA:
        current = raw.get("current")
        if not isinstance(current, str) or not _RELEASE_NAME.match(current):
            raise ValueError(f"{RELEASE_POINTER} names an invalid release: {str(current)[:100]!r}")
        info: dict[str, Any] = {"releaseId": current}
        published = raw.get("published_at") or raw.get("publishedAt")
        if isinstance(published, str) and published:
            info["publishedAt"] = published[:40]
        recent = recent_releases(raw.get("history"))
        if recent:
            info["recentReleases"] = recent
        return f"releases/{current}/", info
    if not isinstance(raw, dict) or raw.get("schema") != RELEASE_POINTER_SCHEMA:
        return "", {}
    base = raw.get("basePath")
    if not isinstance(base, str) or not _RELEASE_BASE.match(base):
        raise ValueError(f"{RELEASE_POINTER} names an invalid release path: {base!r}")
    info: dict[str, Any] = {"releaseId": str(raw.get("releaseId") or base.split("/")[1])[:100]}
    if isinstance(raw.get("publishedAt"), str) and raw["publishedAt"]:
        info["publishedAt"] = raw["publishedAt"][:40]
    recent = recent_releases(raw.get("history"))
    if recent:
        info["recentReleases"] = recent
    return base, info


#: how much of the pointer's history the agent sees: enough for "what was published today and yesterday" (the Radar
#: editor summarises it), small enough for a prompt.  The summaries were written by earlier runs: data, not instructions.
RECENT_RELEASES = 12


#: history entries of the platform's pointer use snake_case names; the older pointer used camelCase
_HISTORY_KEYS = (("releaseId", "release_id", 60), ("publishedAt", "published_at", 40), ("kind", "kind", 20), ("summary", "summary", 300))


def recent_releases(history: Any) -> list[dict]:
    out: list[dict] = []
    for h in history if isinstance(history, list) else []:
        if not isinstance(h, dict):
            continue
        entry = {}
        for key, snake, limit in _HISTORY_KEYS:
            value = h.get(key) if isinstance(h.get(key), str) and h.get(key) else h.get(snake)
            if isinstance(value, str) and value:
                entry[key] = value[:limit]
        if isinstance(h.get("changed"), int) and not isinstance(h.get("changed"), bool):
            entry["changed"] = h["changed"]
        if entry.get("releaseId"):
            out.append(entry)
        if len(out) >= RECENT_RELEASES:
            break
    return out


@dataclass
class PortalData:
    schema: dict
    docs: dict[str, Any]  # "data/lifecycle.json" -> parsed JSON
    manifest: dict = field(default_factory=dict)

    # ---- constructors ------------------------------------------------------------------
    @classmethod
    def from_dir(cls, root: str | os.PathLike) -> "PortalData":
        top = Path(root)
        base, info = "", {}
        pointer = top / RELEASE_POINTER
        if pointer.is_file():
            base, info = release_base(json.loads(pointer.read_text(encoding="utf-8")))
        root = top / base if base else top
        schema = json.loads((root / SCHEMA_PATH).read_text(encoding="utf-8"))
        docs: dict[str, Any] = {}
        for path in sorted((root / "data").rglob("*.json")):
            rel = path.relative_to(root).as_posix()
            if is_data_path(rel):
                docs[rel] = json.loads(path.read_text(encoding="utf-8"))
        if base:
            return cls(schema, docs, {"kind": "release", "source": str(top), **info})
        manifest = {"kind": "portal-checkout", "source": str(root)}
        mpath = root / "snapshot_manifest.json"
        if mpath.exists():
            manifest.update(json.loads(mpath.read_text(encoding="utf-8")))
        return cls(schema, docs, manifest)

    @classmethod
    def from_mapping(cls, files: dict[str, Any], manifest: dict | None = None) -> "PortalData":
        """``files`` maps relative paths ("schema/llmops.schema.json", "data/...") to parsed JSON."""
        schema = files[SCHEMA_PATH]
        docs = {k: v for k, v in files.items() if is_data_path(k)}
        return cls(schema, docs, manifest or {"kind": "inline"})

    @classmethod
    def from_s3(cls, uri: str, store: Any = None) -> "PortalData":
        """``store``: an llmops_common.storage ObjectStore (default: research.objectstore.get_store())."""
        from ..objectstore import get_store, not_found, split_uri

        bucket, prefix = split_uri(uri)
        s3 = store or get_store()
        files: dict[str, Any] = {}
        sync: dict = {}
        base, info = "", {}
        try:
            raw_pointer = s3.get_bytes(bucket, prefix + RELEASE_POINTER)
        except Exception as e:  # noqa: BLE001 - only "no such object" means "no pointer"; anything else is a failed read
            if not not_found(e):
                raise
            raw_pointer = None
        if raw_pointer is not None:
            base, info = release_base(json.loads(raw_pointer))
        top, prefix = prefix, prefix + base  # everything below is read from the release (or from the flat copy)
        for obj in s3.list(bucket, prefix):
            rel = obj.key[len(prefix):]
            if rel == SCHEMA_PATH or is_data_path(rel):
                files[rel] = json.loads(s3.get_bytes(bucket, obj.key))
            elif rel == SYNC_MANIFEST:
                try:
                    sync = sync_info(json.loads(s3.get_bytes(bucket, obj.key)))
                except ValueError:
                    sync = {}  # optional metadata: a malformed manifest must not stop a run
        if SCHEMA_PATH not in files:
            raise FileNotFoundError(f"s3://{bucket}/{prefix}{SCHEMA_PATH} not found")
        if base:
            return cls.from_mapping(files, {"kind": "release", "source": f"s3://{bucket}/{top}", **info})
        return cls.from_mapping(files, {"kind": "s3", "source": uri, **sync})

    @classmethod
    def load(cls, location: str = "bundled") -> "PortalData":
        if location in ("", "bundled"):
            data = cls.from_dir(BUNDLED_DIR)
            return data
        if location.startswith("s3://"):
            return cls.from_s3(location)
        return cls.from_dir(location)

    # ---- helpers -----------------------------------------------------------------------
    def clone(self) -> "PortalData":
        return PortalData(self.schema, copy.deepcopy(self.docs), dict(self.manifest))

    @property
    def sources(self) -> list[dict]:
        return list(self.docs.get("data/sources.json", {}).get("sources", []))

    def source_by_id(self) -> dict[str, dict]:
        return {s["id"]: s for s in self.sources}

    def summary(self) -> dict:
        lc = self.docs.get("data/lifecycle.json", {})
        cat = self.docs.get("data/catalog.json", {})
        digests = sorted(k for k in self.docs if k.startswith("data/digests/"))
        models = [m.get("id") for m in cat.get("models", []) if isinstance(m, dict)] if isinstance(cat.get("models"), list) else []
        dives = sorted(Path(k).stem for k in self.docs if k.startswith("data/models/"))
        # the models a deep dive is about: by its file name, and by what it says (a file of another name - patch.create_problem)
        covered = set(dives) | {d["modelId"] for k, d in self.docs.items()
                                if k.startswith("data/models/") and isinstance(d, dict) and isinstance(d.get("modelId"), str)}
        return {
            "manifest": self.manifest,
            "sources": len(self.sources),
            "models": models,
            "lifecycleEvents": len(lc.get("events", [])),
            "lifecycleAsOf": lc.get("asOf"),
            "catalogAsOf": cat.get("asOf"),
            "digests": [Path(d).stem for d in digests],
            "deepDives": dives,
            # catalog models that no deep dive is about: a proposal may create data/models/<id>.json for each (it waits for a person)
            "modelsWithoutDeepDive": [m for m in models if isinstance(m, str) and m not in covered],
            **({"openModels": [m.get("id") for m in om.get("models", []) if isinstance(m, dict)], "openModelsAsOf": om.get("asOf")}
               if isinstance(om := self.docs.get("data/open-models.json"), dict) else {}),
        }

    @property
    def is_sample(self) -> bool:
        return self.manifest.get("kind") == "sample"
