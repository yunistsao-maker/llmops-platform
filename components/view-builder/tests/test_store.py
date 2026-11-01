import datetime as dt
import json

import pytest

from conftest import EDITORIAL, MATRIX, SECURITY_RUN, without_additions
from llmops_common.queue import Job, PermanentError
from llmops_common.storage import MemoryStore
from view_builder import store_io
from view_builder.store_io import (EDITORIAL as EDITORIAL_KEY, MANIFEST, VIEW_POINTER, NoRelease, build_and_publish,
                                   load_inputs, prune_views, seed, view_key)
from view_builder.worker import Builder, portal_tz

B = "llmops-curated"
T0 = dt.datetime(2026, 10, 31, 6, 0, 0, tzinfo=dt.timezone.utc)


def test_seed_then_build_real_data(portal_data, fixture_view):
    store = MemoryStore()
    done = seed(store, B, portal_data, security_run=SECURITY_RUN, matrix=MATRIX, editorial=EDITORIAL,
                release_id="20261031T000000Z", now=T0)
    assert done["release_id"] == "20261031T000000Z" and done["security_run_id"] == "20261012T151346-a55616"
    assert store.get_json(B, MANIFEST) == {"current": "20261031T000000Z", "history": ["20261031T000000Z"]}
    rel = store.get_json(B, "portal/releases/20261031T000000Z/release.json")
    assert rel["held"] is False and rel["source"] == "yunistsao-maker/llmops-portal@8f56627"
    keys = [o.key for o in store.list(B, "portal/releases/20261031T000000Z/data/")]
    assert any(k.endswith("/data/models/claude-opus-5-5.json") for k in keys)
    assert any(k.endswith("/data/digests/2026-10-24.json") for k in keys)
    assert not any("/internal/" in k for k in keys)
    assert store.get_json(B, "portal/security/current.json") == {"current": "20261012T151346-a55616"}

    res = build_and_publish(store, B, today=dt.date(2026, 10, 30), now=T0)
    assert res.changed and res.release_id == "20261031T000000Z"
    pointer = store.get_json(B, VIEW_POINTER)
    assert pointer == {"current": res.view_id, "release_id": "20261031T000000Z",
                       "security_run_id": "20261012T151346-a55616", "built_at": "2026-10-31T06:00:00Z"}
    view = store.get_json(B, view_key(res.view_id))
    assert without_additions(view) == fixture_view  # the store path gives the same result as the offline compile
    assert view["gw"]["recommended"] == "v1.105.2"  # litellm.json (optional file) is read from the release
    # same inputs again: nothing new is written
    again = build_and_publish(store, B, today=dt.date(2026, 10, 30), now=T0 + dt.timedelta(minutes=5))
    assert again.view_id == res.view_id and not again.changed
    with pytest.raises(FileExistsError):
        seed(store, B, portal_data, security_run=None, matrix=None, editorial=None)


def test_store_build_cycle_and_events(mini_data, tmp_path):
    store = MemoryStore()
    with pytest.raises(NoRelease):
        load_inputs(store, B)
    seed(store, B, mini_data, security_run=None, matrix=MATRIX, editorial=None, release_id="r1", source="mini", now=T0)
    assert not any("/internal/" in o.key for o in store.list(B, "portal/releases/"))
    first = build_and_publish(store, B, today=dt.date(2026, 10, 30), now=T0)
    view = store.get_json(B, view_key(first.view_id))
    assert view["repo"] == "mini" and view["security"]["items"] == [] and len(view["secMatrix"]["items"]) == 32
    assert store.get_json(B, VIEW_POINTER)["security_run_id"] is None

    # editorial.updated: a new overlay changes the content -> new view id and pointer
    store.put_json(B, EDITORIAL_KEY, {"brief": [{"t": "新的摘要", "p": "P1"}]})
    second = build_and_publish(store, B, today=dt.date(2026, 10, 30), now=T0 + dt.timedelta(hours=1))
    assert second.changed and second.view_id != first.view_id
    assert store.get_json(B, view_key(second.view_id))["brief"] == [{"t": "新的摘要", "p": "P1"}]
    assert store.exists(B, view_key(first.view_id))  # old views stay readable (immutable keys)

    # security.results.published: pointer to a portal-shaped run
    store.put_json(B, "portal/security/run-2.json", {"runId": "run-2", "date": "2026-10-30", "purpose": "p", "litellm": "1",
                                                      "judge": "j", "cases": 3, "models": {}, "items": []})
    store.put_json(B, "portal/security/current.json", {"current": "run-2"})
    third = build_and_publish(store, B, today=dt.date(2026, 10, 30), now=T0 + dt.timedelta(hours=2))
    assert third.security_run_id == "run-2" and store.get_json(B, view_key(third.view_id))["security"]["runId"] == "run-2"

    # a pointer with a path in it is refused
    store.put_json(B, "portal/security/current.json", {"current": "../../etc"})
    with pytest.raises(ValueError):
        load_inputs(store, B)


