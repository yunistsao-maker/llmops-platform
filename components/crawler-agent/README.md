# crawler-agent

The LLMOps research agent (from `llmops-research-agent`, `app/llmops_research`, Strands Agents SDK) as a platform job.
Every day it runs the eight research roles one after another — Amazon Bedrock, Microsoft Foundry, Google Cloud,
vendor APIs, LiteLLM, open-weight models, the portal QA reviewer and the Release Radar editor — each reading official
sources, quoting them verbatim and submitting a validated **proposal** (never a change) for the portal data. At the
end it publishes `research.proposal.ready`; the `publisher` decides what goes live.

**Kind:** job / cronjob. Exit code 0 = every role ran.

```
python -m crawler_agent daily [--roles ROLE,...] [--dry-run] [--no-event]   # default CMD: the daily plan
python -m crawler_agent role ROLE [--request JSON] [--dry-run] [--no-event] # one role, with its daily request
python -m crawler_agent roles                                               # print the plan (no settings needed)
python -m crawler_agent announce --payload JSON                             # re-send an event a run could not publish
python -m crawler_agent --help                                              # works without any settings
```

| Exit | Meaning |
|---|---|
| 0 | every role ran (accepted or no proposal) and the event was published |
| 1 | a role failed (model / gateway error, result not saved) or the event could not be published; the other roles ran and what they produced is saved and announced |
| 2 | usage error |
| 3 | configuration / environment problem: nothing ran (missing `LLM_GATEWAY_URL`, unknown `SEARCH_BACKEND`, no portal release yet, ...) |
| 143 | terminated (SIGTERM): no further role starts, the running one is cancelled, proposals already made are announced |

## The daily plan (`src/crawler_agent/plan.py`)

