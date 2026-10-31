"""Curated-bucket I/O (contracts/storage-layout.md): read the build inputs, publish a view, seed a starter release."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from llmops_common.ids import iso, new_release_id, utcnow
from llmops_common.log import get_logger, kv
from llmops_common.storage import NotFound

from .compile import MODEL_ID_RE, OPTIONAL_FILES, REQUIRED_FILES, Inputs, build_view
from .security import to_portal_security

log = get_logger("view_builder.store")

MANIFEST = "portal/manifest.json"
EDITORIAL = "portal/editorial/current.json"
MATRIX = "portal/security-matrix/current.json"
SECURITY_POINTER = "portal/security/current.json"
VIEW_POINTER = "portal/views/current.json"
VIEWS_PREFIX = "portal/views/"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
VIEW_ID = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{12}$")
JSON_TYPE = "application/json; charset=utf-8"


class NoRelease(Exception):
    """portal/manifest.json is missing or has no current release: nothing to build yet."""


@dataclass
class BuildResult:
    view_id: str
    changed: bool
    release_id: str
    security_run_id: str | None
    warnings: list[str]


def release_data_key(release_id: str, name: str) -> str:
    return f"portal/releases/{release_id}/data/{name}"


def view_key(view_id: str) -> str:
    return f"{VIEWS_PREFIX}{view_id}/portal.json"


def _safe(value, what: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.match(value) or ".." in value:
        raise ValueError(f"{what} {value!r} is not a safe id")
    return value


def load_inputs(store, bucket: str) -> tuple[Inputs, str, str | None]:
    """Read everything the build needs. Returns (inputs, release_id, security_run_id)."""
    manifest = store.get_json(bucket, MANIFEST, None)
    if not isinstance(manifest, dict) or not manifest.get("current"):
        raise NoRelease("portal/manifest.json has no current release")
    release_id = _safe(manifest["current"], "release id")
    data = {}
    for name in REQUIRED_FILES:
        try:
            data[name] = store.get_json(bucket, release_data_key(release_id, name))
        except NotFound:
            data[name] = None  # build_view reports the missing file
    data = {k: v for k, v in data.items() if v is not None}
    for name in OPTIONAL_FILES:
        value = store.get_json(bucket, release_data_key(release_id, name), None)
        if value is not None:
            data[name] = value
    for m in (data.get("catalog.json") or {}).get("models") or []:
        mid = m.get("id")
        if isinstance(mid, str) and MODEL_ID_RE.match(mid):
            deep = store.get_json(bucket, release_data_key(release_id, f"models/{mid}.json"), None)
            if deep is not None:
                data[f"models/{mid}.json"] = deep
    release = store.get_json(bucket, f"portal/releases/{release_id}/release.json", {}) or {}
    editorial = store.get_json(bucket, EDITORIAL, None)
    matrix = store.get_json(bucket, MATRIX, None)
    run_id = None
    security = None
    pointer = store.get_json(bucket, SECURITY_POINTER, None)
    if isinstance(pointer, dict) and pointer.get("current"):
        run_id = _safe(pointer["current"], "security run id")
        security = store.get_json(bucket, f"portal/security/{run_id}.json", None)
        if security is None:
            log.warning("security pointer names a missing run", extra=kv(run_id=run_id))
            run_id = None
    source = release.get("source") or f"release:{release_id}"
    return Inputs(data=data, editorial=editorial, security=security, matrix=matrix, source=source), release_id, run_id


def encode_view(view: dict) -> bytes:
    return json.dumps(view, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def publish_view(store, bucket: str, view: dict, *, release_id: str, security_run_id: str | None,
                 now: dt.datetime | None = None, keep_views: int = 30) -> tuple[str, bool]:
    """Write portal/views/{view_id}/portal.json (new key) and move the pointer. Unchanged content is a no-op."""
    body = encode_view(view)
    digest = hashlib.sha256(body).hexdigest()[:12]
    current = store.get_json(bucket, VIEW_POINTER, None)
    cur_id = current.get("current") if isinstance(current, dict) else None
    if isinstance(cur_id, str) and cur_id.endswith("-" + digest) and store.exists(bucket, view_key(cur_id)):
        if (current.get("release_id"), current.get("security_run_id")) != (release_id, security_run_id):
            store.put_json(bucket, VIEW_POINTER, {"current": cur_id, "release_id": release_id,
                                                  "security_run_id": security_run_id, "built_at": iso(now or utcnow())})
        return cur_id, False
    now = now or utcnow()
    view_id = now.strftime("%Y%m%dT%H%M%SZ") + "-" + digest
    store.put_bytes(bucket, view_key(view_id), body, JSON_TYPE)
    store.put_json(bucket, VIEW_POINTER, {"current": view_id, "release_id": release_id,
                                          "security_run_id": security_run_id, "built_at": iso(now)})
    if keep_views > 0:
        prune_views(store, bucket, keep_views, protect={view_id})
    return view_id, True


def prune_views(store, bucket: str, keep: int, protect: set[str]) -> list[str]:
    """Delete all but the newest `keep` views (view ids sort by time). Best effort: needs list + delete rights."""
    try:
        ids = sorted({o.key[len(VIEWS_PREFIX):].split("/", 1)[0] for o in store.list(bucket, VIEWS_PREFIX)
                      if o.key.endswith("/portal.json")})
        ids = [i for i in ids if VIEW_ID.match(i)]
        doomed = [i for i in ids[:-keep] if i not in protect] if len(ids) > keep else []
        for i in doomed:
            store.delete(bucket, view_key(i))
        if doomed:
            log.info("old views pruned", extra=kv(count=len(doomed)))
        return doomed
    except Exception as exc:  # pruning must never fail a build
        log.warning("view pruning skipped", extra=kv(error=f"{type(exc).__name__}: {exc}"))
        return []


def build_and_publish(store, bucket: str, *, today: dt.date, news_window_days: int = 30, keep_views: int = 30,
                      now: dt.datetime | None = None) -> BuildResult:
    inputs, release_id, run_id = load_inputs(store, bucket)
    view = build_view(inputs, today, news_window_days=news_window_days)
    view_id, changed = publish_view(store, bucket, view, release_id=release_id, security_run_id=run_id, now=now,
                                    keep_views=keep_views)
    for w in inputs.warnings:
        log.warning("build note", extra=kv(note=w))
    log.info("view ready" if changed else "view unchanged",
             extra=kv(view_id=view_id, release_id=release_id, security_run_id=run_id, models=len(view["models"]),
                      news=len(view["news"]), ret=len(view["ret"])))
    return BuildResult(view_id, changed, release_id, run_id, list(inputs.warnings))


# ------------------------------------------------------------------------------------------------ local files
def read_json(path: Path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def data_files(data_dir: Path) -> dict[str, Path]:
    """Every *.json under the data directory, keyed by its path relative to it (internal/ is skipped)."""
    out = {}
    for p in sorted(data_dir.rglob("*.json")):
        rel = p.relative_to(data_dir).as_posix()
        if rel.startswith("internal/") or any(part.startswith(".") for part in p.relative_to(data_dir).parts):
            continue
        out[rel] = p
    return out


def local_inputs(data_dir: Path, *, security_run: Path | None = None, matrix: Path | None = None,
                 editorial: Path | None = None, source: str | None = None) -> Inputs:
    files = data_files(data_dir)
    data = {rel: read_json(p) for rel, p in files.items()
            if rel in REQUIRED_FILES or rel in OPTIONAL_FILES or rel.startswith("models/")}
    return Inputs(data=data, editorial=read_json(editorial) if editorial else None,
                  security=to_portal_security(read_json(security_run)) if security_run else None,
                  matrix=read_json(matrix) if matrix else None, source=source or git_source(data_dir) or f"local:{data_dir.name}")


def git_source(path: Path) -> str | None:
    """'owner/repo@shortsha' for a git checkout (the provenance label the prototype showed), else None."""
    try:
        def git(*args):
            return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=5,
                                  check=True).stdout.strip()
        sha = git("rev-parse", "--short", "HEAD")
        url = git("remote", "get-url", "origin")
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
    return f"{m.group(1)}@{sha}" if m and sha else None


# ------------------------------------------------------------------------------------------------ seed
def seed(store, bucket: str, data_dir: Path | None, *, security_run: Path | None, matrix: Path | None,
         editorial: Path | None, release_id: str | None = None, source: str | None = None, force: bool = False,
         now: dt.datetime | None = None, release: bool = True) -> dict:
    """Upload a starter release (+ manifest with one release), editorial, matrix and security results.
    release=False uploads only editorial, matrix and security results (the publisher made the release)."""
    if not release:
        done = {"release_id": None, "files": 0, "editorial": False, "matrix": False, "security_run_id": None}
        return _seed_extras(store, bucket, done, security_run=security_run, matrix=matrix, editorial=editorial)
    if data_dir is None or not data_dir.is_dir():
        raise ValueError(f"--data-dir {data_dir} is not a directory")
    files = data_files(data_dir)
    missing = [f for f in REQUIRED_FILES if f not in files]
    if missing:
        raise ValueError(f"{data_dir} lacks {', '.join(missing)}")
    if store.exists(bucket, MANIFEST) and not force:
        raise FileExistsError("portal/manifest.json already exists; pass --force to replace it")
    now = now or utcnow()
    release_id = _safe(release_id or new_release_id(now), "release id")
    for rel, path in files.items():
        store.put_bytes(bucket, release_data_key(release_id, rel), path.read_bytes(), JSON_TYPE)
    release = {"release_id": release_id, "created_at": iso(now), "source_proposals": [], "held": False,
               "source": source or git_source(data_dir) or f"seed:{data_dir.name}"}
    store.put_json(bucket, f"portal/releases/{release_id}/release.json", release)
    store.put_json(bucket, MANIFEST, {"current": release_id, "history": [release_id]})
    done = {"release_id": release_id, "files": len(files), "editorial": False, "matrix": False, "security_run_id": None}
    return _seed_extras(store, bucket, done, security_run=security_run, matrix=matrix, editorial=editorial)


def _seed_extras(store, bucket: str, done: dict, *, security_run: Path | None, matrix: Path | None,
                 editorial: Path | None) -> dict:
    if editorial:
        store.put_json(bucket, EDITORIAL, read_json(editorial))
        done["editorial"] = True
    if matrix:
        mx = read_json(matrix)
        if not isinstance(mx, dict) or not isinstance(mx.get("items"), list):
            raise ValueError(f"{matrix} is not a security matrix (items list missing)")
        store.put_json(bucket, MATRIX, mx)
        done["matrix"] = True
    if security_run:
        sec = to_portal_security(read_json(security_run))
        run_id = _safe(sec["runId"], "security run id")
        store.put_json(bucket, f"portal/security/{run_id}.json", sec)
        store.put_json(bucket, SECURITY_POINTER, {"current": run_id})
        done["security_run_id"] = run_id
    log.info("seeded", extra=kv(**done))
    return done
