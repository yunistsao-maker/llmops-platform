# request-api

Model access requests for the LLMOps portal. The portal's 申請 form posts here. Reviewers decide gate by gate:
G2 (法遵審查, compliance) and G8 (試用, trial). An approved request goes to key-provisioner as a
`request.approved` event. The same process consumes `key.provisioned` / `key.failed` and records the outcome
on the request. A LiteLLM key never reaches this service: it stores only the key alias and the secret store
reference.

| | |
|---|---|
| Kind | service (FastAPI + uvicorn), one process; also serves `/api/subscriptions` (portal-web forwards both prefixes) |
| Image | `docker build -f components/request-api/Dockerfile .` (repo root); entrypoint `python -m request_api`, CMD `serve` |
| Port / probes | `PORT` (8080): API under `/api/requests`, `GET /healthz` (liveness), `GET /readyz` (readiness: database reachable) |
| Queue consumed | `request-api` (background thread in the same process; safe with several replicas) |
| Runtime | UID/GID 10001, read-only root filesystem, writes nothing (an `emptyDir` on `/tmp` is fine but not needed) |
| Shutdown | SIGTERM: uvicorn drains requests (≤ 25 s), then the consumer thread finishes its job and stops |

Other commands: `python -m request_api check-config` validates the settings and the schema, then exits.

## API

All routes need a signed-in user (`Authorization: Bearer <OIDC token>`). Bodies are JSON. Errors are
`{"detail": ...}`; schema errors are 422 with `[{loc, msg, type}]`.

| Method and path | Who | What |
|---|---|---|
| `POST /api/requests` | any signed-in user | Submit. The body is validated against `contracts/schemas/request.schema.json` (copied into the image). `alias` defaults to the user's alias (email local part). Responds **201** `{id, ref, status, gates, next_gate, stage}` with `Location`. |
| `GET /api/requests?mine=1` | any signed-in user | The caller's own requests. |
| `GET /api/requests[?status=a,b][&next_gate=G2\|G8\|G9]` | reviewer-g2, reviewer-g8, admin | All requests. `next_gate=G2` is the G2 reviewers' queue; `next_gate=G9` lists running trials. |
| `GET /api/requests/{id}` | owner, reviewers, admin | Everything stored plus `decisions`, `history` (audit), `limits` (granted, or proposed for the next launch), `key` (alias, secret reference, expiry) and `permissions` (what the caller may do now). Others get 404. |
| `POST /api/requests/{id}/decisions` | reviewer of that gate, or admin | `{gate, decision: approve\|reject\|request_changes, comment}`. Only the request's current `next_gate` is accepted (409 otherwise). A comment is required for reject and request_changes. G9 is decided by reviewer-g8 or admin and takes approve or reject only. |
| `POST /api/requests/{id}/withdraw` | owner | `{reason?}`, only while `in_review` or `changes_requested`. |
| `POST /api/requests/{id}/resubmit` | owner | Allowed after `request_changes`. The body is a patch (any schema fields). The merged request is validated again, gates are recomputed and **every review gate starts over** from `pending`. Earlier decisions stay in the history. |
| `POST /api/requests/{id}/reprovision` | admin | After `failed`: publishes `request.approved` again for the same stage. |
| `GET /api/requests/openapi.json` | anyone | OpenAPI document (no data). |
| `GET /api/subscriptions` | any signed-in user | Models the caller follows ("notify me when this model can be requested"), as `{"items": [{"model_id", "created_at"}]}` sorted by model id. Only the caller's own rows. |
| `PUT /api/subscriptions/{model_id}` | any signed-in user | Follow a model. Responds **201** `{model_id, created_at}` the first time and **200** with the same body if already followed. Refreshes the stored email when the token's email changed. 429 beyond `MAX_SUBSCRIPTIONS_PER_USER`. |
| `DELETE /api/subscriptions/{model_id}` | any signed-in user | Unfollow. Responds **204**, also when the caller did not follow it. |

Lists use keyset pagination: `limit` is 1–`PAGE_SIZE_MAX` (default `PAGE_SIZE_DEFAULT`). Pass the returned
`next_cursor` as `cursor` to get the next page. Items are newest first.

Limits:
- Bodies over `MAX_BODY_BYTES` get 413. Content-Length is checked, and chunked bodies are counted.
- A user may have at most `MAX_OPEN_REQUESTS_PER_USER` requests in `in_review` / `changes_requested`; beyond
  that, submit returns 429. The count is exact even when the same user submits concurrently.
- Text fields are bounded by the schema.

Subscriptions:
- `model_id` must match the catalog id pattern of the request schema (`models[].pattern`,
  `^[a-z0-9][a-z0-9.-]{1,80}$`); other ids get 422. Ids are not checked against the catalog, which lives in
  object storage.
- The table is `requests.subscriptions` (migration `0004_subscriptions.sql`); the email comes from the token at
  follow time.
- When the publisher sends `model.status_changed` with `to` = `trial` or `available`, the notifier mails every
  follower that has an email. Followers whose token carried no email are stored but cannot be notified.
