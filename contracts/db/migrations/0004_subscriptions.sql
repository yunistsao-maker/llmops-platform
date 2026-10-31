-- "Notify me when this model can be requested" (portal). request-api writes a user's own rows; notifier reads the
-- subscribers of a model when model.status_changed moves it to trial or available.
CREATE SCHEMA IF NOT EXISTS requests;

CREATE TABLE IF NOT EXISTS requests.subscriptions (
    subscriber_sub    text        NOT NULL,                 -- OIDC subject of the subscriber
    subscriber_email  text,                                 -- where the notice goes (email claim at subscribe time)
    model_id          text        NOT NULL CHECK (model_id ~ '^[a-z0-9][a-z0-9.-]{1,80}$'),  -- catalog id, request schema pattern
    created_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (subscriber_sub, model_id)
);
CREATE INDEX IF NOT EXISTS subscriptions_model ON requests.subscriptions (model_id);
