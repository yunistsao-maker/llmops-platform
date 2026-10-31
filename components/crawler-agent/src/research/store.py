"""Persist run results (object storage or a local directory) so scheduled runs leave something durable behind.

``s3://bucket/prefix/`` locations are written through ``research.objectstore`` (llmops_common.storage: AWS S3 or any
S3-compatible store); a plain path is a local directory (development, tests)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .objectstore import get_store, split_uri
from .validate.grounding import whole_json


def _dump(obj: Any) -> bytes:
    # whole_json: a record that holds half a surrogate pair somewhere (a tool argument the model wrote, say) is still saved
    return (json.dumps(whole_json(obj), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def run_location(output_uri: str, run_id: str) -> str:
    """Where ``save_result`` puts the full record of ``run_id`` (used to tell a background caller where to look)."""
    if output_uri.startswith("s3://"):
        bucket, _, prefix = output_uri[5:].partition("/")
        prefix = prefix if not prefix or prefix.endswith("/") else prefix + "/"
        return f"s3://{bucket}/{prefix}runs/{run_id}.json"
    return str(Path(output_uri) / "runs" / f"{run_id}.json")


def save_result(output_uri: str, result: dict, *, store: Any = None) -> str | None:
    """Write ``runs/<runId>.json`` (everything) and, for an accepted proposal, ``proposals/<id>.json``.

    Returns the location of the proposal (or of the run record when there is none), or None if ``output_uri`` is empty.
    """
    if not output_uri:
        return None
    run_id = result.get("runId", "run")
    items: list[tuple[str, dict]] = [(f"runs/{run_id}.json", result)]
    proposal = result.get("proposal")
    if proposal:
        items.append((f"proposals/{proposal['id']}.json", proposal))
    if output_uri.startswith("s3://"):
        bucket, prefix = split_uri(output_uri)
        s3 = store or get_store()
        for key, body in items:
            s3.put_bytes(bucket, prefix + key, _dump(body), "application/json")
        return f"s3://{bucket}/{prefix}{items[-1][0]}"
    root = Path(output_uri)
    for key, body in items:
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_dump(body))
    return str(root / items[-1][0])


# ---------------------------------------------------------------------------------------------
# small JSON records next to the runs (the roles' handoff notes, research/handoff.py)
# ---------------------------------------------------------------------------------------------
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*\Z")
#: a listing reads at most this many keys under one folder (a day of runs is a few dozen)
MAX_LISTED = 1000


def put_json(output_uri: str, key: str, obj: Any, *, store: Any = None) -> str:
    """Write ``obj`` at ``<output_uri>/<key>``; returns where it went.  ``key`` is a plain relative path."""
    if not output_uri or not _KEY.match(key) or ".." in key.split("/"):
        raise ValueError(f"not a result location: {output_uri!r} / {key!r}")
    if output_uri.startswith("s3://"):
        bucket, prefix = split_uri(output_uri)
        (store or get_store()).put_bytes(bucket, prefix + key, _dump(obj), "application/json")
        return f"s3://{bucket}/{prefix}{key}"
    path = Path(output_uri) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_dump(obj))
    return str(path)


def read_json_folder(output_uri: str, folder: str, *, newest: int, max_bytes: int, store: Any = None) -> list[tuple[str, Any]]:
    """The ``newest`` JSON documents directly under ``<output_uri>/<folder>/`` (by key, which sorts by time for the handoff
    notes), oldest first, as (key relative to the folder, parsed JSON).  A document larger than ``max_bytes`` or not JSON
    is skipped; a folder that does not exist is empty."""
    if not output_uri or not _KEY.match(folder) or ".." in folder.split("/"):
        return []
    names: list[str] = []
    if output_uri.startswith("s3://"):
        bucket, prefix = split_uri(output_uri)
        s3 = store or get_store()
        base = f"{prefix}{folder}/"
        for obj in s3.list(bucket, base):
            rest = obj.key[len(base):]
            if rest.endswith(".json") and "/" not in rest and obj.size <= max_bytes:
                names.append(rest)
            if len(names) >= MAX_LISTED:
                break

        def body(name: str) -> bytes:
            return s3.get_bytes(bucket, base + name)[: max_bytes + 1]
    else:
        root = Path(output_uri) / folder
        names = sorted(p.name for p in root.glob("*.json") if p.is_file())[-MAX_LISTED:] if root.is_dir() else []

        def body(name: str) -> bytes:
            with open(root / name, "rb") as f:
                return f.read(max_bytes + 1)
    out: list[tuple[str, Any]] = []
    for name in sorted(names)[-newest:] if newest > 0 else []:
        try:
            raw = body(name)
        except Exception:  # noqa: BLE001 - one unreadable document (deleted meanwhile, denied) does not hide the others
            continue
        if len(raw) > max_bytes:
            continue
        try:
            out.append((name, json.loads(raw)))
        except ValueError:
            continue
    return out
