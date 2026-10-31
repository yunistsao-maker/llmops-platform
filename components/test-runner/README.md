# test-runner

Executes security test jobs (`contracts/test-jobs.md`): for each SEC item of a job it samples the cases of the approved
dataset version, calls the target route for every case x trial, scores the answers with the secsuite scorers (LLM judge
through the gateway where secsuite used one), and writes the results. Many replicas can share the queue.

**Kind:** worker on queue `test-runner`. Health on `HEALTH_PORT` (default 8081): `GET /healthz` (alive), `GET /readyz`
(PostgreSQL and the datasets bucket reachable).

## What one job does

1. Validate the payload (a malformed one fails permanently and is reported).
2. Per item: `sample n` cases (seeded: same dataset version + seed + n gives the same cases on every route and run;
   a composite dataset is split across its sources by `share`, stratified by category), run `case x trials` with
   `RUNNER_CONCURRENCY` threads, score, summarise, write:
   - `runs/{run_id}/{route_id}/{sec_id}.jsonl` - one line per case x trial: outcome, label, detail, model,
     stop_reason, refusal, tokens, latency, judge model. The answer text is kept for fail / review / error lines
     (`STORE_RESPONSE_TEXT=flagged`), redacted (`llmops_common.log.redact`) and clipped. Prompts are not copied (they
     are in the datasets bucket under the case id).
   - `runs/{run_id}/{route_id}/{sec_id}.summary.json` - `n, trials, failures, errors, rate, upper95, verdict, rule,
     models_seen, stop_reasons` plus attempts, reviews, labels, by_source, refusals, rerouted, safeguard_signals,
     judge_models_seen, pair_consistency (SEC-12), median_value (SEC-07), tokens, note.
   - upsert `tests.results` (run_id, route_id, sec_id).
3. Publish `test.job.completed` once per job (dedupe key `test.job.completed:<job_key>`).

**Statistics.** Errors (provider failures) are not attempts; `review` (undecided: no judge, unparsable) is not an
attempt either. `rate = failures / attempts`; `upper95` is the one-sided 95% Clopper-Pearson upper bound
(`1 - 0.05^(1/n)`, about 3/n, at zero failures). More than 20% errors among all executions gives verdict `error`.
Rules: `max_rate` (fail above max; review when the undecided cases could still cross it), `zero` (`review_failures`:
review until a person confirms), `control`, `info`, `not_worse_than_baseline` (the runner applies `fallback` and marks
`rule.baseline` = `pending` when the job names a baseline route, else `missing`; test-orchestrator compares at the end).

**SEC-16** (no cases): OSV.dev malware records and unpatched advisories for `litellm <version>`, PyPI presence / yank,
and the avoid list + minimum baseline of the portal's `litellm.json` (current release in the curated bucket). The
version comes from the job's `settings.gateway_version` (test-orchestrator), else from the gateway.

**Retries and shutdown.** A retried job skips items whose summary exists. Failures retry with backoff; on the last
attempt (or a permanent error such as a missing dataset) the unfinished items are recorded as `error` and the job is
reported `failed`. A heartbeat refreshes the job lease every `JOB_HEARTBEAT_SECONDS`, so a long job is not taken over.
SIGTERM: no new job; the running job stops launching cases and is re-queued at once (finished items are kept); exit in
well under 30 s.

## Providers (route `provider` / `target`)

| provider | target | credentials |
|---|---|---|
| `gateway` | `{"model": "<alias>"}` | `TARGET_GATEWAY_URL`, `TARGET_GATEWAY_KEY` (OpenAI-compatible) |
| `bedrock` | `{"model": "<model id or inference profile>", "region": "us-east-1"}` | default AWS chain (IAM Roles Anywhere `credential_process` via `AWS_CONFIG_FILE`/`AWS_PROFILE`, web identity, env); Converse API |
| `azure` | `{"endpoint": "https://<res>.services.ai.azure.com", "deployment": "...", "api_version": "..." \| "v1"}` | Microsoft Entra token, no Azure SDK: `AZURE_ACCESS_TOKEN`, or `AZURE_TENANT_ID` + `AZURE_CLIENT_ID` with `AZURE_FEDERATED_TOKEN_FILE` / `AZURE_CLIENT_CERTIFICATE_PATH` (PEM: key + cert) / `AZURE_CLIENT_SECRET` |
| `vertex` | `{"project": "...", "region": "global" \| "us-east5", "model": "...", "publisher"?}` | google-auth ADC (`GOOGLE_APPLICATION_CREDENTIALS`: service account or workload identity federation); Claude via `:rawPredict`, others via the OpenAI-compatible endpoint |
| `anthropic` | `{"model": "claude-sonnet-5-5"}` | `ANTHROPIC_API_KEY` |
| `mock` | `{"behaviour": "refuse" \| "comply" \| "echo" \| "simulate", "profile"?: "strong" \| "weak"}` | none (tests, local runs) |

Temperature is never sent (Claude 5.5 rejects non-default values). Azure uses `max_completion_tokens`. An input
content-filter rejection (HTTP 400 `content_filter` / `content_policy_violation`) counts as a refusal, not an error.

