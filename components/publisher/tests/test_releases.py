"""The release layout: what goes into a release, what the pointer says, and what a reader can rely on.

(Ported from llmops-research-agent pipeline/tests/test_releases.py; the pointer now has the contract's shape -
{"current", "history": [30]} - and release ids are llmops_common.ids.new_release_id().)"""

from __future__ import annotations

import json

import pytest
from conftest import T0, T1, snapshot_tree, write_tree

from publisher import releases as R
from publisher.storage import Conflict


# ---------------------------------------------------------------------------------------------- what is publishable
def test_only_publishable_files_of_a_checkout_are_collected(tmp_path):
    files = dict(snapshot_tree())
    files.update({
        "data/manifest.json": b'{"legacy": true}',  # the old build-time index: replaced by the pointer
        "data/.DS_Store": b"junk", "data/notes.docx": b"binary", "runs/compat/node_modules/x.json": b"{}",
        "web/src/data.ts": b"export {}", "README.md": b"# portal", "tools/validate_data.py": b"print()",
        "config/has space.yaml": b"a: 1\n",
    })
    write_tree(tmp_path, files)
    (tmp_path / "data" / "link.json").symlink_to(tmp_path / "data" / "catalog.json")
    collected, skipped = R.collect_tree(tmp_path)
    assert collected == snapshot_tree()
    assert sorted(s.split(" ")[0] for s in skipped) == ["config/has", "data/link.json", "data/notes.docx"]


def test_an_invalid_json_file_stops_the_collection(tmp_path):
    write_tree(tmp_path, {**snapshot_tree(), "data/digests/2026-10-05.json": b'{"id": "2026-10-05",'})
    with pytest.raises(R.ReleaseError, match="data/digests/2026-10-05.json is not valid JSON"):
        R.collect_tree(tmp_path)


def test_a_subset_of_folders_can_be_collected(tmp_path):
    write_tree(tmp_path, snapshot_tree())
    collected, _ = R.collect_tree(tmp_path, roots=["runs", "config"])
    assert sorted(collected) == ["config/litellm.example.yaml", "runs/compat/example-mock.json", "runs/perf/example-mock.json"]


@pytest.mark.parametrize("rel, ok", [
    ("data/catalog.json", True), ("data/internal/platform-inventory.json", True), ("config/litellm.example.yaml", True),
    ("schema/llmops.schema.json", True), ("runs/perf/2026-10-01-opus55.json", True), ("data/radar.json", True),
    ("data/manifest.json", False), ("web/index.html", False), ("data/x.exe", False), ("data/../secrets.json", False),
    ("release.json", False), ("data/a b.json", False),
])
def test_what_may_be_part_of_a_release(rel, ok):
    assert R.allowed(rel) is ok


# ---------------------------------------------------------------------------------------------- write / read
def _publish(store, files, now=T0, previous=None, etag=None):
    release_id = R.new_release_id(store, now)
    changes = R.diff({}, files)
    meta = R.release_meta(release_id, files, created_at=R.iso(now), parent=None, source={"kind": "bootstrap", "by": "t"}, changes=changes)
    pinned = R.write_release(store, release_id, files, meta)
    entry = R.history_entry(release_id, published_at=R.iso(now), source={"kind": "bootstrap", "by": "t"}, summary="first", changes=changes)
    manifest = R.build_manifest(release_id, published_at=R.iso(now), source={"kind": "bootstrap", "by": "t"}, previous=previous, entry=entry,
                                release_sha256=pinned)
    R.switch_pointer(store, manifest, etag)
    return release_id, manifest


def test_a_release_round_trips_and_the_pointer_describes_it(plane):
    files = snapshot_tree()
    release_id, manifest = _publish(plane.data, files)
    assert release_id == "20261002T010000Z" and R.check_release_id(release_id)  # llmops_common.ids.new_release_id
    current = R.load_current(plane.data)
    assert current.files == files and current.release_id == release_id and current.manifest == manifest
    # the contract's pointer: {"current": release_id, "history": [last 30]} (plus what the publisher needs)
    assert manifest["schema"] == "portal-manifest/v2" and manifest["current"] == release_id
    assert manifest["sequence"] == 1 and manifest["previous"] is None and manifest["held"] == []
    assert manifest["published_at"] == "2026-10-02T01:00:00Z"
    assert manifest["history"][0]["release_id"] == release_id and manifest["history"][0]["changed"] == len(files)
    # the contract's release.json: {release_id, created_at, source_proposals, held} first
    assert list(current.meta)[:5] == ["schema", "release_id", "created_at", "source_proposals", "held"]
    assert current.meta["release_id"] == release_id and current.meta["held"] is False and current.meta["source_proposals"] == []
    assert current.meta["files"]["data/catalog.json"] == {"sha256": R.sha256_hex(files["data/catalog.json"]), "bytes": len(files["data/catalog.json"])}
    assert R.list_release_ids(plane.data) == [release_id]
    assert plane.data.list("releases/")[0].startswith(f"releases/{release_id}/")


