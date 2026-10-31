import pytest

from llmops_common import queue


def test_publish_fans_out_and_dedupes(db):
    with db.connection() as c:
        eid = queue.publish(c, "request.approved", {"request_id": "r1"}, source="test", dedupe_key="req:r1:approved")
        again = queue.publish(c, "request.approved", {"request_id": "r1"}, source="test", dedupe_key="req:r1:approved")
        rows = c.execute("SELECT queue, payload FROM platform.jobs ORDER BY queue").fetchall()
    assert eid and again is None
    assert [r["queue"] for r in rows] == ["key-provisioner", "notifier"]
    assert rows[0]["payload"]["payload"] == {"request_id": "r1"} and rows[0]["payload"]["event"] == "request.approved"
    with pytest.raises(ValueError):
        with db.connection() as c:
            queue.publish(c, "not.a.type", {}, source="test")


def test_claim_complete_retry_dead(db):
    with db.connection() as c:
        queue.enqueue(c, "q", {"n": 1}, max_attempts=2)
    with db.connection() as c:
        job = queue.claim(c, "q", "w1")
        assert job.attempts == 1 and job.data == {"n": 1}
        assert queue.claim(c, "q", "w2") is None          # locked
        assert queue.fail(c, job, "boom", delay=0) == "queued"
    with db.connection() as c:
        job = queue.claim(c, "q", "w1")
        assert job.attempts == 2
        assert queue.fail(c, job, "boom again", delay=0) == "dead"
        assert queue.claim(c, "q", "w1") is None


def test_expired_lease_is_taken_over(db):
    with db.connection() as c:
        queue.enqueue(c, "q", {"n": 2})
        first = queue.claim(c, "q", "crashed", lease_seconds=900)
        c.execute("UPDATE platform.jobs SET locked_at = now() - interval '2 hours' WHERE id = %s", (first.id,))
    with db.connection() as c:
        again = queue.claim(c, "q", "w2", lease_seconds=900)
    assert again.id == first.id and again.attempts == 2


def test_worker_run_once(db):
    seen = []

    def handler(job):
        if job.data.get("bad"):
            raise queue.PermanentError("cannot work")
        seen.append(job.data["n"])

    with db.connection() as c:
        queue.enqueue(c, "w", {"n": 1})
        queue.enqueue(c, "w", {"bad": True})
    w = queue.Worker("w", handler, poll_seconds=0.01)
    assert w.run_once() and w.run_once() and not w.run_once()
    with db.connection() as c:
        states = [r["status"] for r in c.execute("SELECT status FROM platform.jobs ORDER BY id").fetchall()]
    assert seen == [1] and states == ["done", "dead"]


def test_migrations_are_idempotent(db, database_url):
    import psycopg
    from psycopg.rows import dict_row

    from conftest import MIGRATIONS
    from db_migrate.__main__ import migrate, status

    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as c:
        assert migrate(c, MIGRATIONS) == []
        assert all(state == "applied" for _, state in status(c, MIGRATIONS))
