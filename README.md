# llmops-platform

The back end of the company's LLMOps portal: it collects model releases and retirements, publishes the model
catalog, runs the security test suite (SEC-01 to SEC-32) against each model route, takes model access requests,
and answers questions about all of it. Everything except the models runs as containers on Kubernetes; the 14
components here talk only through PostgreSQL, the S3 API, OIDC and an OpenAI-compatible gateway (LiteLLM), so the
same images run on the AWS test box and on-prem.

| Repo | Holds | CI publishes |
|---|---|---|
| `llmops-platform` (this) | 14 components, `libs/`, `contracts/`, `deploy/components.yaml`, compose, docs | images to ECR; `platform/` and env bumps to llmops-deploy; contracts to llmops-portal-web |
| `llmops-portal-web` | the portal page (nginx) | `portal-web` image; env bumps to llmops-deploy |
| `llmops-deploy` | rendered manifests, Argo CD apps and environments, AWS test Terraform, promotion | — (Argo CD reads it) |

```
components/<name>/     one deployable unit each: Dockerfile, src/, tests/, README.md, requirements.lock
libs/llmops-common/    shared code copied into every Python image (config, logging, storage, queue, auth, LLM client)
contracts/             DB migrations, events, storage layout, test jobs, request schema, OpenAPI, view-model sample
deploy/components.yaml how each component runs; rendered into llmops-deploy platform/ and the compose file here
deploy/compose/        the whole platform on one machine with fake models
tools/                 mock-llm, docker-build.sh, render_deploy.py, export_openapi.py, smoke.py
docs/                  architecture, runtime contract, on-prem handoff, design review
```

## Components

| Flow | Component | Runs as |
|---|---|---|
| shared | db-migrate | Job (Argo CD sync hook before each rollout) |
| shared | llm-gateway | service: LiteLLM, the only way to a model |
| collect & publish | crawler-agent | CronJob 07:17 Asia/Taipei |
| collect & publish | publisher, view-builder | workers |
| user services | content-api, assistant-api, request-api | services (request-api also consumes key events) |
| user services | key-provisioner, notifier | workers |
| security tests | dataset-manager | Job (on demand) |
| security tests | test-orchestrator | service (API, planner, gateway watcher) |
| security tests | test-runner, results-publisher | workers (test-runner scales out) |

The page itself (`portal-web`) lives in llmops-portal-web; `deploy/components.yaml` still describes how it runs,
so one spec renders the whole system.

## Run it locally

```bash
make build                      # every image as llmops/<name>:dev (BUILD_CA=ca.pem if your network inspects TLS)
# the page: docker build -t llmops/portal-web:dev . in llmops-portal-web, or PORTAL_WEB_IMAGE=<ecr image>
make up                         # http://localhost:8080 (fake models; creates deploy/compose/.env from the example)
C="docker compose -f deploy/compose/docker-compose.yml -f deploy/compose/docker-compose.components.yml"
$C run --rm -v "$PWD/../llmops-portal:/seed:ro" publisher bootstrap --from /seed --by "$USER" --allow-invalid
$C run --rm -v "$PWD/../llmops-portal:/seed:ro" view-builder seed --no-release --build
make smoke                      # end-to-end through portal-web
```

## CI/CD

- **Pull requests** (`ci.yml`): tests of the changed components (all of them when `libs/`, `contracts/` or
  `tools/` change) against PostgreSQL; generated files current (`render_deploy.py --check`,
  `export_openapi.py --check`); manifests render; changed images built and scanned with Trivy.
- **main** (`release.yml`): the 14 images are built once, pushed to ECR as `:<git sha>` through GitHub OIDC (no
  stored AWS keys), with SBOM and provenance, and signed with cosign using the AWS KMS key. Then a PR in
  llmops-deploy renders `platform/main` and bumps `envs/aws-test`; it merges itself once validated and Argo CD
  deploys it.
- **Tags `v*`**: images also tagged `:<tag>`; a GitHub Release with the contracts bundle; PRs adding
  `platform/<tag>/` to llmops-deploy (then promote on-prem from there) and vendoring the contracts into
  llmops-portal-web.

Repository settings: branch protection on `main` (PRs, `ci` checks, CODEOWNERS review); variables `AWS_REGION`,
`ECR_REGISTRY`, `AWS_CI_PUSH_ROLE_ARN`, `COSIGN_KMS_KEY`; secrets `CI_APP_ID`, `CI_APP_PRIVATE_KEY` (GitHub App
with write access to llmops-deploy and llmops-portal-web). The roles and the KMS key come from llmops-deploy
`infra/aws-test/terraform/github.tf`.

## Develop

- Every component follows [docs/runtime-contract.md](docs/runtime-contract.md): UID 10001, read-only root, only
  environment variables, `/healthz` and `/readyz`, JSON logs, graceful SIGTERM.
- Components talk through events and object storage ([contracts/](contracts/)), never by calling each other.
- Images are built from the repo root: `docker build -f components/<name>/Dockerfile .`
- Base images pinned by digest; Python dependencies locked with hashes (`make lock`, needs `uv`); Renovate
  proposes updates, LiteLLM is upgraded only by hand.
- After changing `deploy/components.yaml`: `make render`; after changing an API: `make openapi`.
- `make test` runs the unit tests (PostgreSQL tests need `TEST_DATABASE_URL`).

Adding a component: copy an existing one, keep the runtime contract, add it to `deploy/components.yaml`, the
`Makefile` and `release.yml`, its ECR repository in llmops-deploy, and — if it consumes events — `contracts/events.md`
and `SUBSCRIPTIONS` in `libs/llmops-common/src/llmops_common/queue.py`.

## Security notes

- No secrets in git. Locally `deploy/compose/.env` (ignored); on the AWS test box the llmops-deploy bootstrap
  generates them; on-prem Vault or cluster Secrets.
- Each component has its own gateway virtual key limited to the model aliases it needs, with a budget.
- Only llm-gateway and test-runner reach cloud model endpoints, with short-lived credentials from an X.509
  certificate (IAM Roles Anywhere, Entra certificate credentials, GCP workload identity federation).
- The gateway does not log prompts or responses (`turn_off_message_logging`).