def test_the_keys_are_the_storage_contract_s(plane):
    """contracts/storage-layout.md: portal/releases/{release_id}/data/*.json, .../release.json, portal/manifest.json."""
    release_id, _ = _publish(plane.data, snapshot_tree())
    keys = {o.key for o in plane.store.list("llmops-curated")}
    assert "portal/manifest.json" in keys and f"portal/releases/{release_id}/release.json" in keys
    assert f"portal/releases/{release_id}/data/catalog.json" in keys and f"portal/releases/{release_id}/schema/llmops.schema.json" in keys
    assert all(k.startswith("portal/") for k in keys)


def test_reading_before_the_first_release_says_what_to_do(plane):
    with pytest.raises(R.NotBootstrapped, match="python -m publisher bootstrap"):
        R.load_current(plane.data)


def test_a_tampered_or_incomplete_release_is_detected(plane):
    release_id, _ = _publish(plane.data, snapshot_tree())
    key = f"releases/{release_id}/data/lifecycle.json"
    original = plane.data.get(key).data
    plane.data.put(key, original.replace(b"2026", b"2036", 1))
    with pytest.raises(R.IntegrityError, match="hash mismatch"):
        R.load_current(plane.data)
    plane.data.delete(key)
    with pytest.raises(R.IntegrityError, match="lifecycle.json is missing"):
        R.load_current(plane.data)


def test_the_first_pointer_is_create_only_and_later_ones_compare_and_swap(plane):
    files = snapshot_tree()
    _, manifest = _publish(plane.data, files)
    with pytest.raises(Conflict):  # a second "first release" must not replace a live pointer
        R.switch_pointer(plane.data, manifest, None)
    _, etag = R.read_pointer(plane.data)
    files2 = {**files, "runs/perf/new.json": b"{}"}
    _publish(plane.data, files2, now=T1, previous=manifest, etag=etag)
    with pytest.raises(Conflict):  # the etag that was read is stale now
        R.switch_pointer(plane.data, manifest, etag)
    assert R.load_current(plane.data).files == files2 and R.load_current(plane.data).manifest["sequence"] == 2


def test_a_release_id_is_never_reused_for_different_content(plane):
    files = snapshot_tree()
    release_id = R.new_release_id(plane.data, T0)
    meta = R.release_meta(release_id, files, created_at=R.iso(T0), parent=None, source={"kind": "manual"}, changes={})
    R.write_release(plane.data, release_id, files, meta)
    R.write_release(plane.data, release_id, files, meta)  # the same release again (a retried upload) is fine
    other = {**files, "runs/perf/new.json": b"{}"}
    with pytest.raises(R.ReleaseError, match="already used by different content"):
        R.write_release(plane.data, release_id, other, R.release_meta(release_id, other, created_at=R.iso(T0), parent=None, source={}, changes={}))


def test_a_published_release_is_never_written_over(plane):
    """Not even by the attempt that is then refused: files used to be replaced before the id was found to be taken."""
    files = snapshot_tree()
    release_id = R.new_release_id(plane.data, T0)
    meta = R.release_meta(release_id, files, created_at=R.iso(T0), parent=None, source={"kind": "manual"}, changes={})
    pinned = R.write_release(plane.data, release_id, files, meta)
    assert pinned == R.sha256_hex(plane.data.get(f"releases/{release_id}/release.json").data) == R.release_sha(plane.data, release_id)
    changed = {**files, "data/catalog.json": b'{"models": []}\n'}
    with pytest.raises(R.ReleaseError, match="already used by different content"):
        R.write_release(plane.data, release_id, changed, R.release_meta(release_id, changed, created_at=R.iso(T0), parent=None, source={}, changes={}))
    assert R.read_release(plane.data, release_id, expect_sha=pinned)[1] == files  # every byte of the first one is still there
    # a retried upload of the very same release reports the record that is stored, whoever wrote it
    again = R.release_meta(release_id, files, created_at=R.iso(T1), parent=None, source={"kind": "manual", "by": "someone else"}, changes={})
    assert R.write_release(plane.data, release_id, files, again) == pinned