- Like the open-request cap, the subscription cap is exact under concurrency (per-user transaction lock).

Separation of duties: nobody decides on their own request (403) unless `ALLOW_SELF_APPROVAL=true`. That flag
defaults to true only with `AUTH_MODE=dev`, so a developer can walk the whole flow alone.

## Gates and statuses

Gates come from `llmops_common.gates.required_gates` (the shared, still tentative rule):
- G2 is required for data class Security B, Security A/AA or Other, or for an external service.
- G8 is required for an external service or 10,000 or more requests a day.
- G0 and G9 are always present.

The stored gate list is
`[{"gate": "G0", "status": "approved"}, {"gate": "G2", "status": "pending"}, …, {"gate": "G9", "status": "pending"}]`.
A gate is `pending`, `approved` or `rejected`.

```
in_review ──approve last review gate──▶ provisioning ──key.provisioned──▶ active (full) | trial (G8 required)
    │  ╲──request_changes──▶ changes_requested ──resubmit──▶ in_review          └──key.failed──▶ failed ──reprovision──▶ provisioning
    ├──reject──▶ rejected        └──withdraw──▶ withdrawn
trial ──G9 approve──▶ provisioning (full key) ──▶ active;   trial ──G9 reject──▶ rejected (the trial key expires by itself)
```

- No review gate required (e.g. Security C, internal, under 10,000 requests a day): the request goes straight to
  `provisioning` on submit, with stage `full`.
- G8 required: approving every review gate starts a 14-day **trial** key. Status becomes `trial`, and
  `next_gate` becomes `G9`. The G9 decision after the trial launches the full key.
- `approved` exists in the database schema but is unused; approval moves straight to `provisioning`.
- `stage` in responses means the stage being or last provisioned: `full` once G9 is approved, otherwise `trial`.

## Budget and rate limits in `request.approved`

Computed from the request's own estimate when a stage is launched (`request_api.domain.compute_limits`):

```
rpd       = requests per day (LIMIT_DEFAULT_RPD if 0), tin/tout = tokens per request (defaults if 0)
rpm       = clamp(ceil(rpd × LIMIT_PEAK_FACTOR / 1440), LIMIT_RPM_MIN, LIMIT_RPM_MAX)
tpm       = clamp(rpm × (tin + tout), LIMIT_TPM_MIN, LIMIT_TPM_MAX)
cost/day  = rpd × (tin × price_in + tout × price_out) / 1e6        USD; prices per 1M tokens, the most
                                                                   expensive requested model (BUDGET_MODEL_PRICES,
                                                                   else BUDGET_PRICE_IN/OUT_PER_MTOK)
budget    = clamp(ceil(cost/day × days × BUDGET_HEADROOM), BUDGET_MIN_USD, BUDGET_MAX_USD)
trial:  days = TRIAL_DAYS (14), duration "14d", no budget reset
full:   days = FULL_BUDGET_PERIOD_DAYS (30), budget_duration "30d" (LiteLLM resets spend each period),
        duration = FULL_KEY_DURATION (empty = the key does not expire)
```

Example: 5,000 requests a day × (3,000 in + 500 out) tokens at the default US$3 / US$15 gives:
- rpm 35 and tpm 122,500;
- US$82.50 a day;
- a trial budget of US$1,733 and a full budget of US$3,713 per 30 days.

## Events

Published in the same transaction as the request row, the decision and the audit rows. Fields marked +
are additions to `contracts/events.md`:

| Event | Dedupe key | Payload |
|---|---|---|
| `request.submitted` | `request.submitted:<id>:<revision>` | `request_id, team, alias, gates [ids], next_gate` + `ref, status, requester_email, models, unsure, data_class, data_class_other, rpd, external, taiwan_only, revision` |
| `request.decided` | `request.decided:<decision id>` | `request_id, gate, decision, comment, actor` + `ref, team, alias, requester_email, status, next_gate` |
| `request.approved` | `request.approved:<id>:<stage>[:retry<n>]` | `request_id, team, alias, models, stage, budget_usd, rpm, tpm, duration` + `ref, unsure, budget_duration, requester_email, attempt` |

`next_gate` is null when nobody has to decide anything: the request was launched, or it is closed.

Consumed on queue `request-api`:
- `key.provisioned`: `{request_id, key_alias, secret_ref, stage, expires_at}` sets status `trial` or
  `active` and stores `key_alias` / `secret_ref`.
- `key.failed`: `{request_id, error, stage?}` sets status `failed`.

An event is ignored (and logged) unless the request is still `provisioning` for the same stage. That makes
duplicates and stale events harmless.

