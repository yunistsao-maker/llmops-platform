"""The worker: research.proposal.ready from the queue -> releases -> release.published / route.added / model.status_changed.

Needs TEST_DATABASE_URL (a throwaway PostgreSQL); object storage is llmops_common's MemoryStore.
"""

from __future__ import annotations

import json
import threading

import pytest
from conftest import T0, T1, Plane, snapshot_tree, write_tree
from llmops_common.storage import MemoryStore
from test_batch import proposals

from publisher import cli
from publisher import handler as H
from publisher import publish as P
from publisher import releases as R
from publisher.lock import LockTimeout, publish_lock

CONTRACT_RELEASE_KEYS = {"release_id", "previous_release_id", "manifest_key", "held", "changes"}
CONTRACT_CHANGES_KEYS = {"routes_added", "prices_changed", "retirements"}


@pytest.fixture()
def platform(db, monkeypatch):
    store = MemoryStore()
    plane = Plane(store, "memory")
    monkeypatch.setenv("PUBLISH_MODE", "auto")
    monkeypatch.delenv("BUCKET_CURATED", raising=False)
    return plane


def ready_event(db, keys, run_id="20261002T031700-a1b2c3"):
    from llmops_common import queue

    with db.connection() as conn:
        return queue.publish(conn, "research.proposal.ready", {"run_date": "2026-10-02", "run_id": run_id, "proposal_keys": keys,
                                                               "roles": ["azure-researcher", "radar-editor"]},
                             source="crawler-agent", dedupe_key=f"research.proposal.ready:{run_id}")


def rows(db, sql, *args):
    with db.connection() as conn:
        return conn.execute(sql, args).fetchall()


def events(db, kind=None):
    if kind:
        return rows(db, "SELECT id, type, payload, source, dedupe_key FROM platform.events WHERE type = %s ORDER BY id", kind)
    return rows(db, "SELECT id, type, payload, source, dedupe_key FROM platform.events ORDER BY id")


def test_the_worker_publishes_the_run_and_announces_each_release(platform, db):
    base = platform.bootstrap()
    keys = [k for k, _ in proposals(platform)]
    event_id = ready_event(db, keys)
    assert cli.main(["once"], store=platform.store) == cli.EXIT_OK

    job = rows(db, "SELECT status, attempts, last_error FROM platform.jobs WHERE queue = 'publisher'")[0]
    assert job["status"] == "done" and job["attempts"] == 1, job
    current = R.load_current(platform.data)
    assert current.meta["source"]["trigger"] == f"event:{event_id}" and current.meta["source_proposals"] == keys[:2]
    published = events(db, "release.published")
    assert [e["payload"]["held"] for e in published] == [False, True]
    for e in published:
        assert set(e["payload"]) == CONTRACT_RELEASE_KEYS and set(e["payload"]["changes"]) == CONTRACT_CHANGES_KEYS
        assert e["payload"]["manifest_key"] == "portal/manifest.json" and e["source"] == "publisher"
        assert e["dedupe_key"] == f"release.published:{e['payload']['release_id']}"
    live_event, held_event = (e["payload"] for e in published)
    assert (live_event["release_id"], live_event["previous_release_id"]) == (current.release_id, base.release_id)
    assert (held_event["release_id"], held_event["previous_release_id"]) == (R.held_of(current.manifest)[0]["release_id"], current.release_id)
    # every subscriber got its job (contracts/events.md: view-builder, test-orchestrator, notifier)
    queues = sorted(r["queue"] for r in rows(db, "SELECT queue FROM platform.jobs WHERE payload->>'event' = 'release.published'"))
    assert queues == sorted(["view-builder", "test-orchestrator", "notifier"] * 2)


