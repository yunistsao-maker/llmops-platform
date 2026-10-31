# Runtime contract (every component)

The on-prem platform team deploys our images on their Kubernetes; developers only hand over a Dockerfile.
So every component behaves the same way and needs no special knowledge to run.

## Image
- Built from the **repo root**: `docker build -f components/<name>/Dockerfile .` (components share `libs/`).
- Base images pinned by digest. Python dependencies installed from `requirements.lock` with `--require-hashes`.
- Runs as **UID/GID 10001**, never root. Works with a **read-only root filesystem**; writes only to `/tmp`
  (mount an `emptyDir` there).
- Python code is copied, not pip-installed: `PYTHONPATH=/app/lib:/app/src`.
- One process per container. `ENTRYPOINT` starts it; subcommands go in `CMD`.

## Kinds
| Kind | Example | Port | Probes |
|---|---|---|---|
| service | request-api | `PORT` (default 8080) | `GET /healthz`, `GET /readyz` on the same port |
| worker | key-provisioner | health on `HEALTH_PORT` (default 8081) | `GET /healthz`, `GET /readyz` |
| job / cronjob | crawler-agent, db-migrate | none | exit code 0 = success |

`/healthz` = the process is alive (liveness). `/readyz` = dependencies it needs are reachable (readiness).

## Configuration
- Only environment variables. Any secret may instead be a file: set `NAME_FILE=/path` (mounted Secret).
- Common settings (all optional unless the component's README says required):

| Variable | Meaning |
|---|---|
| `COMPONENT` | set in the Dockerfile; appears in every log line |
| `LOG_LEVEL` | `INFO` (default), `DEBUG`, ... |
| `DATABASE_URL` | `postgresql://user:pass@host:5432/llmops` |
| `S3_ENDPOINT_URL` | empty for AWS S3; `http://object-store:7070` for a self-hosted S3-compatible store |
| `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_FORCE_PATH_STYLE` | object storage access |
| `BUCKET_RAW`, `BUCKET_CURATED`, `BUCKET_DATASETS`, `BUCKET_RUNS` | bucket names (default `llmops-<kind>`) |
| `LLM_GATEWAY_URL`, `LLM_GATEWAY_KEY`, `LLM_MODEL` | gateway base URL, this component's virtual key, model alias |
| `AUTH_MODE`, `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL`, `OIDC_GROUPS_CLAIM` | token checks (services) |
| `ROLE_<NAME>` | groups that hold a role, e.g. `ROLE_ADMIN=llmops-admins` |
| `HTTPS_PROXY`, `NO_PROXY` | outbound proxy; honoured by every HTTP client |

## Logging
JSON lines on stdout: `ts`, `level`, `component`, `logger`, `msg`, plus fields. No secrets, prompts or
personal data in logs (use `llmops_common.log.redact` for anything that may carry a token).

## Shutdown
SIGTERM: stop taking new work, finish the current request or job, exit within 30 s
(`terminationGracePeriodSeconds: 30`). Workers that are killed anyway lose nothing: an unfinished job's
lease expires and another worker takes it.

## Data and messaging
- PostgreSQL schemas are created only by `db-migrate` (run it before rolling out a new version).
- Components talk through events (`contracts/events.md`) and object storage (`contracts/storage-layout.md`),
  not by calling each other, except `portal-web`, which forwards `/api/*` to the services.
- Every write to object storage is a new key or an atomic overwrite of a small pointer file.

## Outbound network
Only these talk to anything outside the cluster, always through `HTTPS_PROXY` on-prem:
`crawler-agent` (research sources), `llm-gateway` and `test-runner` (cloud model endpoints; test-runner also the
SEC-16 feeds), `dataset-manager` (public datasets, only when importing), `notifier` (SMTP relay or chat webhooks).
`key-provisioner` reaches the gateway's admin API and the secret store (Vault), both inside the company network;
services that verify user tokens fetch the IdP's public keys. Everything else stays inside the namespace.
