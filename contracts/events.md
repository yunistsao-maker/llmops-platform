# Events

Published with `llmops_common.queue.publish(conn, type, payload, source=..., dedupe_key=...)` inside the
publisher's own transaction. Each subscriber gets one job in `platform.jobs` (queue = component name) with
payload `{"event": type, "event_id": n, "payload": {...}}`; handlers read `job.data`.
The table below and `SUBSCRIPTIONS` in `libs/llmops-common/src/llmops_common/queue.py` must stay identical.

| Event | Published by | Subscribers | Payload |
|---|---|---|---|
| `research.proposal.ready` | crawler-agent | publisher | `{run_date, run_id, proposal_keys: [..], roles: [..]}` |
| `release.published` | publisher | view-builder, test-orchestrator, notifier | `{release_id, previous_release_id, manifest_key, held: bool, changes: {routes_added: [..], prices_changed: [..], retirements: [..]}}` |
| `route.added` | publisher | test-orchestrator | `{model_id, provider, region, model_name, status}` one event per new route |
| `gateway.version_changed` | test-orchestrator | test-orchestrator, notifier | `{gateway, previous_version, version}` |
| `test.job.completed` | test-runner | test-orchestrator | `{plan_id, run_id, job_key, route_id, sec_ids: [..], status: done/failed, error}` |
| `test.run.completed` | test-orchestrator | results-publisher, notifier | `{plan_id, run_id, status: done/partial/failed}` |
| `security.results.published` | results-publisher | view-builder | `{run_id, key}` (curated key of the portal security data) |
| `editorial.updated` | editor tool or a person | view-builder | `{key}` |
| `request.submitted` | request-api | notifier | `{request_id, team, alias, gates: [..], next_gate}` |
| `request.decided` | request-api | notifier | `{request_id, gate, decision, comment, actor}` |
| `request.approved` | request-api | key-provisioner, notifier | `{request_id, team, alias, models: [..], stage: trial/full, budget_usd, rpm, tpm, duration}` |
| `key.provisioned` | key-provisioner | request-api, notifier | `{request_id, key_alias, secret_ref, stage, expires_at}` (never the key) |
| `key.failed` | key-provisioner | request-api, notifier | `{request_id, error}` |
| `model.status_changed` | publisher | notifier | `{model_id, model_name, from, to, eta?}`; `from` / `to` one of `watch`, `evaluating`, `trial`, `available`, `paused`, `retired`; `eta` optional ISO date or time |

Dedupe keys: `<event>:<natural id>` (for example `release.published:20261031T000000Z`) so that a retried
publisher does not fan out twice. For `model.status_changed` use `model.status_changed:<model_id>:<to>:<release_id>`.

## Model ids and routes
- `model_id` is the catalog id (`claude-sonnet-5-5`).
- A route id is `direct:<provider>:<region>:<model_id>` or `gw:<gateway>:<model_id>`
  (for example `direct:bedrock:us-east-1:claude-sonnet-5-5`, `gw:onprem:claude-sonnet-5-5`).