Same order, requests and budgets (searches / fetches / tool calls) as the AWS orchestrator
(`infra/daily-orchestrator.yaml`): `bedrock-researcher`, `azure-researcher`, `gcp-researcher`, `vendor-api-researcher`,
`litellm-researcher` (8 / 14 / 60 each), `open-model-scout` (10 / 18 / 80), `portal-reviewer` (8 / 14 / 70),
`radar-editor` (weekly kind with the digest, 8 / 14 / 70, always last). A role that fails does not stop the others.
Roles hand notes to each other through `research/handoff/` exactly as before. One difference from AWS: nothing is
published between roles (the publisher replays all of a run's proposals afterwards, in this order), so every role
researches the release that was live when the run started.

## Schedule

Daily at **07:17 Asia/Taipei** (`schedule: "17 7 * * *"`, `timeZone: Asia/Taipei`). A run takes one to three hours
(each role is capped at `MAX_RUNTIME_SECONDS`, 25 min). CronJob settings: `concurrencyPolicy: Forbid`,
`backoffLimit: 0`, `restartPolicy: Never` (a retry would repeat hours of paid research; re-run a single role by hand
instead), `activeDeadlineSeconds: 14400`, `startingDeadlineSeconds: 3600`.

## Configuration (environment only; any secret may be given as `NAME_FILE=/path`)

| Variable | Required / default | Meaning |
|---|---|---|
| `DATABASE_URL` | required for the event (not with `--no-event` / `--dry-run`) — **secret** | PostgreSQL of the platform queue |
| `LLM_GATEWAY_URL` | required (gateway provider) | e.g. `http://llm-gateway:4000`; the OpenAI base URL is `<it>/v1` |
| `LLM_GATEWAY_KEY` | optional — **secret** | this component's gateway virtual key (sent as `Authorization: Bearer`) |
| `LLM_MODEL` | `agent-research` | gateway model alias (Claude Sonnet on Bedrock behind the gateway) |
| `LLM_TIMEOUT` | `600` | seconds per model call (one turn may write up to `MAX_TOKENS`) |
| `MAX_TOKENS` | `32000` | max output tokens per model turn |
| `MODEL_EXTRA_FIELDS` | empty | JSON object added to every chat-completions request body (e.g. a reasoning setting the gateway passes on). Never set `temperature` / `top_p` / `tool_choice`: Claude 5.5 answers 400 |
| `MODEL_PROVIDER` | `gateway` | `gateway`, or `scripted` (offline test double; `SCRIPT_FILE` = JSON script, default: a smoke script) |
| `S3_ENDPOINT_URL` | empty (AWS S3) | e.g. `http://object-store:7070` for the S3-compatible store |
| `S3_REGION` | `us-east-1` | |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | optional — **secret** | without them the default AWS credential chain is used |
| `S3_FORCE_PATH_STYLE` | `true` when `S3_ENDPOINT_URL` is set | |
| `BUCKET_CURATED` | `llmops-curated` | where the portal data and the research results live |
| `PORTAL_DATA_URI` | `s3://$BUCKET_CURATED/portal/` | the live release the agent researches against (follows `portal/manifest.json`) |
| `OUTPUT_URI` | `s3://$BUCKET_CURATED/research/` | results: `runs/`, `proposals/`, `handoff/`. Must stay in the curated bucket when events are published |
| `SEARCH_BACKEND` | `none` | `none` = no live search: the portal's own sources plus a curated list of official pages. A future provider registers itself in `research/web/search_backend.py` (`register_provider`) |
| `FETCH_TIMEOUT` / `FETCH_MAX_BYTES` | `20` / `3000000` | per page |
| `EXTRA_ALLOW_DOMAINS` | empty | more hosts the agent may fetch (comma-separated) |
| `EXTRA_OFFICIAL_DOMAINS` | empty | hosts the operator vouches for as a vendor's own documentation |
| `MAX_SEARCHES` / `MAX_FETCHES` / `MAX_TOOL_CALLS` | `30` / `40` / `200` | per role run; the daily plan lowers them per role |
| `MAX_TURNS` / `MAX_TOTAL_TOKENS` / `MAX_RUNTIME_SECONDS` | `120` / `6000000` / `1500` | per role run (spend and time guards) |
| `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY` | empty | honoured by the page fetcher, the gateway client and the S3 client. With a proxy the fetcher leaves DNS to it (it still refuses non-https, other ports, private names and literal private IPs). `NO_PROXY` must list the in-cluster names (`llm-gateway`, the object store, `.svc`, `.cluster.local`) |
| `SSL_CERT_FILE` / `AWS_CA_BUNDLE` | empty | CA bundle of a TLS-inspecting proxy (httpx / OpenAI client, and boto3) |
| `LOG_LEVEL` | `INFO` | `DEBUG` also logs tool results |
| `COMPONENT` | `crawler-agent` (Dockerfile) | |
| `RESEARCH_NOW` / `RESEARCH_TODAY` | unset | tests and replays only: pin the clock |

Logs are JSON lines on stdout: run and role start / finish, every tool call (tool name and a short input — queries,
URLs, never prompts or page text), the outcome counts and the proposal keys. The gateway key is never logged.

## Storage (contracts/storage-layout.md)

Reads `curated/portal/manifest.json` → `portal/releases/<current>/schema|data/...`. Writes, in the curated bucket:
`research/runs/<runId>.json` (every role run), `research/proposals/<id>.json` (accepted proposals),
`research/handoff/<YYYY-MM-DD>/<HHMMSS>-<role>-<runId>.json` (notes between roles) — the agent's existing layout.
Every key is new; nothing is overwritten. The agent keeps no page bytes (only parsed text in memory), so nothing is
written to the raw bucket.

## Events

| | Event | Payload |
|---|---|---|
| publishes | `research.proposal.ready` (once per run, dedupe `research.proposal.ready:<run_id>`) | `{run_date, run_id, proposal_keys, roles}`: `run_date` = Asia/Taipei day; `proposal_keys` = curated keys of the accepted proposals in role order; `roles` = the roles that ran |
| consumes | — | |

## Probes and ports

None (job). Success = exit code 0. SIGTERM: no new role starts, the running role is cancelled, the event for the
proposals already accepted is published, exit 143 within seconds.

## Outbound connections

In the cluster: `llm-gateway:4000` (model), the object store, PostgreSQL. Outside, through `HTTPS_PROXY`, HTTPS (443)
only, to research sources: the official domains in `research/web/tiers.py` (`github.com` (BerriAI/litellm releases),
`docs.aws.amazon.com`, `aws.amazon.com`, `learn.microsoft.com`, `azure.microsoft.com`, `techcommunity.microsoft.com`,
`ai.google.dev`, `docs.cloud.google.com`, `cloud.google.com`, `blog.google`, `deepmind.google`, `platform.claude.com`,
`docs.claude.com`, `docs.anthropic.com`, `*.anthropic.com`, `*.claude.com`, `developers.openai.com`,
`platform.openai.com`, `deploymentsafety.openai.com`, `*.openai.com`, `docs.litellm.ai`, `litellm.ai`, `pypi.org`,
`docs.nvidia.com`, `*.moda.gov.tw`, `*.fsc.gov.tw`, `aiec.org.tw`, `law.moj.gov.tw`, `eur-lex.europa.eu`,
`digital-strategy.ec.europa.eu`), every host the portal's `data/sources.json` already cites (about 110 today), and
`EXTRA_ALLOW_DOMAINS`. Use the FQDN allowlist of the project doc `egress-allowlist-2026-10-13` on the proxy. The
fetcher itself refuses anything but https on 443, credentials in URLs, private / link-local / metadata addresses, and
re-checks every redirect.

## Resources (suggested)

requests `cpu: 200m, memory: 512Mi`; limits `memory: 1Gi` (one role at a time; pages are capped at 3 MB). Needs
`/tmp` writable (emptyDir) and nothing else: the image runs as UID 10001 with a read-only root filesystem.

For `deploy/components.yaml`:

```yaml
  crawler-agent:
    uses: [postgres, s3, gateway, internet]
    gateway_models: [agent-research]
    env:
      SEARCH_BACKEND: none
    workloads:
      - {name: crawler-agent, kind: cronjob, args: [daily], schedule: "17 7 * * *",
         resources: {cpu: 200m, memory: 512Mi, memory_limit: 1Gi}}
```

## What changed from the AWS agent (and why)

| AWS | Platform |
|---|---|
| AgentCore Runtime entrypoint (`main.py`, `bedrock-agentcore`, ADOT) | a CLI process (`crawler_agent`); AgentCore code removed |
| Step Functions ran the 8 roles, each followed by a publish | `daily` runs them in-process in the same order; one event at the end |
| `boto3.client("s3")` for `s3://` locations | `research/objectstore.py`: `llmops_common.storage` (any S3-compatible endpoint, path style) |
| Bedrock model (`MODEL_PROVIDER=bedrock`, prompt caching) | Strands `OpenAIModel` against the gateway (`model/load.py`); `scripted` kept for tests |
| AgentCore Gateway web-search connector, AgentCore Browser | `SEARCH_BACKEND=none` (seed pages + portal sources) with a provider registry; no JavaScript rendering |
| `portal-manifest/v1` pointer | the publisher's `{"current", "history"}` pointer (the old one is still read) |

Validation, grounding, gates, prompts, skills, handoff and fetch safety are unchanged.

## Tests

```
uv venv .venv --python 3.13 && uv pip install --python .venv/bin/python --require-hashes -r requirements.lock
uv pip install --python .venv/bin/python pytest "moto[s3,server]>=5.1"     # test-only
TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/test_agent_a .venv/bin/python -m pytest -q tests
```

The agent's own suite (validation, grounding, web safety, roles, handoff — ported unchanged where the code is
unchanged) plus the platform adapters: `test_objectstore.py` (S3 endpoint, path style, against moto's server),
`test_gateway_model.py` (a whole run through a mock OpenAI-compatible gateway), `test_crawler_cli.py` (settings checks,
the daily run, a failing role, SIGTERM, the event in PostgreSQL). Nothing reaches the internet, a cloud or a real model.