def test_the_pointer_pins_the_record_of_the_release_it_names(plane):
    files = snapshot_tree()
    plane.bootstrap()
    current = R.load_current(plane.data)
    assert current.manifest["release_sha256"] == R.release_sha(plane.data, current.release_id)
    key = f"releases/{current.release_id}/release.json"
    meta = json.loads(plane.data.get(key).data)
    meta["source"] = {"kind": "manual", "by": "somebody who edited the bucket"}
    plane.data.put(key, R.dump(meta))
    with pytest.raises(R.IntegrityError, match="release.json is not the one that was published"):
        R.load_current(plane.data)
    assert R.read_release(plane.data, current.release_id)[1] == files  # (without the pin the edit goes unnoticed)


def test_the_pause_flag_and_held_releases_travel_with_the_pointer_unless_changed_on_purpose():
    entry = R.history_entry("20261002T010000Z", published_at="2026-10-02T01:00:00Z", source={"kind": "manual"}, summary="s", changes={})
    kw = dict(published_at="2026-10-02T01:00:00Z", source={"kind": "manual"}, entry=entry)
    first = R.build_manifest("20261002T010000Z", previous=None, **kw)
    assert "auto_publish" not in first and R.hold_of(first) is None
    held = R.held_entry("20261002T020000Z", created_at="2026-10-02T02:00:00Z", base="20261002T010000Z",
                        source_proposals=["research/proposals/prop-a.json"], reasons=["發布模式是 review"], trigger="event:7")
    paused = {**first, "auto_publish": {"paused": True, "by": "eunice", "at": "2026-10-02T02:00:00Z", "reason": "連假"}, "held": [held]}
    kept = R.build_manifest("20261002T010000Z", previous=paused, **kw)
    assert R.hold_of(kept) == {"by": "eunice", "at": "2026-10-02T02:00:00Z", "reason": "連假"} and kept["sequence"] == 2
    assert R.held_of(kept) == [held]
    lifted = R.build_manifest("20261002T010000Z", previous=paused, auto_publish={"paused": False}, held=[], **kw)
    assert R.hold_of(lifted) is None and lifted["held"] == []
    # the AWS pipeline's flag name is still honoured: a pause must never be lost in a migration
    assert R.hold_of({"autoPublish": {"paused": True, "by": "x", "at": "t"}}) == {"by": "x", "at": "t", "reason": ""}
    for odd in ({"sequence": "7"}, {"sequence": True}, {"sequence": -3}, {"history": "x"}, {"history": [1, "x"]}, {"held": "x"}):
        m = R.build_manifest("20261002T010000Z", previous=odd, **kw)
        assert m["sequence"] == 1 and m["held"] == []


def test_a_pointer_change_that_is_not_a_publish_keeps_the_live_release_and_its_place_in_history():
    entry = R.history_entry("20261002T010000Z", published_at="2026-10-02T01:00:00Z", source={"kind": "bootstrap"}, summary="s", changes={})
    first = R.build_manifest("20261002T010000Z", published_at="2026-10-02T01:00:00Z", source={"kind": "bootstrap"}, previous=None, entry=entry,
                             release_sha256="ab" * 32)
    held = R.held_entry("20261002T030000Z", created_at="t", base="20261002T010000Z", source_proposals=[], reasons=[])
    same = R.build_manifest("20261002T010000Z", published_at=first["published_at"], source=first["source"], previous=first, entry=None,
                            release_sha256=first["release_sha256"], held=[held])
    assert same["sequence"] == first["sequence"] and same["history"] == first["history"] and same["previous"] is None
    assert same["release_sha256"] == "ab" * 32 and R.held_of(same) == [held]


