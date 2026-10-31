# publisher

The Publish step of the LLMOps portal pipeline (from `llmops-research-agent`, `pipeline/portal_pipeline/`: the Publish
Lambda) as a platform worker. On `research.proposal.ready` it replays the run's proposals on the **live** portal data
with the same code the agent validated them with, applies the existing gates (refuse / hold for a person / publish),
writes a new immutable release under `portal/releases/{release_id}/`, moves `portal/manifest.json`, and announces it.
No model, no internet.

**Kind:** worker on queue `publisher` (health on `HEALTH_PORT`, default 8081).

```
python -m publisher [worker]          # default CMD: consume queue "publisher" until SIGTERM
python -m publisher once              # process the jobs that are due, then exit (tests, catching up by hand)
python -m publisher status            # live release, held releases, pause flag (one JSON line)
python -m publisher approve RELEASE|PROPOSAL --by NAME [--proposal ID ...] [--sha256 H]
python -m publisher discard RELEASE|PROPOSAL --by NAME [--reason TEXT]
python -m publisher rollback [--to RELEASE] [--if-current RELEASE] [--unlisted] --by NAME [--reason TEXT]
python -m publisher pause --by NAME [--reason TEXT] | resume --by NAME
python -m publisher bootstrap --from DIR|s3://bucket/prefix/ --by NAME [--force] [--allow-invalid] [--note TEXT]
python -m publisher --help            # works without any settings
```
Operator commands run as a one-off pod of this image (`kubectl run` / a Job with the same env and Secret); exit codes:
0 done · 1 refused (reason in the log) · 2 usage · 3 configuration problem.

## What happens to a run's proposals

