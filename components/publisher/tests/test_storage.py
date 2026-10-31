"""The storage interface behaves the same on MemoryStore and on S3 (moto), including the conditional writes publishing relies on
(read - compare - write under the publish lock, publisher.storage)."""

from __future__ import annotations

import pytest

from publisher.storage import Conflict, NotFound, StorageError


def test_put_get_list_delete(plane):
    store = plane.data
    assert store.list() == [] and not store.exists("a/b.json")
    etag1 = store.put("a/b.json", b'{"v": 1}', content_type="application/json")
    store.put("a/c.yaml", b"x: 1\n", content_type="text/yaml")
    store.put("z.json", b"{}", content_type="application/json")
    blob = store.get("a/b.json")
    assert blob.data == b'{"v": 1}' and blob.etag == etag1
    assert store.list() == ["a/b.json", "a/c.yaml", "z.json"]
    assert store.list("a/") == ["a/b.json", "a/c.yaml"]
    assert store.exists("z.json")
    etag2 = store.put("a/b.json", b'{"v": 2}', content_type="application/json")
    assert etag2 != etag1 and store.get("a/b.json").data == b'{"v": 2}'
    store.delete("z.json")
    store.delete("z.json")  # deleting what is not there is not an error
    assert store.list() == ["a/b.json", "a/c.yaml"]
    with pytest.raises(NotFound):
        store.get("z.json")


def test_a_create_only_write_never_replaces_an_existing_object(plane):
    store = plane.data
    store.put("releases/r1/release.json", b"one", if_none_match=True)
    with pytest.raises(Conflict):
        store.put("releases/r1/release.json", b"two", if_none_match=True)
    assert store.get("releases/r1/release.json").data == b"one"


def test_a_compare_and_swap_write_needs_the_version_that_was_read(plane):
    store = plane.data
    store.put("manifest.json", b"v1")
    seen = store.get("manifest.json").etag
    store.put("manifest.json", b"v2 by someone else")
    with pytest.raises(Conflict):
        store.put("manifest.json", b"v2 by me", if_match=seen)
    assert store.get("manifest.json").data == b"v2 by someone else"
    current = store.get("manifest.json").etag
    store.put("manifest.json", b"v3", if_match=current)
    store.put("manifest.json", b"v4", if_match='"%s"' % store.get("manifest.json").etag)  # quoted ETags are accepted too
    assert store.get("manifest.json").data == b"v4"


def test_a_compare_and_swap_on_a_missing_object_is_a_conflict_not_a_create(plane):
    with pytest.raises(Conflict):
        plane.data.put("manifest.json", b"x", if_match="abc123")
    assert not plane.data.exists("manifest.json")


@pytest.mark.parametrize("key", ["", "../x", "/abs", "a//b", ".hidden", "a/.hidden", "a/../b", "a b", "a/b/", "a\\b", "a\nb", "é.json"])
def test_unsafe_keys_are_refused_before_touching_storage(plane, key):
    for call in (lambda: plane.data.get(key), lambda: plane.data.put(key, b"x"), lambda: plane.data.exists(key), lambda: plane.data.delete(key)):
        with pytest.raises(StorageError):
            call()
    assert plane.data.list() == []


def test_s3_objects_carry_the_content_type(plane):
    if plane.kind != "s3":
        pytest.skip("S3 metadata")
    plane.data.put("manifest.json", b"{}", content_type="application/json", cache_control="no-cache")
    head = plane.store.client.head_object(Bucket="llmops-curated", Key="portal/manifest.json")
    assert head["ContentType"] == "application/json"


def test_stores_never_leave_their_prefix(plane):
    plane.data.put("x.json", b"{}")
    plane.control.put("rejected/p.json", b"{}")
    assert plane.data.list() == ["x.json"] and plane.control.list() == ["rejected/p.json"]
    assert plane.research.list() == []
    assert plane.data.uri("x.json") == "s3://llmops-curated/portal/x.json"


def test_the_version_token_is_the_content_hash_on_every_store(plane):
    """S3 ETags are not always the MD5 of the content (SSE-KMS, multipart, other S3-compatible stores): the publisher
    compares content hashes, which mean the same everywhere."""
    import hashlib

    etag = plane.data.put("manifest.json", b'{"current": "x"}')
    assert etag == hashlib.sha256(b'{"current": "x"}').hexdigest() == plane.data.get("manifest.json").etag


def test_a_store_needs_a_bucket_and_a_safe_prefix():
    from llmops_common.storage import MemoryStore

    from publisher.storage import PrefixStore

    for bucket, prefix in (("", "portal/"), ("b", "../x/"), ("b", "/abs/")):
        with pytest.raises(StorageError):
            PrefixStore(MemoryStore(), bucket, prefix)
    assert PrefixStore(MemoryStore(), "b", "portal").prefix == "portal/"


def test_failures_other_than_a_missing_object_are_not_mistaken_for_one():
    """An unreachable store or a denied read must surface: "missing" would look like "never published" (bootstrap)."""
    from publisher.storage import PrefixStore

    class Broken:
        def get_bytes(self, bucket, key):
            raise PermissionError("AccessDenied")

        def head(self, bucket, key):
            raise ConnectionError("store down")

    store = PrefixStore(Broken(), "b", "portal/")
    with pytest.raises(PermissionError):
        store.get("manifest.json")
    with pytest.raises(ConnectionError):
        store.exists("manifest.json")
