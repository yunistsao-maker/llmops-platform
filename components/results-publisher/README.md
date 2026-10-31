# results-publisher

Turns a completed security test run into the portal's security data: the `security` key of the portal view model
(`contracts/fixtures/portal-view.sample.json`), written to the **curated** bucket for view-builder.

**Kind:** worker on queue `results-publisher`. Health on `HEALTH_PORT` (default 8081): `GET /healthz`, `GET /readyz`
(PostgreSQL and the curated bucket reachable). One-off rebuild: `python -m results_publisher publish <run_id>`.

## Output

```json
{"runId": "...", "date": "2026-10-31", "purpose": "pre-release", "litellm": "1.105.2", "judge": "Opus 5.5", "cases": 465,
 "status": "done",
 "models": {"claude-sonnet-5-5": {"label": "Sonnet 5.5", "counts": {"pass": 6, "fail": 4, "review": 1, "info": 4},
                                  "issues": [{"id": "SEC-06", "t": "...", "tEn": "...", "v": "fail", "rate": 0.15, "rule": "..."}]}},
 "items": [{"id": "SEC-01", "t": "...", "tEn": "...", "tier": "core", "layer": "model", "rule": "...", "ruleEn": "...",
            "res": {"Sonnet 5.5": {"v": "pass", "rate": 0.1, "n": 900, "upper95": 0.13,
                                   "routes": {"gw:onprem:claude-sonnet-5-5": {"v": "pass", "rate": 0.1, "n": 900, "upper95": 0.13}}}}}]}
```

- Names (`t`, `tEn`), tier, layer and the rule text come from `contracts/sec-catalog.json` (copied into the image).
  Rule text uses secsuite's wording (Chinese; English in `ruleEn`).
- A model can have several routes (direct per cloud, via the gateway). Its result for an item is its **worst** route
  (fail > error > review > pass > info > not_run); every route is kept under `res[label].routes`.
- Gateway-level items (SEC-16) are keyed `"gateway"` and do not count toward any model.
- `n` is case executions (cases x trials), as in secsuite; `cases` sums them over the run (not_run and SEC-11 excluded).
- `issues` = items whose result is fail, review or error.
- Labels: the model's name in the current portal release (`portal/releases/<id>/data/catalog.json`) without the
  prefixes in `LABEL_STRIP_PREFIXES` ("Claude Sonnet 5.5" -> "Sonnet 5.5", as in the fixture); otherwise derived from
  the model id. Judge label: `JUDGE_LABEL`, else the model that answered the judge calls (ignored when it is just the
  gateway alias), else the alias.

Written to the curated bucket:
- `portal/security/{run_id}.json` - every run (written once; a re-delivered event finds it unchanged).
- `portal/security/current.json` - `{"current": run_id, "key", "updated_at"}`, moved only when the run's purpose is in
  `CURRENT_PURPOSES` (default `pre-release`), its status is not `failed`, and it is not older than the current run. A
  smoke run after a gateway upgrade or an ad-hoc single-item run therefore does not replace a model's full results.
- When the pointer moves: `security.results.published {run_id, key}` (dedupe `security.results.published:<run_id>`).

## Configuration

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `DATABASE_URL` | yes | | PostgreSQL (`tests.plans`, `tests.results`, `tests.routes`) |
| `S3_ENDPOINT_URL`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_FORCE_PATH_STYLE` | no | AWS defaults | object store; the secret key is a **secret** |
| `BUCKET_CURATED`, `BUCKET_RUNS` | no | `llmops-<kind>` | write portal files / read run.json and summaries |
| `HEALTH_PORT` | no | 8081 | probes |
| `POLL_SECONDS` | no | 2 | queue poll interval |
| `CURRENT_PURPOSES` | no | `pre-release` | purposes that move the pointer (comma-separated) |
| `LABEL_STRIP_PREFIXES` | no | `Claude ` | prefixes removed from catalog names |
| `JUDGE_LABEL` | no | | fixed judge label (useful when the gateway only reports the alias) |
| `SEC_CATALOG_PATH` | no | `/app/contracts/sec-catalog.json` | |
| `COMPONENT`, `LOG_LEVEL` | no | `results-publisher`, `INFO` | |

No secrets beyond the database URL and the object store key. **Outbound:** none outside the cluster.
**Resources:** requests 50m CPU / 128Mi, limit 250m / 256Mi. One replica is plenty (more are safe).

**Events:** consumes `test.run.completed`; publishes `security.results.published`.

## Contract notes

- The fixture's `tier` held secsuite's A-D tiers; the platform catalog has `core` / `draft`, which is what is written
  (plus `layer`). Extra keys (`status`, `layer`, `ruleEn`, `upper95`, `routes`) are additions; nothing in the fixture's
  shape was removed. `rate` is `null` for not_run / error rows without attempts.

## Tests

`pytest components/results-publisher/tests` with `TEST_DATABASE_URL` set.