def test_a_crash_between_the_pointer_and_the_events_loses_nothing_and_doubles_nothing(platform, db, monkeypatch):
    platform.bootstrap()
    keys = [k for k, _ in proposals(platform)]
    ready_event(db, keys)
    real, calls = H.emit, []

    def dies_once(conn, releases):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("the pod was killed")  # the pointer has switched; nothing was announced
        return real(conn, releases)

    monkeypatch.setattr(H, "emit", dies_once)
    assert cli.main(["once"], store=platform.store) == cli.EXIT_OK
    job = rows(db, "SELECT status, last_error FROM platform.jobs WHERE queue = 'publisher'")[0]
    assert job["status"] == "queued" and "ConnectionError" in job["last_error"] and events(db, "release.published") == []
    rows_before = R.list_release_ids(platform.data)
    with db.connection() as conn:
        conn.execute("UPDATE platform.jobs SET run_after = now() WHERE queue = 'publisher'")
    assert cli.main(["once"], store=platform.store) == cli.EXIT_OK
    assert rows(db, "SELECT status FROM platform.jobs WHERE queue = 'publisher'")[0]["status"] == "done"
    assert R.list_release_ids(platform.data) == rows_before  # the retry made no new release ...
    assert len(events(db, "release.published")) == 2  # ... and announced the ones made the first time, once each
    with db.connection() as conn:  # a third delivery changes nothing either
        from llmops_common.queue import Job

        outcome = H.Publisher(platform.store, settings=H.Settings("llmops-curated", P.Policy(mode="auto")), lock=lambda: _nolock()).handle(
            Job(id=99, queue="publisher", payload={"event": "research.proposal.ready", "event_id": 1, "payload": {
                "run_date": "2026-10-02", "run_id": "20261002T031700-a1b2c3", "proposal_keys": keys, "roles": []}}, attempts=1, max_attempts=5))
    assert outcome.repeated and len(events(db, "release.published")) == 2


def _nolock():
    from publisher.lock import no_lock

    return no_lock()


def test_routes_and_status_changes_of_a_live_release_are_announced(platform, db, tmp_path):
    """The first release (bootstrap) makes every callable offering a new route and every model news."""
    checkout = write_tree(tmp_path / "portal", snapshot_tree())
    assert cli.main(["bootstrap", "--from", str(checkout), "--by", "eunice"], store=platform.store) == cli.EXIT_OK
    release = R.load_current(platform.data).release_id
    catalog = json.loads(snapshot_tree()["data/catalog.json"])
    routes = events(db, "route.added")
    assert routes and all(set(e["payload"]) == {"model_id", "provider", "region", "model_name", "status"} for e in routes)
    assert {e["payload"]["model_id"] for e in routes} <= {m["id"] for m in catalog["models"]}
    assert all(e["dedupe_key"].startswith(f"route.added:{release}:direct:") for e in routes)
    statuses = events(db, "model.status_changed")
    assert {e["payload"]["model_id"] for e in statuses} == {m["id"] for m in catalog["models"] if m.get("platformStatus") != "watch"}
    for e in statuses:
        assert set(e["payload"]) == {"model_id", "model_name", "from", "to"} and e["payload"]["from"] == "watch"
        assert e["dedupe_key"] == f"model.status_changed:{e['payload']['model_id']}:{e['payload']['to']}:{release}"
    first = events(db, "release.published")[0]["payload"]
    assert first["previous_release_id"] is None and len(first["changes"]["routes_added"]) == len(routes)
    assert {r["queue"] for r in rows(db, "SELECT queue FROM platform.jobs WHERE payload->>'event' = 'route.added'")} == {"test-orchestrator"}


def test_a_rollback_is_announced_again_with_its_own_keys(platform, db):
    platform.bootstrap()
    proposal = proposals(platform)[:1]
    ready_event(db, [k for k, _ in proposal])
    cli.main(["once"], store=platform.store)
    assert cli.main(["rollback", "--by", "bob", "--reason", "日期有疑問"], store=platform.store) == cli.EXIT_OK
    back = events(db, "release.published")[-1]
    current = R.load_current(platform.data)
    assert back["payload"]["release_id"] == current.release_id and back["dedupe_key"] == f"release.published:{current.release_id}@{current.manifest['sequence']}"
    assert R.hold_of(current.manifest)["by"] == "bob"  # (a rollback pauses automatic publishing)


def test_a_platform_without_a_release_retries_later(platform, db):
    ready_event(db, ["research/proposals/prop-x.json"])
    assert cli.main(["once"], store=platform.store) == cli.EXIT_OK
    job = rows(db, "SELECT status, last_error, run_after > now() + interval '5 minutes' AS later FROM platform.jobs WHERE queue = 'publisher'")[0]
    assert job["status"] == "queued" and "python -m publisher bootstrap" in job["last_error"] and job["later"]


