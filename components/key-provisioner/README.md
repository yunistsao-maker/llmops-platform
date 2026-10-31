# key-provisioner

Turns an approved model access request into a LiteLLM virtual key. On `request.approved` it:
1. creates the key through the gateway's admin API;
2. writes the key to the secret store (`SECRET_SINK`);
3. publishes `key.provisioned` with **where** the key is, never the key itself, or `key.failed`.

Keys must never travel by email or chat: people fetch them from the secret store, normally by mounting them
into their service.

| | |
|---|---|
| Kind | worker on queue `key-provisioner` |
| Image | `docker build -f components/key-provisioner/Dockerfile .` (repo root); entrypoint `python -m key_provisioner`, CMD `run` |
| Probes | `HEALTH_PORT` (8081): `GET /healthz`, `GET /readyz` (database and LiteLLM `/health/liveliness` reachable) |
| Runtime | UID/GID 10001, read-only root filesystem; writes only to `/tmp` and only with `SECRET_SINK=file` |
| Shutdown | SIGTERM: finishes the current job (a few HTTP calls) and exits; an unfinished job's lease expires and is retried safely |

Other commands: `python -m key_provisioner check-config` checks the settings, admin access to the gateway and
the database, then exits.

## What the key gets

The LiteLLM proxy API calls below were checked against `ghcr.io/berriai/litellm-non_root:v1.105.2`.
`tests/test_kp_live_litellm.py` exercises them against a running proxy.

`POST /key/generate`, from the `request.approved` payload:

| Key field | From |
|---|---|
| `key_alias` | `<KEY_ALIAS_PREFIX>-<first 8 hex of request_id>-<stage>`, e.g. `req-3f2a9c41-trial` (deterministic) |
| `models` | catalog ids mapped to gateway aliases: identity, or `MODEL_ALIAS_MAP`. A request with no models (「不確定」) gets `DEFAULT_MODELS`. If that is empty too, the job fails instead of creating an all-models key, because LiteLLM treats `[]` as every model. |
| `max_budget`, `rpm_limit`, `tpm_limit` | `budget_usd`, `rpm`, `tpm` (formula in request-api's README) |
| `duration` | `duration`: `14d` for a trial, none (no expiry) or `FULL_KEY_DURATION` for full |
| `budget_duration` | `budget_duration`: `30d` for full keys (spend resets each period), none for a trial |
| `metadata` | `{request_id, team, requester, stage, managed_by: "key-provisioner"}` |

### Idempotency: one key per alias

Jobs can run twice: after a crash, a lost commit, a lease timeout or an admin retry. The provisioner handles
each case as follows.
1. It looks up the alias (`GET /key/list?key_alias=…&return_full_object=true`, exact match).
2. If the alias belongs to another request (`metadata.request_id` differs; possible with 8 hex digits), it uses
   the long alias `req-<32 hex>-<stage>` instead.
3. If the alias exists and the secret store's metadata holds the same `token_id` (the hashed key LiteLLM
   reports), the key was already stored: nothing is created, and `key.provisioned` is published again (its
   dedupe key prevents a second fan-out).
4. If the alias exists but the store does not hold it (crash between generate and store), the key is
   unreachable forever. It is deleted (`POST /key/delete {"keys": [token]}`) and a new one generated. LiteLLM
   itself also rejects a duplicate alias (400), so there is never a second key.
5. If storing a freshly generated key fails, that key is deleted at once and the job retried.

### Retries

Transient errors are retried with back-off of `RETRY_BASE_SECONDS` × 2ⁿ, at most 15 minutes, for the job's 5
attempts (about 7.5 minutes with the default). Transient means:
- the gateway or store is unreachable, or answers 5xx or 429;
- HTTP 401/403, so a rotated master key or Vault token is picked up.

After the last attempt, or at once for a permanent error, the provisioner publishes `key.failed`. Permanent
errors are:
- a 400 from LiteLLM;
- an invalid payload;
- no models;
- an invalid secret path;
- AWS `AccessDenied` / `ValidationException`.

The job is then parked `dead` for inspection. An admin retries with `POST /api/requests/{id}/reprovision`.

## Secret sinks

The stored value is a JSON object:
`{"api_key", "key_alias", "models", "stage", "expires_at", "request_id", "base_url"?}`. `base_url` comes from
`GATEWAY_PUBLIC_URL`.

Each sink also stores the metadata `{key_alias, token_id, request_id, stage}`. The provisioner reads that
metadata back, never the value, to recognise its own keys.

The path is `SECRET_PATH_TEMPLATE`, default `llmops/keys/{key_alias}`. Placeholders: `{key_alias}`,
`{request_id}`, `{request_id8}`, `{stage}`. A trial and its later full launch get separate keys and paths.

| `SECRET_SINK` | Writes | `secret_ref` | Fetch hint sent to the requester |
|---|---|---|---|
| `vault` | KV v2: `POST /v1/<VAULT_MOUNT>/data/<path>`, then `custom_metadata` on `/v1/<mount>/metadata/<path>` | `vault:<mount>/<path>` | `vault kv get -mount=<mount> -field=api_key <path>` |
| `aws-secretsmanager` | `CreateSecret` (or `PutSecretValue` + `TagResource` if it exists; restores a secret scheduled for deletion); metadata as tags `llmops:*`; optional `AWS_SM_KMS_KEY_ID` | the secret ARN | `aws secretsmanager get-secret-value --secret-id <ARN> …` |
| `file` | local development only: `<SECRET_FILE_DIR>/<path>.json` (mode 0600, atomic) + `.meta.json` | `file:<path>` | `cat <path>` |

Vault policy for the provisioner's token (KV v2 mount `secret`):
```hcl
path "secret/data/llmops/keys/*"     { capabilities = ["create", "update"] }
path "secret/metadata/llmops/keys/*" { capabilities = ["read", "create", "update"] }
```