def test_prune_keeps_newest(mini_data):
    store = MemoryStore()
    ids = [f"2026103{d}T000000Z-{i:012x}" for i, d in enumerate("0123456789")]
    for i in ids:
        store.put_bytes(B, view_key(i), b"{}")
    store.put_bytes(B, "portal/views/current.json", b"{}")
    doomed = prune_views(store, B, keep=3, protect={ids[0]})
    assert doomed == ids[1:7]
    left = sorted(o.key for o in store.list(B, "portal/views/") if o.key.endswith("portal.json"))
    assert left == sorted(view_key(i) for i in [ids[0]] + ids[7:])


def _job(event, data=None):
    return Job(id=1, queue="view-builder", payload={"event": event, "event_id": 7, "payload": data or {}}, attempts=1,
               max_attempts=5)


def test_worker_handler(mini_data, monkeypatch):
    store = MemoryStore()
    b = Builder(store, B, tz=portal_tz())
    b.handle(_job("release.published", {"release_id": "r1"}))  # no manifest yet: logged, job completes
    assert not store.exists(B, VIEW_POINTER)
    seed(store, B, mini_data, security_run=None, matrix=None, editorial=None, release_id="r1", source="mini")
    b.handle(_job("release.published", {"release_id": "r1", "held": False}))
    vid = store.get_json(B, VIEW_POINTER)["current"]
    b.handle(_job("editorial.updated", {"key": EDITORIAL_KEY}))
    assert store.get_json(B, VIEW_POINTER)["current"] == vid  # nothing changed
    with pytest.raises(PermanentError):
        b.handle(_job("request.submitted"))
    store.put_bytes(B, "portal/releases/r1/data/radar.json", b"{not json")
    with pytest.raises(PermanentError):
        b.handle(_job("security.results.published", {"run_id": "x", "key": "portal/security/x.json"}))


def test_cli_compile_and_seed(mini_data, tmp_path, monkeypatch, capsys):
    from view_builder.__main__ import main

    out = tmp_path / "view.json"
    assert main(["compile", "--data-dir", str(mini_data), "--today", "2026-10-30", "--out", str(out), "--source", "cli"]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["repo"] == "cli"
    assert main(["compile", "--data-dir", str(tmp_path / "nope"), "--out", str(out)]) == 2

    store = MemoryStore()
    monkeypatch.setattr("view_builder.__main__._store_and_bucket", lambda: (store, B))
    assert main(["seed", "--data-dir", str(mini_data), "--release-id", "r9", "--build"]) == 0
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed["release_id"] == "r9" and printed["editorial"] and printed["matrix"] and printed["view_id"]
    assert main(["seed", "--data-dir", str(mini_data)]) == 2  # manifest exists, no --force
    assert main(["build", "--today", "2026-10-30"]) == 0


def test_store_rejects_bad_release_id(mini_data):
    store = MemoryStore()
    store.put_json(B, MANIFEST, {"current": "../x"})
    with pytest.raises(ValueError):
        load_inputs(store, B)
    assert store_io.SAFE_ID.match("20261031T000000Z")


def test_seed_without_release_uploads_only_extras(tmp_path, portal_data):
    """After `publisher bootstrap` made the release: editorial, matrix and security results only.

    Needs `portal_data` only so it is skipped without the llmops-portal checkout, where SECURITY_RUN lives."""
    store = MemoryStore()
    store.ensure_bucket("b")
    done = store_io.seed(store, "b", None, security_run=SECURITY_RUN, matrix=MATRIX, editorial=EDITORIAL,
                         release=False)
    assert done["release_id"] is None and done["editorial"] and done["matrix"] and done["security_run_id"]
    assert not store.exists("b", MANIFEST)          # the publisher owns releases and the manifest
    assert store.exists("b", EDITORIAL_KEY)