## Configuration

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `DATABASE_URL` | yes | | PostgreSQL (`tests.results`, `platform.*`) |
| `DB_POOL_MAX` | no | 5 | |
| `S3_ENDPOINT_URL`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_FORCE_PATH_STYLE` | no | AWS defaults | object store; the secret key is a **secret** |
| `BUCKET_DATASETS`, `BUCKET_RUNS`, `BUCKET_CURATED` | no | `llmops-<kind>` | read cases / write results / read the litellm policy |
| `HEALTH_PORT` | no | 8081 | probes |
| `POLL_SECONDS` | no | 2 | queue poll interval |
| `JOB_LEASE_SECONDS` | no | 900 | a job whose worker stopped heart-beating is retaken after this |
| `JOB_HEARTBEAT_SECONDS` | no | 60 | lease refresh while a job runs |
| `RUNNER_CONCURRENCY` | no | 4 | parallel case executions per job (mind provider rate limits) |
| `STORE_RESPONSE_TEXT` | no | `flagged` | `all`, `flagged` (fail/review/error), `none` |
| `RESPONSE_TEXT_MAX_CHARS` | no | 4000 | clip of stored answers |
| `ERROR_SHARE` | no | 0.2 | error share above which the verdict is `error` |
| `DEFAULT_MAX_TOKENS`, `SAMPLE_SEED` | no | 2048, 7 | used when the job's `settings` lack them |
| `TARGET_GATEWAY_URL` | for gateway routes | | e.g. `http://llm-gateway:4000` (a trailing `/v1` is accepted) |
| `TARGET_GATEWAY_KEY` | for gateway routes | | **secret**: the runner's virtual key for target aliases |
| `TARGET_TIMEOUT`, `TARGET_MAX_RETRIES`, `PROVIDER_BACKOFF_SECONDS` | no | 300, 3, 1 | per-call timeout and retries |
| `JUDGE_GATEWAY_URL` | recommended | unset | judge gateway; unset = judge-scored cases end as `review`; `mock` = simulated judge |
| `JUDGE_GATEWAY_KEY` | with a judge | | **secret**: virtual key allowed to call the judge alias |
| `JUDGE_MODEL` | no | `judge` | alias when a job has no `judge.model` |
| `JUDGE_MAX_TOKENS`, `JUDGE_TIMEOUT`, `JUDGE_MAX_RETRIES` | no | 1024, 120, 3 | |
| `AWS_REGION`, `AWS_CONFIG_FILE`, `AWS_PROFILE`, ... | for bedrock | | standard AWS settings; credentials from the default chain |
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_FEDERATED_TOKEN_FILE`, `AZURE_CLIENT_CERTIFICATE_PATH`, `AZURE_CLIENT_SECRET`, `AZURE_ACCESS_TOKEN`, `AZURE_AUTHORITY_HOST`, `AZURE_SCOPE` | for azure | | the secret, certificate and token are **secrets** |
| `GOOGLE_APPLICATION_CREDENTIALS` | for vertex | | path of the credentials / WIF config file (mounted Secret) |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL` | for anthropic | `https://api.anthropic.com` | the key is a **secret** |
| `OSV_API_URL`, `PYPI_LITELLM_URL` | no | `https://api.osv.dev/v1/query`, `https://pypi.org/pypi/litellm/json` | SEC-16 feeds (mirrors allowed) |
| `HTTPS_PROXY`, `NO_PROXY`, `SSL_CERT_FILE` | no | | every outbound call honours them |
| `COMPONENT`, `LOG_LEVEL` | no | `test-runner`, `INFO` | |

**Outbound (through `HTTPS_PROXY`):** `bedrock-runtime.<region>.amazonaws.com` (+ `rolesanywhere.<region>.amazonaws.com`
/ `sts.amazonaws.com` for credentials), `<res>.services.ai.azure.com` and `login.microsoftonline.com`,
`aiplatform.googleapis.com` / `<region>-aiplatform.googleapis.com` and `sts.googleapis.com`, `oauth2.googleapis.com`,
`iamcredentials.googleapis.com`, `api.anthropic.com`, `api.osv.dev`, `pypi.org`. In-cluster: the LLM gateway, PostgreSQL,
the object store. Only enable the clouds your routes use.

**Resources:** requests 250m CPU / 512Mi, limit 1 CPU / 1Gi (up to 8 dataset versions are cached in memory).
Scale replicas for throughput; each runs `RUNNER_CONCURRENCY` calls at a time.

**Events:** consumes runner jobs on queue `test-runner` (enqueued by test-orchestrator, not an event); publishes
`test.job.completed {plan_id, run_id, job_key, route_id, sec_ids, status: done|failed, error}`.

## Changes from secsuite (and why)

- Scorers, refusal detection, judge prompts, canary matching, zh-TW checks, sampling and the SEC-16 checks are ported
  unchanged. The zh-TW scorer gets its OpenCC tables from the dataset's assets instead of a local cache.
- Bedrock moved from InvokeModel (Anthropic body) to **Converse** (works for every Bedrock model); Azure Foundry and
  Vertex were added; the gateway client is `llmops_common.llm.GatewayClient`. Input content-filter rejections count as
  refusals.
- Verdicts follow the platform rules (`contracts/sec-catalog.json`) instead of secsuite's (`relative` became
  `not_worse_than_baseline`). The rate bound is the one-sided Clopper-Pearson bound (Wilson stays in the summary).
  Errors and undecided cases are not attempts; >20% errors is `error`.
- Results go to the runs bucket and `tests.results` instead of `.cache/secsuite/results/`; answers of passing cases are
  not stored by default.
- SEC-11 is no longer a separate dataset (MITRE FRR): per the platform catalog it is derived from response.model and
  stop_reason of every call. The runner records the inputs (`models_seen`, `stop_reasons`, refusals, reroutes) and
  test-orchestrator computes SEC-11 when the run completes. Through the gateway, response.model is the **alias**
  (LiteLLM), so a fallback behind the gateway is only visible on direct routes.

## Tests

`pytest components/test-runner/tests` with `TEST_DATABASE_URL` set for the job tests. HTTP APIs use
`httpx.MockTransport`, Bedrock a botocore `Stubber`; nothing reaches a cloud or the internet.
