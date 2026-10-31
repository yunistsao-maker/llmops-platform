# test-orchestrator

Plans security test runs, sends the work to test-runner, tracks every run to completion, and watches the LLM gateway
version.

**Kind:** service + worker in one process. FastAPI on `PORT` (default 8080) with `GET /healthz` and `GET /readyz`
(database reachable); a Worker thread on queue `test-orchestrator`; a watcher thread (gateway version + reaper).
SIGTERM: uvicorn stops, the worker finishes its current job, the process exits (well within 30 s).

## API (role `admin`; `ROLE_ADMIN` lists the groups)

| Method | Path | Body / result |
|---|---|---|
| POST | `/api/tests/plans` | `{model_id?, route_ids?, sec_ids?, trigger: "manual" \| "schedule", purpose?: "pre-release" \| "regression" \| "adhoc"}` -> 201 `{plan_id, run_id, purpose, jobs_total, jobs, routes, items, not_run, derived, skipped, status}` |
| GET | `/api/tests/plans/{id}` | plan row + verdict counts |
| GET | `/api/tests/runs/{run_id}` | plan + every `tests.results` row |
| GET | `/api/tests/routes` | every route (enabled or not) |

With neither `model_id` nor `route_ids`, every enabled route is planned. Without `sec_ids`, every runnable item.
Default purpose: `pre-release` (route.added, or a full manual plan), `adhoc` (manual subset), `regression` (gateway).

## Planning

For each catalog item (`contracts/sec-catalog.json`, copied into the image) and each route it applies to
(`routes`: all / gateway / primary = first gateway route / none):

- `runnable: false` - never planned (asked-for ones are listed under `skipped`).
- SEC-11 (`layer: logged`) - derived when the run completes from the run's other results of that route:
  `n` = answered case executions, failures = classifier / content-filter refusals or reroutes, verdict `info`,
  `models_seen` / `stop_reasons` merged. Through the gateway response.model is the alias, so this is meaningful on
  direct routes (noted in the SEC-11 summary).
- SEC-16 (platform, no cases) - one check per gateway, on that gateway's first route.
- Everything else needs an approved dataset in the datasets bucket's `index.json`; without one the (route, item) is
  written to `tests.results` as `not_run` and nothing is sent.
