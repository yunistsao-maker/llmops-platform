# Handoff to the on-prem platform team

Developers hand over a Dockerfile per component; the platform team runs them on the company Kubernetes.
This page is everything the platform team needs to know. All components follow the same
[runtime contract](runtime-contract.md), so none of them needs special treatment.

## What we provide and what we need

| Platform team provides | Project provides |
|---|---|
| Kubernetes, registry, CI build | 15 Dockerfiles and their code; `docker build -f components/<name>/Dockerfile .` from the repo root |
| PostgreSQL (one database, `llmops`; plus `litellm` if our gateway is used) | schema migrations, run by the `db-migrate` Job before each rollout |
| S3-compatible storage, four buckets | bucket layout and retention ([storage-layout](../contracts/storage-layout.md)) |
| OIDC IdP and the login in front of portal-web | token checks in every API; role mapping through `ROLE_<NAME>` variables |
| Forward proxy and FQDN allowlist | the list of domains each component needs (below) |
| Internal CA and certificate rotation | how each cloud trusts a certificate (below) |
| Vault or cluster Secrets | the keys each component's Secret must hold (below) |

The `llmops-deploy` repo holds the rendered manifests (`platform/<version>/base`, run by Argo CD on the AWS test
box) and the environments; `envs/onprem-staging` there shows the on-prem differences. Use them as they are or as
a reference.

## Components

Each row is one workload. Every component reads ConfigMap `llmops-common` (non-secret settings) and its own
Secret `llmops-<component>`. Pods that go outside the cluster also read ConfigMap `llmops-proxy`
(`HTTPS_PROXY`, `NO_PROXY`); llm-gateway and test-runner read `llmops-cloud` (cloud identity settings).
"Talks to" values: `postgres`, `s3`, `gateway` (llm-gateway, port 4000), `internet` (through the proxy).

<!-- BEGIN generated: tools/render_deploy.py -->

| Component | Runs as | Command | Port / schedule | Secret keys (`llmops-<component>`) | Talks to |
|---|---|---|---|---|---|
| db-migrate | Job (run on demand) | `migrate` | - | `DATABASE_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | postgres, s3 |
| bucket-init | Job (run on demand) | `ensure-buckets` | - | `DATABASE_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | postgres, s3 |
| llm-gateway | Deployment + Service | `--port 4000 --config /app/llmops-config/onprem.yaml` | 4000 (`/health/liveliness`, `/health/readiness`) | `DATABASE_URL`, `LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY` | postgres, internet |
| crawler-agent | CronJob | `daily` | `17 7 * * *` Asia/Taipei | `DATABASE_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `LLM_GATEWAY_KEY` | postgres, s3, gateway, internet |
| publisher | Deployment (worker) | `worker` | health 8081 | `DATABASE_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | postgres, s3 |
| portal-web | Deployment + Service | `(image default)` | 8080 (`/healthz`, `/readyz`) | - | - |
| content-api | Deployment + Service | `(image default)` | 8080 (`/healthz`, `/readyz`) | `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | s3, oidc |
| assistant-api | Deployment + Service | `(image default)` | 8080 (`/healthz`, `/readyz`) | `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `LLM_GATEWAY_KEY` | s3, gateway, oidc |
| request-api | Deployment + Service | `serve` | 8080 (`/healthz`, `/readyz`) | `DATABASE_URL` | postgres, oidc |
| key-provisioner | Deployment (worker) | `run` | health 8081 | `DATABASE_URL`, `LITELLM_MASTER_KEY` | postgres, gateway-admin |
| notifier | Deployment (worker) | `run` | health 8081 | `DATABASE_URL` | postgres, internet |
| view-builder | Deployment (worker) | `worker` | health 8081 | `DATABASE_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | postgres, s3 |
| dataset-import | Job (run on demand) | `import all --approved-by __APPROVER__` | - | `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | s3, internet |
| test-orchestrator | Deployment + Service | `(image default)` | 8080 (`/healthz`, `/readyz`) | `DATABASE_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `GW_KEY` | postgres, s3, gateway, oidc |
| test-runner | Deployment (worker) | `worker` | health 8081 | `DATABASE_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `TARGET_GATEWAY_KEY`, `JUDGE_GATEWAY_KEY` | postgres, s3, gateway, internet |
| results-publisher | Deployment (worker) | `(image default)` | health 8081 | `DATABASE_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | postgres, s3 |

Optional secret keys: key-provisioner: `VAULT_TOKEN`; notifier: `SMTP_PASSWORD`, `WEBHOOK_URLS`

<!-- END generated -->

Rollout order: run `db-migrate` (Job) first; then the rest in any order. Migrations only add things, so the
previous version keeps working while the new one rolls out.

## Settings in `llmops-common`

| Key | On-prem value |
|---|---|
| `S3_ENDPOINT_URL`, `S3_REGION`, `S3_FORCE_PATH_STYLE` | the company S3-compatible endpoint |
| `BUCKET_RAW`, `BUCKET_CURATED`, `BUCKET_DATASETS`, `BUCKET_RUNS` | bucket names |
| `LLM_GATEWAY_URL` | `http://llm-gateway:4000`, or the company gateway |
| `AUTH_MODE`, `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL`, `OIDC_GROUPS_CLAIM` | `oidc` and the IdP's values |
| `ROLE_ADMIN` and the other `ROLE_<NAME>` keys | IdP groups that hold each role |