AWS IAM for the provisioner:
- `secretsmanager:DescribeSecret`, `CreateSecret`, `PutSecretValue`, `TagResource` and `RestoreSecret` on
  `arn:aws:secretsmanager:<region>:<acct>:secret:llmops/keys/*`;
- `kms:GenerateDataKey` and `kms:Decrypt` if a customer-managed key is used.

`GetSecretValue` is **not** needed.

## Events

- Consumed: `request.approved`
  `{request_id, team, alias, models, stage, budget_usd, rpm, tpm, duration, budget_duration?, requester_email?, ref?, attempt?}`.
  Other events are acknowledged and ignored.
- Published:

| Event | Dedupe key | Payload (+ = additions to `contracts/events.md`) |
|---|---|---|
| `key.provisioned` | `key.provisioned:<request_id>:<stage>:<event id>` | `request_id, key_alias, secret_ref, stage, expires_at` + `ref, team, alias, requester_email, models, fetch_hint, reused` |
| `key.failed` | `key.failed:<request_id>:<stage>:<event id>` | `request_id, error` + `stage, key_alias, ref, team, alias, requester_email` |

`requester_email`, `team` and `ref` are copied from `request.approved` so the notifier can write to the
requester. Errors are passed through `redact()`.

## Configuration

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `DATABASE_URL` / `_FILE` | yes | – | PostgreSQL (`platform.events/jobs` only; this component never touches `requests.*`) |
| `LITELLM_URL` | yes | `LLM_GATEWAY_URL` | gateway admin base URL, e.g. `http://llm-gateway:4000` |
| `LITELLM_MASTER_KEY` / `_FILE` | yes | – | LiteLLM master key (re-read on every call, so a rotated Secret file is picked up) |
| `LITELLM_TIMEOUT` | no | `30` | seconds per admin call |
| `SECRET_SINK` | yes | – | `vault`, `aws-secretsmanager` or `file` (local only) |
| `SECRET_PATH_TEMPLATE` | no | `llmops/keys/{key_alias}` | secret path or name |
| `GATEWAY_PUBLIC_URL` | no | – | base URL written into the secret for the app team |
| `MODEL_ALIAS_MAP` | no | identity | JSON: catalog id → gateway alias or list, e.g. `{"claude-sonnet-5-5": "sonnet-tw"}` |
| `DEFAULT_MODELS` | no | – | comma-separated gateway aliases for requests without models |
| `KEY_ALIAS_PREFIX` | no | `req` | key alias prefix |
| `RETRY_BASE_SECONDS` | no | `30` | first retry delay |
| `HEALTH_PORT` | no | `8081` | probes |
| `WORKER_POLL_SECONDS` | no | `2` | queue poll interval when idle |
| `VAULT_ADDR` | with vault | – | e.g. `https://vault.vault.svc:8200` |
| `VAULT_TOKEN` / `_FILE` | with vault | – | token (re-read on every call, so a Vault agent can renew it) |
| `VAULT_MOUNT` | no | `secret` | KV v2 mount |
| `VAULT_NAMESPACE` | no | – | Vault Enterprise namespace |
| `VAULT_CACERT` | no | system / `SSL_CERT_FILE` | CA bundle for Vault's TLS certificate |
| `VAULT_TIMEOUT` | no | `10` | seconds |
| `AWS_REGION` | with aws | – | Secrets Manager region; credentials from the default chain (env keys, `AWS_CONFIG_FILE` with `credential_process` for IAM Roles Anywhere, instance role on the test box) |
| `AWS_SM_ENDPOINT_URL` | no | – | VPC endpoint or emulator URL |
| `AWS_SM_KMS_KEY_ID` | no | AWS-managed key | KMS key for new secrets |
| `SECRET_FILE_DIR` | no | `/tmp/llmops-secrets` | `file` sink directory |
| `DB_POOL_MAX`, `LOG_LEVEL` | no | `5`, `INFO` | shared settings |
| `HTTPS_PROXY`, `NO_PROXY` | no | – | put the in-cluster gateway and Vault in `NO_PROXY` |

## Secrets

The secrets it reads, all of which can be mounted as files with `*_FILE`:
- `LITELLM_MASTER_KEY`;
- `VAULT_TOKEN` or the AWS credentials;
- `DATABASE_URL`.

It creates the app teams' virtual keys and writes them only to the secret store. A key is never written to
logs, events, job payloads or the database, and it is held in memory only between generate and store. The
tests assert this.

## Outbound connections

- The LiteLLM admin API (in cluster).
- The secret store: Vault in cluster, or AWS Secrets Manager over HTTPS through `HTTPS_PROXY`.
- PostgreSQL.

## Resources

About 30 MiB RSS measured idle and while provisioning; boto3 adds about 30 MiB with the AWS sink. Suggested:
- requests `cpu: 20m, memory: 64Mi`, limits `cpu: 200m, memory: 192Mi`;
- 1 replica is enough (more are safe: SKIP LOCKED plus idempotency).

## Tests

```
TEST_DATABASE_URL=postgresql://… pytest components/key-provisioner/tests
LITELLM_TEST_URL=http://127.0.0.1:14013 LITELLM_TEST_MASTER_KEY=sk-… pytest components/key-provisioner/tests/test_kp_live_litellm.py
```

`tests/kp_fakes.py` is an in-memory LiteLLM admin API with the response shapes of v1.105.2. The sinks are
tested as follows:
- Vault against an `httpx.MockTransport`;
- AWS with botocore's `Stubber`;
- the file sink on a temporary directory.

The live test needs a real proxy with a database; the docstring in `test_kp_live_litellm.py` has the
`docker run` line.