- `not_worse_than_baseline` items: when a baseline (production) model is set (`BASELINE_MODEL_ID` or the routes file's
  `baseline_model_id`) and differs from the planned model, the baseline's matching route (same kind and provider, same
  gateway or region) runs the same items with the same dataset, seed and n. At completion the candidate is compared
  with it (secsuite's rule: not worse -> pass; worse -> review, or **fail** when a one-sided two-proportion z-test is
  significant; review while the candidate's undecided cases could still make it worse). Without a baseline the runner's fallback verdict stays and `rule.baseline = "missing"`.

Jobs: one per (route, batch of items), batches of at most `JOB_MAX_CALLS` case executions (`min(n, dataset size) x
trials`), enqueued on `test-runner` with dedupe key = `job_key` (`<run_id>|<route_id>|SEC-01,SEC-04`). The plan also
writes `runs/{run_id}/run.json` (routes, items and their mode, jobs, settings, judge alias).

## Events

| Consumes | Action |
|---|---|
| `route.added` | upsert the direct route (a route already in `ROUTES_FILE` is not overwritten); after `ROUTE_PLAN_DELAY_SECONDS` (internal job `test-orchestrator.plan_model`, so the events of one new model become one plan) plan the model with trigger `route.added` |
| `release.published` | if `SYNC_ROUTES_ON_RELEASE`: reload `ROUTES_FILE`, add the release's `changes.routes_added` (no plan) |
| `gateway.version_changed` | plan the runnable platform-layer items (SEC-07, SEC-16) plus the smoke set (`SMOKE_SEC_IDS` at `SMOKE_N` x `SMOKE_TRIALS`) on that gateway's routes, purpose `regression` |
| `test.job.completed` | recompute the plan counters; when all jobs ended: SEC-11, baseline comparisons, `error` rows for lost jobs, status `done` (no job failed) / `partial` / `failed` (no job done, or nothing could be planned), then publish `test.run.completed` |

| Publishes | When |
|---|---|
| `gateway.version_changed {gateway, previous_version, version}` | the watcher sees a new version (or the first one) |
| `test.run.completed {plan_id, run_id, status}` | a run ends (dedupe `test.run.completed:<run_id>`) |

Idempotency: counters are recomputed from `platform.events` (one `test.job.completed` per job key) and dead runner jobs,
never incremented; the plan row is locked while finalising; event-triggered plans store `trigger_detail.source_key`.
Every `REAP_SECONDS` the watcher finalises runs whose last job died without an event (killed on its last attempt).

**Gateway watcher:** every `GW_POLL_SECONDS`, `GatewayClient.gateway_version()` on `GW_URL` (LiteLLM v1.105 reports the
version only on the authenticated `/health/readiness/details`, so `GW_KEY` must be the master key or an admin key). The
last `gateway.version_changed` event of that gateway is the state (no table); dedupe key
`gateway.version_changed:<gateway>:<previous event id>:<version>`, so replicas polling together publish once.

## Routes file

`ROUTES_FILE` (JSON, e.g. a ConfigMap) lists configured direct and gateway routes; sample:
`config/routes.sample.json` (also in the image at `/app/config/routes.sample.json`). It is loaded into `tests.routes` at
start-up and on `release.published`; routes in the file are the source of truth. `provider_defaults` completes routes
created from `route.added` events (Azure endpoint, Vertex project); an incomplete one is stored disabled.

## Configuration

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `DATABASE_URL` | yes | | PostgreSQL |
| `S3_ENDPOINT_URL`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_FORCE_PATH_STYLE` | no | AWS defaults | object store; the secret key is a **secret** |
| `BUCKET_DATASETS`, `BUCKET_RUNS` | no | `llmops-<kind>` | read `index.json` / manifests; write `run.json` and SEC-11 summaries |
| `PORT` | no | 8080 | API and probes |
| `AUTH_MODE`, `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL`, `OIDC_GROUPS_CLAIM` | yes (oidc) | `oidc` | token checks; `dev` only on a test box |
| `ROLE_ADMIN` | yes | | groups allowed to use the API |
| `ROUTES_FILE` | recommended | | routes file path |
| `SEC_CATALOG_PATH` | no | `/app/contracts/sec-catalog.json` | |
| `GW_URL` | for the watcher | unset (watcher only reaps) | e.g. `http://llm-gateway:4000` |
| `GW_KEY` | with `GW_URL` | | **secret**: key allowed to call `/health/readiness/details` |
| `GW_NAME` | no | `onprem` | gateway name in route ids `gw:<name>:...` |
| `GW_POLL_SECONDS`, `REAP_SECONDS` | no | 300, 120 | |
| `JUDGE_MODEL` | no | `judge` | judge alias put into every job |
| `MAX_TOKENS`, `SAMPLE_SEED` | no | 2048, 7 | job `settings` |
| `JOB_MAX_CALLS`, `RUNNER_MAX_ATTEMPTS` | no | 1500, 3 | job size and attempts |
| `SMOKE_SEC_IDS`, `SMOKE_N`, `SMOKE_TRIALS` | no | `SEC-01,SEC-04,SEC-10`, 20, 1 | smoke set after a gateway change |
| `ROUTE_PLAN_DELAY_SECONDS` | no | 60 | debounce of route.added plans |
| `PLAN_ON_ROUTE_ADDED`, `PLAN_ON_GATEWAY_CHANGE`, `SYNC_ROUTES_ON_RELEASE` | no | true | |
| `BASELINE_MODEL_ID` | no | routes file `baseline_model_id` | production model for `not_worse_than_baseline` |
| `ORCHESTRATOR_BACKGROUND` | no | true | false = API only (tests) |
| `POLL_SECONDS` | no | 2 | queue poll interval |
| `COMPONENT`, `LOG_LEVEL` | no | `test-orchestrator`, `INFO` | |

**Outbound:** none outside the cluster (gateway, PostgreSQL, object store). **Resources:** requests 100m CPU / 256Mi,
limit 500m / 512Mi. 1-2 replicas (safe with more: dedupe keys and row locks).

## Contract notes

- The runs bucket is "written by test-runner"; test-orchestrator also writes `run.json` (only it knows the whole plan)
  and the derived `SEC-11.summary.json`.
- `route.added` carries no route id, kind or full target; direct routes are built as
  `direct:<provider>:<region>:<model_id>` and completed from `provider_defaults`.
- One target gateway per deployment (`TARGET_GATEWAY_URL` in test-runner); several gateways would need one URL each.

## Tests

`pytest components/test-orchestrator/tests` with `TEST_DATABASE_URL` set. `test_e2e.py` runs dataset-manager ->
orchestrator API -> two runner workers (mock provider, judge behind a fake gateway) -> run completion ->
results-publisher in one process.
