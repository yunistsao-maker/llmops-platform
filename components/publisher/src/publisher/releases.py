"""The layout of the published portal data (contracts/storage-layout.md, curated bucket), and the only way it changes.

    portal/manifest.json                     the pointer: {"current": <release id>, "history": [last 30], ...}
    portal/releases/<id>/release.json        {release_id, created_at, source_proposals, held} + what changed, where it came
                                             from, and the sha256 of every file
    portal/releases/<id>/schema/...          llmops.schema.json (the agent validates proposals against the live schema)
    portal/releases/<id>/data/...            catalog.json, lifecycle.json, radar.json, digests/*.json, models/*.json ...
    portal/releases/<id>/runs/...            test results people publish with the data (compat / perf)
    portal/releases/<id>/config/...          litellm.example.yaml

(Ported from llmops-research-agent pipeline/portal_pipeline/releases.py.  Changed for the platform: the pointer has the
contract's shape and keeps 30 releases in its history (no CDN index, no separate list of daily versions); release ids
are llmops_common.ids.new_release_id(); a release can be *held* - complete and immutable, but not live - until a person
approves the proposals in it.)

A release is immutable and is never deleted: each one stays the record of what it held.  Publishing writes a complete
new release and then replaces the pointer with one conditional write (under the publish lock, publisher.lock).  Readers
therefore see either the old data or the new data, never a mixture, and a rollback is a pointer change.  The pointer's
history and its list of held releases are short lists of names; dropping a name from them deletes nothing.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar

from research.portal.checks import loads_strict
from research.validate.injection import visible

from .storage import Conflict, NotFound, Store, check_key

MANIFEST_KEY = "manifest.json"
MANIFEST_SCHEMA = "portal-manifest/v2"  # the agent's loader (research.portal.data) follows {"current": id} pointers
RELEASE_SCHEMA = "portal-release/v2"
#: release.json written by the AWS pipeline (portal-release/v1) can still be read: a migrated data plane keeps its history
RELEASE_SCHEMAS = (RELEASE_SCHEMA, "portal-release/v1")
RELEASE_FILE = "release.json"
ROOTS = ("schema", "data", "runs", "config")
CONTENT_TYPES = {
    ".json": "application/json",
    ".yaml": "text/yaml; charset=utf-8",
    ".yml": "text/yaml; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
}
#: the UI's old build step wrote this index next to the data; the pointer replaces it
LEGACY_INDEX = "data/manifest.json"
IMMUTABLE = "public, max-age=31536000, immutable"
POINTER_CACHE = "no-cache"  # CDN and browsers must revalidate the pointer on every use
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_FILES = 2000
#: the pointer's history: the last 30 releases that were made live (contracts/storage-layout.md)
HISTORY_LIMIT = 30
#: held releases (complete, not live, waiting for a person) the pointer lists
HELD_LIMIT = 30
#: A release is hundreds of small objects; one at a time that is minutes of round trips, which a Lambda does not have.
IO_THREADS = 8  # below the default connection pool of a boto3 client (10)
AUTO_PUBLISH = "auto_publish"  # the pause flag lives in the pointer: reading it and switching the pointer are one conditional write
_AUTO_PUBLISH_V1 = "autoPublish"  # (the AWS pipeline's name for it)
_KEEP: Any = object()
T = TypeVar("T")
#: 20261031T061346Z (llmops_common.ids.new_release_id); the AWS pipeline's ids had a content hash: 20261005T032307Z-3f9a1c2b
_RELEASE_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z(?:-[0-9a-f]{8})?\Z")


class ReleaseError(Exception):
    """The data plane is not in a state this operation can work with."""


class NotBootstrapped(ReleaseError):
    """There is no manifest.json yet: publish the first release from a portal checkout (python -m publisher bootstrap)."""


class IntegrityError(ReleaseError):
    """A file of a release is missing or is not the file that was published."""


def dump(obj: Any) -> bytes:
    """Raises ValueError for NaN / Infinity: what is written must be JSON for every parser, not only Python's."""
    return (json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def each(store: Store, items: Iterable[T], work: Callable[[T], Any]) -> list[Any]:
    """``work(item)`` for every item, in order of the items - concurrently where the store is remote.

    The first exception is raised once every started call has finished, so nothing keeps writing in the background.
    """
    items = list(items)
    if len(items) < 2 or not getattr(store, "concurrent", False):
        return [work(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(IO_THREADS, len(items))) as pool:
        futures = [pool.submit(work, item) for item in items]
    return [f.result() for f in futures]


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def iso(now: dt.datetime) -> str:
    return now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def content_type(rel: str) -> str:
    return CONTENT_TYPES.get(os.path.splitext(rel)[1].lower(), "application/octet-stream")


def allowed(rel: str) -> bool:
    """May ``rel`` be part of a release?  (a known top-level folder, a text format the UI reads, a safe key)"""
    if rel == LEGACY_INDEX or rel.split("/")[0] not in ROOTS or os.path.splitext(rel)[1].lower() not in CONTENT_TYPES:
        return False
    try:
        check_key(rel)
    except Exception:
        return False
    return True


#: What is taken from a portal checkout: the files the UI reads, and nothing else.  Everything under releases/ can be
#: read by every user of the UI, and a checkout holds more than that - a real gateway config with keys next to the
#: example one, notes, exports.  ``*`` does not cross a "/".
PUBLISHABLE = (
    "schema/llmops.schema.json",
    "data/sources.json", "data/catalog.json", "data/lifecycle.json", "data/litellm.json", "data/requirements.json",
    "data/digests/*.json", "data/models/*.json", "data/open-models.json",
    "data/provider-compatibility.json",  # the documented LiteLLM provider matrix (people maintain it; both UI editions)
    "data/radar.json",  # the merged Release Radar (contracts/storage-layout.md lists it with the release data; not edited by the agent)
    "data/internal/platform-inventory*.json",
    "runs/compat/*.json", "runs/perf/*.json", "runs/security/*.json",
    "config/litellm.example.yaml",
)


def _glob(pattern: str) -> re.Pattern[str]:
    return re.compile("".join("[^/]*" if ch == "*" else re.escape(ch) for ch in pattern))


_PUBLISHABLE = [_glob(p) for p in PUBLISHABLE]


def publishable(rel: str, include: Iterable[str] = ()) -> bool:
    """Is ``rel`` one of the files a checkout contributes to a release?  ``include``: more patterns, named by a person."""
    return allowed(rel) and (any(p.fullmatch(rel) for p in _PUBLISHABLE) or any(_glob(p.strip("/")).fullmatch(rel) for p in include if p.strip("/")))


def is_release_id(value: Any) -> bool:
    return isinstance(value, str) and _RELEASE_ID.match(value) is not None


def check_release_id(release_id: str) -> str:
    if not isinstance(release_id, str) or not _RELEASE_ID.match(release_id):
        raise ReleaseError(f"not a release id: {release_id!r} (expected e.g. 20261031T061346Z)")
    return release_id


def release_prefix(release_id: str) -> str:
    return f"releases/{check_release_id(release_id)}/"


# ---------------------------------------------------------------------------------------------- trees
class Skipped(str):
    """A path that ``collect_tree`` left out.  It reads "<path> (<reason>)"; ``path`` and ``why`` are the two parts -
    for a program, which must not cut them out of that text again (a folder may be called "perf (copy)")."""

    path: str
    why: str

    def __new__(cls, path: str, why: str) -> "Skipped":
        self = super().__new__(cls, f"{path} ({why})")
        self.path, self.why = path, why
        return self


def collect_tree(root: str | os.PathLike, roots: Iterable[str] = ROOTS, *, include: Iterable[str] = (),
                 validate: bool = True) -> tuple[dict[str, bytes], list[Skipped]]:
    """The files of a portal checkout that belong in a release, and the paths that were left out (with the reason).

    Only what the UI reads is taken (``PUBLISHABLE``, plus the patterns in ``include``); symbolic links are never
    followed.  With ``validate`` every ``.json`` file is parsed first, so an invalid file stops a publish instead of
    reaching the UI (a caller that only compares files - pull - turns that off).
    """
    root = Path(root)
    include = list(include)
    files: dict[str, bytes] = {}
    skipped: list[Skipped] = []
    for top in roots:
        base = root / top
        if base.is_symlink():
            skipped.append(Skipped(f"{top}/", "symlinked folder"))
            continue
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            rel = path.relative_to(root).as_posix()
            parts = rel.split("/")
            if any(p.startswith(".") or p in ("node_modules", "__pycache__") for p in parts):
                continue
            if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if root in parent.parents):
                skipped.append(Skipped(rel, "symlink"))
                continue
            if not path.is_file() or rel == LEGACY_INDEX:
                continue
            if not allowed(rel):
                skipped.append(Skipped(rel, "not a publishable file name or type"))
                continue
            if not publishable(rel, include):
                skipped.append(Skipped(rel, "not a file the UI reads"))
                continue
            raw = path.read_bytes()
            if len(raw) > MAX_FILE_BYTES:
                raise ReleaseError(f"{rel} is {len(raw)} bytes; the limit is {MAX_FILE_BYTES}")
            if validate and rel.endswith(".json"):
                try:
                    loads_strict(raw)  # NaN / Infinity / 1e999 are not JSON for a browser
                except ValueError as e:
                    raise ReleaseError(f"{rel} is not valid JSON: {e}") from e
            files[rel] = raw
            if len(files) > MAX_FILES:
                raise ReleaseError(f"more than {MAX_FILES} files under {root}: is this really a portal checkout?")
    return files, skipped


def tree_digest(files: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(rel.encode("utf-8") + b"\0" + sha256_hex(files[rel]).encode("ascii") + b"\n")
    return h.hexdigest()


def new_release_id(store: Store, now: dt.datetime) -> str:
    """A fresh release id for ``now`` (llmops_common.ids.new_release_id: 20261031T061346Z).  Ids are names of moments, so
    two releases made within one second (a published release and the held one built on it) get consecutive seconds:
    an id whose release.json exists is never reused."""
    from llmops_common.ids import new_release_id as stamp

    when = now.astimezone(dt.timezone.utc).replace(microsecond=0)
    for _ in range(600):
        release_id = stamp(when)
        if not store.exists(release_prefix(release_id) + RELEASE_FILE):
            return release_id
        when += dt.timedelta(seconds=1)
    raise ReleaseError("no free release id within ten minutes of now")


def diff(old: dict[str, bytes], new: dict[str, bytes]) -> dict[str, list[str]]:
    return {
        "added": sorted(k for k in new if k not in old),
        "modified": sorted(k for k in new if k in old and old[k] != new[k]),
        "removed": sorted(k for k in old if k not in new),
    }


def changed_count(changes: dict[str, list[str]]) -> int:
    return sum(len(changes.get(k, [])) for k in ("added", "modified", "removed"))


# ---------------------------------------------------------------------------------------------- reading
@dataclass
class Current:
    """The live release as one consistent snapshot."""

    manifest: dict
    etag: str  # of manifest.json: publishing replaces the pointer only if it still has this version
    meta: dict
    files: dict[str, bytes]

    @property
    def release_id(self) -> str:
        return self.manifest["current"]


def read_pointer(store: Store) -> tuple[dict, str]:
    try:
        blob = store.get(MANIFEST_KEY)
    except NotFound as e:
        raise NotBootstrapped(f"{store.uri(MANIFEST_KEY)} does not exist yet: publish the first release with "
                              "`python -m publisher bootstrap --from <portal checkout>`") from e
    try:
        manifest = json.loads(blob.data)
    except ValueError as e:
        raise ReleaseError(f"{store.uri(MANIFEST_KEY)} is not valid JSON: {e}") from e
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        raise ReleaseError(f"{store.uri(MANIFEST_KEY)} is not a release pointer (schema {MANIFEST_SCHEMA})")
    check_release_id(manifest.get("current"))
    return manifest, blob.etag


def release_sha(store: Store, release_id: str) -> str:
    """The sha256 of a release's release.json as it is stored (what the pointer records about the release it names)."""
    prefix = release_prefix(release_id)
    try:
        return sha256_hex(store.get(prefix + RELEASE_FILE).data)
    except NotFound as e:
        raise ReleaseError(f"release {release_id} does not exist at {store.uri(prefix)}") from e


def read_release(store: Store, release_id: str, *, verify: bool = True, expect_sha: str | None = None) -> tuple[dict, dict[str, bytes]]:
    """(release.json, files).  With ``verify`` every file is checked against the hash recorded when it was published;
    ``expect_sha`` is the hash the pointer recorded for release.json itself, so that the list of hashes cannot have
    been rewritten together with a file."""
    prefix = release_prefix(release_id)
    try:
        body = store.get(prefix + RELEASE_FILE).data
        meta = json.loads(body)
    except NotFound as e:
        raise ReleaseError(f"release {release_id} does not exist at {store.uri(prefix)}") from e
    except ValueError as e:
        raise IntegrityError(f"{store.uri(prefix + RELEASE_FILE)} is not valid JSON: {e}") from e
    if verify and expect_sha and sha256_hex(body) != expect_sha:
        raise IntegrityError(f"release {release_id}: {RELEASE_FILE} is not the one that was published (hash mismatch)")
    listed = meta.get("files") if isinstance(meta, dict) else None
    if not isinstance(listed, dict) or meta.get("schema") not in RELEASE_SCHEMAS:
        raise IntegrityError(f"{store.uri(prefix + RELEASE_FILE)} is not a release description")
    for rel in listed:
        if not allowed(rel):
            raise IntegrityError(f"release {release_id} lists a file that cannot be part of a release: {rel!r}")

    def fetch(rel: str) -> bytes:
        try:
            data = store.get(prefix + rel).data
        except NotFound as e:
            raise IntegrityError(f"release {release_id} is incomplete: {rel} is missing") from e
        info = listed[rel]
        if verify and sha256_hex(data) != (info.get("sha256") if isinstance(info, dict) else None):
            raise IntegrityError(f"release {release_id}: {rel} is not the file that was published (hash mismatch)")
        return data

    names = sorted(listed)
    return meta, dict(zip(names, each(store, names, fetch)))


def read_meta(store: Store, release_id: str) -> dict:
    """release.json of a release (what it was made from, what it changed) without its files."""
    prefix = release_prefix(release_id)
    try:
        meta = json.loads(store.get(prefix + RELEASE_FILE).data)
    except NotFound as e:
        raise ReleaseError(f"release {release_id} does not exist at {store.uri(prefix)}") from e
    except ValueError as e:
        raise IntegrityError(f"{store.uri(prefix + RELEASE_FILE)} is not valid JSON: {e}") from e
    if not isinstance(meta, dict) or meta.get("schema") not in RELEASE_SCHEMAS:
        raise IntegrityError(f"{store.uri(prefix + RELEASE_FILE)} is not a release description")
    return meta


def load_current(store: Store) -> Current:
    manifest, etag = read_pointer(store)
    pinned = manifest.get("release_sha256")
    meta, files = read_release(store, manifest["current"], expect_sha=pinned if isinstance(pinned, str) else None)
    return Current(manifest, etag, meta, files)


def list_release_ids(store: Store) -> list[str]:
    ids = set()
    for key in store.list("releases/"):
        parts = key.split("/")
        if len(parts) == 3 and parts[2] == RELEASE_FILE and _RELEASE_ID.match(parts[1]):
            ids.add(parts[1])
    return sorted(ids)


# ---------------------------------------------------------------------------------------------- writing
def release_meta(release_id: str, files: dict[str, bytes], *, created_at: str, parent: str | None, source: dict,
                 changes: dict[str, list[str]], source_proposals: Iterable[str] = (), held: bool = False,
                 details: dict | None = None) -> dict:
    """release.json: the contract's fields first ({release_id, created_at, source_proposals, held}), then the record."""
    meta: dict[str, Any] = {
        "schema": RELEASE_SCHEMA, "release_id": release_id, "created_at": created_at, "source_proposals": list(source_proposals),
        "held": bool(held), "parent": parent, "source": source, "changes": changes,
    }
    if details:
        meta.update(details)
    meta["files"] = {rel: {"sha256": sha256_hex(data), "bytes": len(data)} for rel, data in sorted(files.items())}
    return meta


def write_release(store: Store, release_id: str, files: dict[str, bytes], meta: dict) -> str:
    """Upload a complete release.  ``release.json`` goes last and only if the id is unused.

    Returns the sha256 of the release.json that is stored (for the pointer to record)."""
    prefix = release_prefix(release_id)
    for rel in files:
        if not allowed(rel):
            raise ReleaseError(f"{rel!r} cannot be part of a release")
    if not any(rel.startswith("data/") for rel in files) or "schema/llmops.schema.json" not in files:
        raise ReleaseError("a release needs schema/llmops.schema.json and at least one file under data/")
    body = dump(meta)

    def create(rel: str) -> None:
        # Never replace an object under releases/: a release that was published once stays what it was.  Finding the
        # very same bytes there is fine (an earlier attempt at this release got that far).
        try:
            store.put(prefix + rel, files[rel], content_type=content_type(rel), cache_control=IMMUTABLE, if_none_match=True)
        except Conflict:
            if store.get(prefix + rel).data != files[rel]:
                raise ReleaseError(f"release id {release_id} is already used by different content ({rel})") from None

    each(store, sorted(files), create)
    try:
        store.put(prefix + RELEASE_FILE, body, content_type="application/json", cache_control=IMMUTABLE, if_none_match=True)
    except Conflict:
        stored = store.get(prefix + RELEASE_FILE).data
        try:
            existing = json.loads(stored)
        except ValueError:
            existing = None
        if not isinstance(existing, dict) or existing.get("files") != meta["files"]:  # same id, different content
            raise ReleaseError(f"release id {release_id} is already used by different content") from None
        return sha256_hex(stored)  # an earlier attempt at this very release wrote it: that one is the record
    return sha256_hex(body)


def history_entry(release_id: str, *, published_at: str, source: dict, summary: str, changes: dict[str, list[str]],
                  previous: str | None = None, source_proposals: Iterable[str] = ()) -> dict:
    entry: dict[str, Any] = {"release_id": release_id, "published_at": published_at, "previous_release_id": previous,
                             "kind": source.get("kind", "manual"), "by": str(source.get("by", ""))[:80],
                             "summary": " ".join(visible(str(summary)).split())[:400], "changed": changed_count(changes)}
    proposals = list(source_proposals)
    if proposals:
        entry["source_proposals"] = proposals[:50]
    for key in ("trigger", "from", "approved_from", "request"):
        if source.get(key):
            entry[key] = source[key]
    return entry


def build_manifest(release_id: str, *, published_at: str, source: dict, previous: dict | None, entry: dict | None,
                   auto_publish: Any = _KEEP, release_sha256: str | None = None, held: Any = _KEEP) -> dict:
    """The pointer for ``release_id``; ``previous`` is the manifest it replaces (None for the first release).

    ``entry`` (the history entry of this switch) is None when the live release does not change (a held release was added).
    The pause flag and the list of held releases of ``previous`` are kept unless ``auto_publish`` / ``held`` say otherwise:
    publishing something by hand must not quietly switch automatic publishing back on.
    """
    prev = previous or {}
    earlier = prev.get("history")
    history = ([entry] if entry else []) + [h for h in (earlier if isinstance(earlier, list) else []) if isinstance(h, dict)]
    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "current": release_id,
        "published_at": published_at,
        "sequence": _sequence(previous) + (1 if entry else 0),
        "previous": prev.get("current") if entry else prev.get("previous"),
        "source": source,
    }
    if release_sha256:
        manifest["release_sha256"] = release_sha256  # of release.json: pins the list of file hashes to this pointer
    flag = (prev.get(AUTO_PUBLISH, prev.get(_AUTO_PUBLISH_V1)) if auto_publish is _KEEP else auto_publish)
    if flag is not None:
        manifest[AUTO_PUBLISH] = flag
    manifest["history"] = history[:HISTORY_LIMIT]
    waiting = held_of(prev) if held is _KEEP else list(held)
    manifest["held"] = waiting[:HELD_LIMIT]
    return manifest


def held_of(manifest: dict | None) -> list[dict]:
    """The held releases the pointer lists, newest first: {release_id, created_at, base, source_proposals, reasons, trigger}."""
    listed = (manifest or {}).get("held")
    return [h for h in (listed if isinstance(listed, list) else []) if isinstance(h, dict) and is_release_id(h.get("release_id"))]


def held_entry(release_id: str, *, created_at: str, base: str, source_proposals: Iterable[str], reasons: Iterable[str],
               trigger: str | None = None, proposals: Iterable[dict] = ()) -> dict:
    """A held release as the pointer lists it.  ``proposals``: [{id, sha256, key, reasons}] - what approve replays."""
    entry: dict[str, Any] = {"release_id": release_id, "created_at": created_at, "base": base,
                             "source_proposals": list(source_proposals)[:50],
                             "reasons": [" ".join(visible(str(r)).split())[:300] for r in list(reasons)[:12]],
                             "proposals": [{"id": str(p.get("id") or "")[:100], "sha256": str(p.get("sha256") or "")[:64],
                                            "key": str(p.get("key") or "")[:200],
                                            "reasons": [" ".join(visible(str(r)).split())[:300] for r in list(p.get("reasons") or [])[:6]]}
                                           for p in list(proposals)[:50] if isinstance(p, dict)]}
    if trigger:
        entry["trigger"] = trigger
    return entry


def _sequence(manifest: dict | None) -> int:
    value = (manifest or {}).get("sequence", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def hold_of(manifest: dict) -> dict | None:
    """Who paused automatic publishing, when and why - None when it is not paused.

    Anything that is there and does not plainly say "not paused" counts as paused: a damaged flag must never switch
    automatic publishing back on.
    """
    flag = manifest.get(AUTO_PUBLISH, manifest.get(_AUTO_PUBLISH_V1))
    if flag is None or (isinstance(flag, dict) and flag.get("paused") is False):
        return None
    info = flag if isinstance(flag, dict) else {}

    def line(value: Any, limit: int) -> str:  # whoever wrote the flag: what is read from it is one printable line
        return " ".join(visible(str(value)).split())[:limit]

    return {"by": line(info.get("by") or "unknown", 80), "at": line(info.get("at") or "unknown", 40), "reason": line(info.get("reason") or "", 300)}


def was_live(manifest: dict, release_id: str) -> bool:
    """Has ``release_id`` ever been the live release, as far as this pointer remembers (its history)?"""
    if manifest.get("current") == release_id or manifest.get("previous") == release_id:
        return True
    history = manifest.get("history")
    return any(isinstance(h, dict) and (h.get("release_id") == release_id or h.get("from") == release_id)
               for h in (history if isinstance(history, list) else []))


def switch_pointer(store: Store, manifest: dict, expected_etag: str | None) -> str:
    """Make ``manifest`` live.  Raises ``Conflict`` when the pointer is no longer the one that was read
    (``expected_etag``), or - for the first release (``expected_etag`` None) - when one already exists."""
    return store.put(MANIFEST_KEY, dump(manifest), content_type="application/json", cache_control=POINTER_CACHE,
                     if_match=expected_etag, if_none_match=expected_etag is None)