def test_a_malformed_event_is_parked_as_dead(platform, db):
    from llmops_common import queue

    platform.bootstrap()
    with db.connection() as conn:
        queue.enqueue(conn, "publisher", {"event": "research.proposal.ready", "event_id": 5, "payload": {"run_id": "r", "proposal_keys": "x"}})
        queue.enqueue(conn, "publisher", {"event": "release.published", "event_id": 6, "payload": {}})
    assert cli.main(["once"], store=platform.store) == cli.EXIT_OK
    assert [r["status"] for r in rows(db, "SELECT status FROM platform.jobs WHERE queue = 'publisher' ORDER BY id")] == ["dead", "dead"]


def test_without_a_publish_mode_every_proposal_waits(platform, db, monkeypatch):
    """A deployment that does not say is not one that publishes unseen (PUBLISH_MODE defaults to review)."""
    monkeypatch.delenv("PUBLISH_MODE")
    base = platform.bootstrap()
    ready_event(db, [k for k, _ in proposals(platform)[:2]])
    cli.main(["once"], store=platform.store)
    assert R.load_current(platform.data).release_id == base.release_id
    assert [e["payload"]["held"] for e in events(db, "release.published")] == [True]
    monkeypatch.setenv("PUBLISH_MODE", "manual")
    with pytest.raises(Exception, match="PUBLISH_MODE"):
        H.policy_from_env()


def test_one_publisher_at_a_time(db):
    held, released = threading.Event(), threading.Event()

    def first():
        with publish_lock():
            held.set()
            released.wait(5)

    t = threading.Thread(target=first)
    t.start()
    assert held.wait(5)
    with pytest.raises(LockTimeout):
        with publish_lock(timeout=0.3, poll=0.1):
            pass
    released.set()
    t.join(5)
    with publish_lock(timeout=2, poll=0.1):  # free again
        pass


def test_readiness_needs_the_database_and_the_bucket(platform, db, monkeypatch):
    publisher = H.Publisher(platform.store)
    publisher.ready()
    from llmops_common import db as dbmod

    dbmod.close_pool()
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:wrong@127.0.0.1:9/none")
    monkeypatch.setattr(dbmod, "_get_pool", lambda: (_ for _ in ()).throw(ConnectionError("database down")))
    with pytest.raises(ConnectionError):
        publisher.ready()


def test_status_and_held_decisions_from_the_command_line(platform, db, capsys):
    assert cli.main(["status"], store=platform.store) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1]) == {"bootstrapped": False}
    platform.bootstrap(now=T0)
    P.publish_batch(platform.data, proposals(platform), policy=P.Policy(mode="review"), control=platform.control, now=T1, trigger="event:1")
    capsys.readouterr()
    assert cli.main(["status"], store=platform.store) == cli.EXIT_OK
    status = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    held = status["held"][0]["release_id"]
    assert status["bootstrapped"] and len(status["held"][0]["proposals"]) == 3 and status["paused"] is None
    assert cli.main(["discard", "prop-20261002T030000Z-0000000c", "--by", "amy", "--reason", "先不要"], store=platform.store) == cli.EXIT_OK
    assert cli.main(["approve", held, "--by", "eunice"], store=platform.store) == cli.EXIT_OK
    assert P.waiting(platform.data) == [] and events(db, "release.published")[-1]["payload"]["held"] is False
    assert cli.main(["approve", held, "--by", "eunice"], store=platform.store) == cli.EXIT_REFUSED  # nothing waits any more
    assert cli.main(["pause", "--by", "amy", "--reason", "連假"], store=platform.store) == cli.EXIT_OK
    assert R.hold_of(R.read_pointer(platform.data)[0])["reason"] == "連假"
    assert cli.main(["resume", "--by", "amy"], store=platform.store) == cli.EXIT_OK
    assert cli.main(["bootstrap", "--from", "/nonexistent", "--by", "x"], store=platform.store) == cli.EXIT_USAGE