def test_reads_and_writes_of_a_release_survive_a_failing_file(plane):
    """Files are handled concurrently on S3: an error in one of them must surface, not vanish in a worker thread."""

    class OneBadFile:
        concurrent = True

        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def put(self, key, data, **kw):
            if key.endswith("data/lifecycle.json"):
                raise RuntimeError("simulated network error")
            return self._inner.put(key, data, **kw)

    files = snapshot_tree()
    release_id = R.new_release_id(plane.data, T0)
    meta = R.release_meta(release_id, files, created_at=R.iso(T0), parent=None, source={"kind": "manual"}, changes={})
    with pytest.raises(RuntimeError, match="simulated network error"):
        R.write_release(OneBadFile(plane.data), release_id, files, meta)
    assert not plane.data.exists(f"releases/{release_id}/release.json")  # an incomplete release never gets its record
    R.write_release(plane.data, release_id, files, meta)  # the retry completes it
    assert R.read_release(plane.data, release_id)[1] == files


def test_a_release_needs_the_schema_and_data(plane):
    for files in ({"data/catalog.json": b"{}"}, {"schema/llmops.schema.json": b"{}"}):
        with pytest.raises(R.ReleaseError, match="needs schema/llmops.schema.json"):
            R.write_release(plane.data, R.new_release_id(plane.data, T0), files, {"files": {}})
    with pytest.raises(R.ReleaseError, match="cannot be part of a release"):
        R.write_release(plane.data, R.new_release_id(plane.data, T0), {**snapshot_tree(), "web/x.html": b""}, {"files": {}})


@pytest.mark.parametrize("pointer", [
    b"not json", b"[]", b'{"schema": "something-else"}',
    b'{"schema": "portal-manifest/v2", "current": "../../etc"}', b'{"schema": "portal-manifest/v2"}',
    b'{"schema": "portal-manifest/v2", "current": "latest"}', b'{"current": "20261002T010000Z"}',
])
def test_a_malformed_pointer_is_an_error_not_a_guess(plane, pointer):
    plane.data.put("manifest.json", pointer)
    with pytest.raises(R.ReleaseError):
        R.read_pointer(plane.data)


def test_release_ids_are_moments_and_never_reused(plane):
    a = R.new_release_id(plane.data, T0)
    assert a == "20261002T010000Z" and a < R.new_release_id(plane.data, T1)
    files = snapshot_tree()
    R.write_release(plane.data, a, files, R.release_meta(a, files, created_at=R.iso(T0), parent=None, source={}, changes={}))
    assert R.new_release_id(plane.data, T0) == "20261002T010001Z"  # taken: the next second
    for good in ("20261002T010000Z", "20261005T032307Z-3f9a1c2b"):  # (the AWS pipeline's ids can still be read)
        assert R.check_release_id(good) == good
    for bad in ("", "latest", "../x", "20261002T010000", "20261002T010000Z-XYZ", "20261002T010000Z-", None):
        with pytest.raises(R.ReleaseError):
            R.check_release_id(bad)


def test_history_keeps_the_last_thirty_releases(plane):
    previous = {"history": [{"release_id": f"old-{i}"} for i in range(200)], "sequence": 200, "current": "old-0"}
    entry = R.history_entry("20261002T010000Z", published_at="t", source={"kind": "agent", "by": "publisher", "trigger": "event:9"},
                            summary="  多行\n摘要  " * 80, changes={"modified": ["data/lifecycle.json"]}, previous="old-0",
                            source_proposals=["research/proposals/prop-1.json"])
    manifest = R.build_manifest("20261002T010000Z", published_at="t", source={"kind": "agent"}, previous=previous, entry=entry)
    assert R.HISTORY_LIMIT == 30 and len(manifest["history"]) == 30 and manifest["history"][0] is entry and manifest["sequence"] == 201
    assert len(entry["summary"]) <= 400 and "\n" not in entry["summary"] and entry["changed"] == 1 and entry["trigger"] == "event:9"
    assert entry["previous_release_id"] == "old-0" and entry["source_proposals"] == ["research/proposals/prop-1.json"]
    assert manifest["previous"] == "old-0"
    assert len(json.dumps(manifest)) < 60_000  # the pointer stays small: every reader downloads it


def test_security_test_summaries_are_published_with_the_other_test_results():
    assert R.publishable("runs/security/20261012T215504-494d13.json")
    assert not R.publishable("runs/security/nested/x.json") and not R.publishable("runs/secsuite-cache/x.json")


def test_the_radar_travels_with_the_release_data():
    """contracts/storage-layout.md lists radar among a release's data files: a checkout's data/radar.json is published."""
    assert R.publishable("data/radar.json") and not R.publishable("radar/curation.json")
