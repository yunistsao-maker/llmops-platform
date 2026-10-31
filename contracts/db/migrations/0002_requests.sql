-- Model access requests (request-api). key-provisioner never writes here; it publishes key.* events.
CREATE SCHEMA IF NOT EXISTS requests;

CREATE TABLE IF NOT EXISTS requests.requests (
    id                uuid        PRIMARY KEY,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    requester_sub     text        NOT NULL,
    requester_email   text,
    team              text        NOT NULL,          -- 申請單位
    alias             text        NOT NULL,          -- 填表人 Alias
    use_case          text        NOT NULL,
    models            text[]      NOT NULL DEFAULT '{}',
    unsure            boolean     NOT NULL DEFAULT false,
    data_class        text        NOT NULL CHECK (data_class IN ('other', 'property', 'sec-c', 'sec-b', 'sec-a')),
    data_class_other  text,
    cloud             text        NOT NULL DEFAULT 'any' CHECK (cloud IN ('any', 'aws', 'azure', 'gcp')),
    rpd               int         NOT NULL DEFAULT 0 CHECK (rpd >= 0),
    tokens_in         int         NOT NULL DEFAULT 0 CHECK (tokens_in >= 0),
    tokens_out        int         NOT NULL DEFAULT 0 CHECK (tokens_out >= 0),
    target_date       date,
    external          boolean     NOT NULL DEFAULT false,
    taiwan_only       boolean     NOT NULL DEFAULT false,
    note              text,
    status            text        NOT NULL DEFAULT 'in_review'
                      CHECK (status IN ('in_review', 'changes_requested', 'approved', 'provisioning',
                                        'active', 'trial', 'rejected', 'failed', 'withdrawn')),
    gates             jsonb       NOT NULL DEFAULT '[]',   -- [{"gate":"G2","status":"pending|approved|rejected|skipped"}]
    key_alias         text,                                -- GW key alias, never the key itself
    secret_ref        text                                 -- where the key was stored (vault path, ARN, ...)
);
CREATE INDEX IF NOT EXISTS requests_requester ON requests.requests (requester_sub, created_at DESC);
CREATE INDEX IF NOT EXISTS requests_status ON requests.requests (status, created_at DESC);

CREATE TABLE IF NOT EXISTS requests.decisions (
    id           bigserial   PRIMARY KEY,
    request_id   uuid        NOT NULL REFERENCES requests.requests (id) ON DELETE CASCADE,
    gate         text        NOT NULL,
    decision     text        NOT NULL CHECK (decision IN ('approve', 'reject', 'request_changes')),
    comment      text,
    actor_sub    text        NOT NULL,
    actor_email  text,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS decisions_request ON requests.decisions (request_id, created_at);

CREATE TABLE IF NOT EXISTS requests.audit (
    id          bigserial   PRIMARY KEY,
    request_id  uuid        REFERENCES requests.requests (id) ON DELETE CASCADE,
    action      text        NOT NULL,
    actor       text        NOT NULL,
    detail      jsonb       NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_request ON requests.audit (request_id, created_at);
