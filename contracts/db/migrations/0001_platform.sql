-- Shared event log and work queues (llmops_common.queue). Owned by db-migrate; used by every worker.
CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE IF NOT EXISTS platform.events (
    id          bigserial PRIMARY KEY,
    type        text        NOT NULL,
    payload     jsonb       NOT NULL,
    source      text        NOT NULL,
    dedupe_key  text        UNIQUE,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_type_created ON platform.events (type, created_at DESC);

CREATE TABLE IF NOT EXISTS platform.jobs (
    id            bigserial PRIMARY KEY,
    queue         text        NOT NULL,
    payload       jsonb       NOT NULL,
    status        text        NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'dead')),
    attempts      int         NOT NULL DEFAULT 0,
    max_attempts  int         NOT NULL DEFAULT 5,
    run_after     timestamptz NOT NULL DEFAULT now(),
    locked_by     text,
    locked_at     timestamptz,
    last_error    text,
    dedupe_key    text        UNIQUE,
    created_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz
);
CREATE INDEX IF NOT EXISTS jobs_claim ON platform.jobs (queue, status, run_after, id);
CREATE INDEX IF NOT EXISTS jobs_dead ON platform.jobs (queue) WHERE status = 'dead';