## Configuration

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `DATABASE_URL` / `_FILE` | yes | – | PostgreSQL (`requests.*`, `platform.events/jobs`) |
| `PORT` | no | `8080` | HTTP port |
| `AUTH_MODE` | no | `oidc` | `oidc`; `dev` only for local runs and the test box (everyone is `DEV_USER_EMAIL`). Do not use `none` here. |
| `OIDC_ISSUER`, `OIDC_AUDIENCE` | with `oidc` | – | issuer URL and audience the bearer token must carry |
| `OIDC_JWKS_URL` | no | discovered | JWKS URL (skip discovery) |
| `OIDC_GROUPS_CLAIM` | no | `groups` | claim with the user's groups |
| `ROLE_ADMIN`, `ROLE_REVIEWER_G2`, `ROLE_REVIEWER_G8` | yes (for reviews) | – | comma-separated groups holding each role |
| `DEV_USER_EMAIL`, `DEV_USER_GROUPS` | no | `dev@example.local`, `llmops-admins` | identity in `AUTH_MODE=dev` |
| `ALLOW_SELF_APPROVAL` | no | `true` with dev auth, else `false` | allow deciding on your own request |
| `REQUEST_SCHEMA_PATH` | no | `/app/schemas/request.schema.json` | request JSON schema (baked into the image) |
| `REQUEST_API_WORKER` | no | `true` | run the `key.*` consumer thread in this process |
| `WORKER_POLL_SECONDS` | no | `2` | queue poll interval when idle |
| `MAX_OPEN_REQUESTS_PER_USER` | no | `20` | open requests per user (`0` = unlimited) |
| `MAX_SUBSCRIPTIONS_PER_USER` | no | `50` | followed models per user (`0` = unlimited) |
| `MAX_BODY_BYTES` | no | `65536` | request body limit |
| `PAGE_SIZE_DEFAULT`, `PAGE_SIZE_MAX` | no | `50`, `200` | list page sizes |
| `LIMIT_PEAK_FACTOR` | no | `10` | peak minute = factor × average minute |
| `LIMIT_RPM_MIN`, `LIMIT_RPM_MAX` | no | `10`, `6000` | rpm bounds |
| `LIMIT_TPM_MIN`, `LIMIT_TPM_MAX` | no | `10000`, `10000000` | tpm bounds |
| `LIMIT_DEFAULT_RPD`, `LIMIT_DEFAULT_TOKENS_IN`, `LIMIT_DEFAULT_TOKENS_OUT` | no | `1000`, `2000`, `500` | used when the form says 0 |
| `BUDGET_PRICE_IN_PER_MTOK`, `BUDGET_PRICE_OUT_PER_MTOK` | no | `3.0`, `15.0` | USD per 1M tokens for models without a configured price |
| `BUDGET_MODEL_PRICES` | no | – | JSON `{"claude-opus-5-5": {"in": 15, "out": 75}}` |
| `BUDGET_HEADROOM` | no | `1.5` | multiplier over the estimate |
| `BUDGET_MIN_USD`, `BUDGET_MAX_USD` | no | `20`, `20000` | budget bounds |
| `TRIAL_DAYS` | no | `14` | trial key lifetime and budget window |
| `FULL_BUDGET_PERIOD_DAYS` | no | `30` | budget reset period of full keys |
| `FULL_KEY_DURATION` | no | – (no expiry) | LiteLLM duration for full keys, e.g. `365d` |
| `DB_POOL_MAX`, `LOG_LEVEL` | no | `5`, `INFO` | shared settings |
| `HTTPS_PROXY`, `NO_PROXY` | no | – | used to fetch the IdP's JWKS |

## Secrets

- `DATABASE_URL` (holds the database password; mount it as `DATABASE_URL_FILE`). That is the only secret.
- OIDC verification needs only public keys (JWKS), never a client secret.

Database grants:
- `SELECT, INSERT, UPDATE` on `requests.requests`;
- `SELECT, INSERT` on `requests.decisions` and `requests.audit`;
- `SELECT, INSERT, UPDATE, DELETE` on `requests.subscriptions`;
- `INSERT` on `platform.events`;
- `SELECT, INSERT, UPDATE` on `platform.jobs`;
- `USAGE` on the sequences.

## Outbound connections

- PostgreSQL.
- The OIDC issuer (JWKS over HTTPS, cached for an hour).

Nothing outside the cluster otherwise.

## Resources

About 50 MiB RSS measured under a burst of 100 concurrent submissions. Suggested:
- requests `cpu: 50m, memory: 128Mi`, limits `cpu: 500m, memory: 256Mi`;
- 2 replicas (stateless; the queue consumer uses `FOR UPDATE SKIP LOCKED`).

## Tests

```
uv venv .venv --python 3.13 && uv pip install -r components/request-api/requirements.lock && uv pip install pytest
TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/test_x pytest components/request-api/tests
```

The tests drop and recreate the schemas in that database, so use a throwaway one. They cover:
- the formula and gate logic;
- every route with role, ownership and state checks;
- real OIDC tokens against a local JWKS server;
- the consumer thread;
- an end-to-end run through key-provisioner (mock LiteLLM) and notifier (dry run);
- subscriptions: ownership, validation, cap, email refresh, and follow → `model.status_changed` → notifier.