## Secrets

One Secret per component, named `llmops-<component>`, with the keys in the table above. Recommendations:

- **PostgreSQL:** one login per component, each with rights only on what it uses; `db-migrate` is the only one
  that may change the schema. Every worker needs `platform.events` / `platform.jobs`; beyond that:
  request-api `requests.*` (including `requests.subscriptions`), notifier SELECT on `requests.subscriptions`,
  test-orchestrator, test-runner and results-publisher `tests.*`.
- **S3:** one access key per component, with a bucket policy limited to what it reads and writes
  ([storage-layout](../contracts/storage-layout.md) says who writes where).
- **Gateway keys:** one virtual key per component, limited to the model aliases in `deploy/components.yaml`
  (`gateway_models`). `bootstrap/gw_keys.py` in llmops-deploy shows how we issue them on the test box.
- **llm-gateway:** `LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY` (set once, never change; it encrypts stored
  credentials), `DATABASE_URL` for its own database.

## Outbound domains

| Component | Domains |
|---|---|
| llm-gateway, test-runner | AWS: `bedrock-runtime.<region>.amazonaws.com`, `rolesanywhere.<region>.amazonaws.com`; Azure: the Foundry resource's domain, `login.microsoftonline.com`; Google: `<region>-aiplatform.googleapis.com`, `sts.googleapis.com`, `iamcredentials.googleapis.com` |
| crawler-agent | the research sources: the existing 84-domain allowlist (project doc `egress-allowlist-2026-10-13`) |
| notifier | the SMTP relay or the chat webhook host |
| key-provisioner | Vault, if Vault is the secret store |

## Cloud identities from certificates

Only llm-gateway and test-runner get a certificate (mounted at `/var/run/secrets/rolesanywhere/tls.crt` and
`tls.key`), issued by the company CA with the component name as CN.

| Cloud | Setup on the cloud side | Settings (`llmops-cloud`) |
|---|---|---|
| AWS Bedrock | IAM Roles Anywhere trust anchor = company CA; a profile and a role limited to the allowed models; trust policy conditions on the anchor and the certificate CN | `AWS_REGION`, `ROLES_ANYWHERE_TRUST_ANCHOR_ARN`, `ROLES_ANYWHERE_PROFILE_ARN`, `ROLES_ANYWHERE_ROLE_ARN` |
| Azure AI Foundry | Entra app registration with the certificate; role on the Foundry resource; disable API-key auth | tenant ID, client ID, certificate path (gateway config) |
| Google Vertex AI | workload identity pool with an X.509 provider trusting the company CA; service account impersonation limited to Vertex | credential configuration file (gateway config) |

The AWS path is exercised on the test box (Terraform in llmops-deploy: `infra/aws-test/terraform/rolesanywhere.tf`).
Until a cloud's certificate identity is set up, the gateway can use an API key from its Secret instead; no
code changes either way.

## Running it

- Health: services answer `/healthz` (alive) and `/readyz` (dependencies reachable) on their port; workers on 8081.
- Logs: one JSON object per line on stdout (`ts`, `level`, `component`, `msg`, ...). No prompts or secrets.
- Scaling: test-runner scales out freely; other workers can too (jobs are claimed with `SKIP LOCKED`);
  services are stateless.
- Backups: PostgreSQL (company backup); buckets `curated`, `datasets`, `runs` (turn on versioning);
  `raw` can expire after 180 days.
- Sandbox: SEC-15, 22 and 27 run untrusted output; use gVisor or Kata for those runners if the cluster offers it.