Each proposal of the event is replayed in role order on the live data plus the proposals before it that go live in
this batch, and judged by the unchanged gates (`refusals`, `holds`, the review of the real difference — see the AWS
repo's `docs/architecture.md` §4):

- **refused** (never publishable: wrong shape, made against the sample data, does not apply to the live data, touches
  files outside research data, ...): left out; filed under `curated/pipeline/rejected/<proposal id>.json` and logged.
- **published**: everything that passes goes into **one new live release**; the pointer moves in one write.
- **held** (needs a person: `PUBLISH_MODE=review`, deletions, rewrites, new deep dives, open-model changes,
  instruction-like text, paused auto-publishing, ...): replayed on top of the new live release into **one held
  release** — complete and immutable, but not live — listed in the pointer's `held` with its proposals and reasons.
  `approve` replays the held proposals on whatever is live then (approval skips the holds, never the validation)
  and publishes a new release; `discard` takes them off the list. The records that were held are kept under
  `curated/pipeline/held/<release>/<sha256>.json`, so what is approved is exactly what was shown.

A rollback makes an earlier live release current again and pauses automatic publishing (every proposal is then held
until `resume`), as before.

## Configuration (environment only; any secret may be given as `NAME_FILE=/path`)

| Variable | Required / default | Meaning |
|---|---|---|
| `DATABASE_URL` | **required** — **secret** | queue, events and the publish lock |
| `S3_ENDPOINT_URL` | empty (AWS S3) | e.g. `http://object-store:7070` |
| `S3_REGION` | `us-east-1` | |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | optional — **secret** | without them the default AWS credential chain is used |
| `S3_FORCE_PATH_STYLE` | `true` when `S3_ENDPOINT_URL` is set | |
| `BUCKET_CURATED` | `llmops-curated` | |
| `PUBLISH_MODE` | `review` | `review`: every proposal waits for approval; `auto`: what passes every gate goes live. Anything else stops the worker (config error) |
| `AUTO_MAX_OPS` | `60` | in auto mode, a proposal changing more values (or with more operations) waits |
| `AUTO_ALLOW_REMOVE` | `false` | in auto mode, deletions wait unless `true` |
| `ROUTE_REGION_BEDROCK` / `_VERTEX` / `_AZURE` / `_ANTHROPIC` | `us-east-1` / `global` / `global` / `global` | the region in route ids and `route.added` (the catalog does not say where the platform calls a cloud) |
| `PUBLISH_LOCK_TIMEOUT` | `300` | seconds to wait for another publisher holding the lock |
| `HEALTH_PORT` | `8081` | |
| `DB_POOL_MAX` | `5` | |
| `LOG_LEVEL` | `INFO` | |
| `COMPONENT` | `publisher` (Dockerfile) | |

## Storage (contracts/storage-layout.md, curated bucket)

| Key | |
|---|---|
| `research/proposals/<id>.json` (read) | the proposals named by the event (a run record `research/runs/<id>.json` is accepted too) |
| `portal/releases/{release_id}/release.json` | `{release_id, created_at, source_proposals, held}` first, then `parent`, `source`, file `changes`, `catalog_changes`, every proposal of the batch with its status / reasons / review / operations / quotes, and the sha256 of every file |
| `portal/releases/{release_id}/schema/...`, `data/...`, `runs/...`, `config/...` | the complete data of the release (`data/radar.json` included) |
| `portal/manifest.json` | the pointer: `{"current": release_id, "history": [last 30 live releases], "held": [...], "published_at", "sequence", "previous", "source", "release_sha256", "auto_publish"}` |
| `pipeline/rejected/`, `pipeline/held/`, `pipeline/decided/` | the publisher's own records (refusals, held records, approvals and discards) |

Release ids are `llmops_common.ids.new_release_id()` (`20261031T061346Z`; the next free second when two releases are
made at once). Releases are never overwritten or deleted; only the pointer is replaced. All writers of `portal/` and
`pipeline/` hold a PostgreSQL advisory lock, so the compare-and-swap of the pointer needs no S3 conditional writes
(self-hosted S3-compatible stores do not all support them).

## Events

| | Event | Payload / dedupe key |
|---|---|---|
| consumes | `research.proposal.ready` | `{run_date, run_id, proposal_keys, roles}` |
| publishes | `release.published` — one per release made (the live one, and the held one with `held: true`) | `{release_id, previous_release_id, manifest_key: "portal/manifest.json", held, changes: {routes_added, prices_changed, retirements}}`; `release.published:<release_id>` (`…@<sequence>` when a rollback makes a release live again) |
| publishes | `route.added` — one per new route of a release that went live | `{model_id, provider, region, model_name, status}`; `route.added:<release_id>:<route_id>` |
| publishes | `model.status_changed` — per model whose platform status moved in a release that went live | `{model_id, model_name, from, to}`; `model.status_changed:<model_id>:<to>:<release_id>` |

`changes` is a diff of `data/catalog.json` between the previous and the new release (`src/publisher/catalog_diff.py`):
a **route** is a callable model offering on a cloud — `aws/bedrock` → `bedrock`, `azure/foundry` → `azure`,
`gcp/agent-platform` → `vertex`, `vendor-direct/anthropic-api` → `anthropic` (route id
`direct:<provider>:<region>:<model_id>`; `model_name` is the Bedrock inference profile that fits the region, else the
provider's model id; offerings `announced` / `retired`, and the OpenAI / Gemini APIs, which have no platform provider,
are not routes). `prices_changed` lists the price fields that changed per offering and scope; `retirements` lists
offerings whose status became legacy / deprecated / extended-access / retired, whose retirement or deprecation date
was set or moved, or that left the catalog. Model status = the catalog's `platformStatus` mapped to the event's terms:
watch → watch, evaluating → evaluating, beta → trial, ga and deprecating → available, restricted and not-planned →
paused, removed → retired (a model new to the catalog comes `from: watch`).
Events are published after the pointer switch, in one transaction; if the process dies in between, the retried job
recognises its event (the trigger is recorded in the pointer) and announces the same releases again — the dedupe keys
keep each event to one. SNS mails of the AWS pipeline are replaced by these events (the notifier subscribes).

## Probes and ports

`GET /healthz` (process alive) and `GET /readyz` (PostgreSQL answers and the curated bucket is reachable) on
`HEALTH_PORT` (8081). Suggested probe `timeoutSeconds: 10`. SIGTERM: the current job finishes, then the process exits
(a killed job's lease expires and it is retried; see the Events paragraph).

## Outbound connections

Inside the cluster only: PostgreSQL and the object store. Nothing outside.

## Resources (suggested)

requests `cpu: 100m, memory: 256Mi`; limits `memory: 1Gi` (a whole release is held in memory while it is replayed and
written; the largest proposal the gates work through stays under a fifth of 1 GiB — `test_publish_gates.py`). One
replica is enough; more are safe (the lock serialises publishing). UID 10001, read-only root filesystem, `/tmp` only.

For `deploy/components.yaml`:

```yaml
  publisher:
    uses: [postgres, s3]
    env:
      PUBLISH_MODE: review
    workloads:
      - {name: publisher, kind: worker, args: [worker], resources: {cpu: 100m, memory: 256Mi, memory_limit: 1Gi}}
```

## First release

The publisher needs a live release before it can replay anything (until then a job is retried every 10 minutes, five
times, then parked as dead for a person — re-enqueue it after bootstrapping). Publish the portal repo's data once:

```
kubectl run publisher-bootstrap --rm -it --image=<registry>/llmops/publisher:<tag> --overrides=... -- \
    bootstrap --from s3://llmops-curated/import/ --by <name>      # after copying schema/ and data/ there with any S3 tool
```

(or mount a checkout and use `--from /portal`). The current portal data has free-text version ranges in
`data/litellm.json` that the validators refuse; `--allow-invalid` publishes anyway and lists them as warnings.

## The shared portal data model

The replay, the schema and cross-file checks and the change labels are the research agent's own modules
(`components/crawler-agent/src/research/{apply.py, portal/, validate/injection.py, validate/urls.py}`), copied into this
image by the Dockerfile — one copy of the rules for both sides, as the AWS Lambda bundle did (`scripts/build_lambdas.py`).

## Tests

```
uv pip install --python .venv/bin/python --require-hashes -r requirements.lock
uv pip install --python .venv/bin/python pytest "moto[s3]>=5.1"     # test-only (moto: the S3 variant of every storage test)
TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/test_agent_a_pub .venv/bin/python -m pytest -q tests
```

The pipeline's own suites (`test_publish.py`, `test_publish_gates.py`, `test_releases.py`, mutation gaps), run on
MemoryStore and on moto's S3, plus `test_batch.py` (a run's proposals together), `test_catalog_diff.py`,
`test_handler.py` (the worker against PostgreSQL: events, a crash between pointer and events, the lock, probes) and
`test_cli.py`.
