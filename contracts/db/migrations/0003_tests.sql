-- Security test planning and results (test-orchestrator, test-runner, results-publisher).
CREATE SCHEMA IF NOT EXISTS tests;

-- One callable target: a model on a cloud (direct) or through a gateway.
CREATE TABLE IF NOT EXISTS tests.routes (
    id          text        PRIMARY KEY,          -- e.g. direct:bedrock:us-east-1:claude-sonnet-5-5 / gw:onprem:claude-sonnet-5-5
    model_id    text        NOT NULL,             -- catalog id, e.g. claude-sonnet-5-5
    kind        text        NOT NULL CHECK (kind IN ('direct', 'gateway')),
    provider    text        NOT NULL CHECK (provider IN ('bedrock', 'azure', 'vertex', 'anthropic', 'gateway', 'mock')),
    target      jsonb       NOT NULL,             -- provider-specific: model name, region, endpoint, gateway alias
    enabled     boolean     NOT NULL DEFAULT true,
    first_seen  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS routes_model ON tests.routes (model_id);

CREATE TABLE IF NOT EXISTS tests.plans (
    id                uuid        PRIMARY KEY,
    created_at        timestamptz NOT NULL DEFAULT now(),
    trigger           text        NOT NULL,       -- route.added | gateway.version_changed | manual | schedule
    trigger_detail    jsonb       NOT NULL DEFAULT '{}',
    model_id          text,
    run_id            text        NOT NULL UNIQUE,
    status            text        NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'partial', 'failed', 'cancelled')),
    jobs_total        int         NOT NULL DEFAULT 0,
    jobs_done         int         NOT NULL DEFAULT 0,
    jobs_failed       int         NOT NULL DEFAULT 0,
    gateway_version   text,
    finished_at       timestamptz
);

-- One row per (run, route, SEC item): what the portal and G6 read.
CREATE TABLE IF NOT EXISTS tests.results (
    run_id        text        NOT NULL,
    route_id      text        NOT NULL,
    sec_id        text        NOT NULL,
    dataset       text,                           -- <dataset_id>@v<N>
    n             int         NOT NULL DEFAULT 0,
    trials        int         NOT NULL DEFAULT 1,
    failures      int         NOT NULL DEFAULT 0,
    errors        int         NOT NULL DEFAULT 0,
    rate          double precision,
    upper95       double precision,               -- one-sided 95% upper bound of the failure rate
    verdict       text        NOT NULL CHECK (verdict IN ('pass', 'fail', 'review', 'info', 'not_run', 'error')),
    rule          jsonb       NOT NULL DEFAULT '{}',
    models_seen   jsonb       NOT NULL DEFAULT '{}', -- response.model counts (SEC-11)
    stop_reasons  jsonb       NOT NULL DEFAULT '{}',
    details_key   text,                           -- runs bucket key with the case-level lines
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, route_id, sec_id)
);
CREATE INDEX IF NOT EXISTS results_sec ON tests.results (sec_id, created_at DESC);
