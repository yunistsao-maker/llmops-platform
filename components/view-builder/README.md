# view-builder

Compiles the **portal view model**, the single JSON document the portal page loads
(`contracts/fixtures/portal-view.sample.json` shape: `asOf, built, repo, brief, news, actions, open, ret, models,
security, mock, gates, progress, hl, cdNew, cdPrice, secMatrix`, plus the fields added since — see
[Additions to the fixture shape](#additions-to-the-fixture-shape)), from what is in the curated bucket:

| Input (curated bucket) | Used for |
|---|---|
| `portal/manifest.json` → `portal/releases/{current}/data/*.json` | catalog, radar, lifecycle, sources, open-models, requirements, `models/<id>.json` deep dives, `litellm.json` (optional) |
| `portal/releases/{current}/release.json` | optional `source` label, shown as `repo` |
| `portal/editorial/current.json` | hand-written overlay (format below) |
| `portal/security/current.json` → `portal/security/{run_id}.json` | security results (fixture `security` shape) |
| `portal/security-matrix/current.json` | SEC-01..32 matrix (`contracts/fixtures/security-matrix.json` shape) |

Output: `portal/views/{view_id}/portal.json` (new key, compact UTF-8 JSON) and then the pointer
`portal/views/current.json` = `{"current": view_id, "release_id", "security_run_id", "built_at"}`.
`view_id` = `YYYYMMDDTHHMMSSZ-<first 12 hex of sha256(content)>`. A build whose content equals the current view
writes nothing (same id), so repeated events are cheap. Older views beyond `KEEP_VIEWS` are deleted (best effort).

It is a port of the prototype's `build_data.py` + `augment.py`; the hand-written dicts of those scripts now live in
the editorial overlay (`editorial/current.json` in this folder is the starter file). Built from the llmops-portal
repo data with the build date the prototype used (2026-10-30) the output is **byte-identical** to the fixture
(`tests/test_compile.py`).

**Kind:** worker (queue `view-builder`) plus a CLI. One replica is enough; more are safe (builds are serialised
with a PostgreSQL advisory lock).

## Commands
```
python -m view_builder worker     # default CMD: consume queue view-builder, health on HEALTH_PORT
python -m view_builder build [--today YYYY-MM-DD]
                                  # one-shot build from the bucket (CronJob or by hand); exit 3 = no release yet
python -m view_builder seed --no-release [--security-run FILE] [--matrix FILE] [--editorial FILE] [--build]
                                  # the platform's bootstrap, after `publisher bootstrap` made the first release:
                                  # editorial, matrix and security results only (the llmops-deploy bootstrap runs both)
python -m view_builder seed --data-dir DIR [--security-run FILE] [--matrix FILE] [--editorial FILE]
                            [--release-id ID] [--source LABEL] [--force] [--build] [--ensure-bucket]
                                  # without a publisher only (offline experiments): its manifest is not the
                                  # publisher's portal-manifest/v2, so the publisher and crawler-agent refuse it
                                  # bootstrap (local / AWS test): uploads every *.json under DIR (internal/ skipped)
                                  # as one release, a manifest with that one release, the editorial overlay, the matrix
                                  # and the security results (a legacy runs/security/<run>.json is converted).
                                  # --matrix / --editorial default to the copies in the image (/app/seed).
                                  # Refuses to replace an existing manifest without --force.
python -m view_builder compile --data-dir DIR [...same inputs...] [--today D] --out view.json
                                  # offline, no object storage (debugging, fixture comparison)
```
Example (AWS test box, data mounted read-only):
```
docker run --rm --read-only --tmpfs /tmp -e S3_ENDPOINT_URL=... -e S3_ACCESS_KEY_ID=... -e S3_SECRET_ACCESS_KEY=... \
  -v $PWD/llmops-portal:/portal:ro llmops/view-builder:dev \
  seed --data-dir /portal/data --security-run /portal/runs/security/20261012T151346-a55616.json --ensure-bucket --build
```

## Events
| Consumes | Action |
|---|---|
| `release.published` | rebuild from the manifest's current release (the event's `release_id` is only logged; a held release does not move the manifest) |
| `security.results.published` | rebuild with `portal/security/current.json` |
| `editorial.updated` | rebuild with `portal/editorial/current.json` |

Publishes no events: the moved pointer is the signal (content-api and assistant-api poll it).
No manifest yet → the job completes with a warning. Malformed inputs (bad JSON, missing release file, unsafe ids)
→ the job is parked as dead with the reason (retrying the same state cannot succeed; the next event rebuilds).
Object-storage or database errors → retried with backoff by the queue.

## Configuration
| Variable | Required | Default | Meaning |
|---|---|---|---|
| `DATABASE_URL` (or `_FILE`) | worker: yes; `build`: optional | — | PostgreSQL (queue + advisory lock). **Secret** (password). |
| `DB_POOL_MAX` | no | `5` | pool size |
| `S3_ENDPOINT_URL` | no | empty = AWS S3 | S3-compatible endpoint, e.g. `http://object-store:7070` |
| `S3_REGION` | no | `us-east-1` | |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` (or `_FILE`) | no | default AWS credential chain | **Secret** (secret key) |
| `S3_FORCE_PATH_STYLE` | no | `true` when an endpoint is set | |
| `BUCKET_CURATED` | no | `llmops-curated` | the only bucket used |
| `HEALTH_PORT` | no | `8081` | probes |
| `POLL_SECONDS` | no | `2` | queue poll interval when idle |
| `REBUILD_INTERVAL_SECONDS` | no | `3600` | periodic rebuild so date-relative fields stay current (`0` = off); unchanged content writes nothing |
| `PORTAL_TZ` | no | `Asia/Taipei` | "today" for `built`, the news window and `ret[].days` |
| `NEWS_WINDOW_DAYS` | no | `30` | radar cards last seen in the last N days (including today) |
| `KEEP_VIEWS` | no | `30` | views kept under `portal/views/` (`0` = never delete) |
| `SEED_DIR` | no | `/app/seed` | bundled `editorial.json` and `security-matrix.json` for `seed` |
| `COMPONENT`, `LOG_LEVEL` | no | `view-builder`, `INFO` | JSON logs on stdout |
| `HTTPS_PROXY`, `NO_PROXY` | no | — | honoured by the S3 client |

Object-storage rights on the curated bucket: read `portal/*`; write `portal/views/*` (and everything under `portal/`
for `seed`); list + delete under `portal/views/` for pruning (optional); `HeadBucket` for readiness.

## Probes, ports, network, resources
- Worker: `GET /healthz` (alive), `GET /readyz` (database and bucket reachable) on `HEALTH_PORT` 8081. CLI: exit code.
- Outbound: PostgreSQL and object storage only (inside the cluster).
- Image: UID 10001, read-only root filesystem (nothing is written locally), `/tmp` emptyDir optional.
- Suggested resources: requests 100m CPU / 192Mi, limits 1 CPU / 512Mi (a build reads ~1.5 MB of JSON, ~1 s).
- SIGTERM: finishes the current build and exits (measured < 1 s idle).

## Editorial overlay (`portal/editorial/current.json`, schema `portal-editorial/v1`)
Hand-written copy that restates facts already in the release data. Every block is optional.
```json
{
  "schema": "portal-editorial/v1",
  "brief": [{"t": "one-line summary", "p": "P0"}],
  "highlights": [{"id": "it-c3612f990f", "zh": "首頁重點（白話）", "en": "Home highlight"}],
  "countdown": {
    "new":   [{"date": "2026-10-31", "zh": "...", "en": "...", "wz": "補充", "we": "note", "link": "#news-open"}],
    "price": [{"date": null, "zh": "...", "en": "...", "wz": "...", "we": "...", "link": "#m-gemini-4-argon"}]
  },
  "models": {"claude-opus-5-5": {"one": "", "hi": [], "cau": [], "oneZ": "", "oneE": "", "hiZ": [], "hiE": [],
                                 "cauZ": [], "cauE": [], "brE": ["English title of each breaking change, same order"]}},
  "offerings": [
    {"model": "gpt-6-astra", "cloud": "gcp", "add": {"status": "private-preview", "since": "2026-10-21", "note": "...", "src": []}},
    {"model": "claude-opus-5-5", "cloud": "aws", "set": {"so": "card-updated", "soNote": "..."}},
    {"model": "claude-opus-5-5", "cloud": "azure", "default": {"geoPrice": {"scope": "data-zone-us", "in": 4.4, "out": 22.0}}}
  ],
  "availability": {
    "durations": {"G1": 3, "G2": 10, "G3": 3, "G4": 3, "G5": 5, "G6": 5, "G7": 2, "G8": 14, "G9": 2},
    "parallel": ["G3", "G4", "G5", "G6", "G7"],
    "models": {"claude-opus-5-5": {"state": "available", "eta": null, "note": "開通給先導團隊"}}
  },
  "mock": {"perf": {}, "gw": {}}
}
```
- `highlights[].id` is a radar card id; `sec`, `imp`, `date`, `last` come from the card (also when it is older than
  the news window). Unknown ids are skipped with a warning.
- `models`: without an entry the copy falls back to the catalog (first sentence of `summary`, first clause of two
  `strengths` / `weaknesses`; English falls back to Chinese). `brE` must match the deep dive's breaking changes
  one to one, else it is dropped (with a warning).
- `offerings`: `add` a cloud the catalog does not list yet, `set` fields on a listed one, `default` fields that are empty.
- `mock`: the "sample layout (fake data)" tables on the Tests page (they are not part of the release data files).
- `availability`: standard days per rollout gate (defaults above; G3–G7 run in parallel and count once, as the
  longest still open) and per-model overrides (`state`, `eta` YYYY-MM-DD or null, `note`); an override makes
  `basis` `"editorial"`. Bad values are reported as build notes and ignored.

Publish a change: write the file to `portal/editorial/current.json`, then publish `editorial.updated`
(`llmops_common.queue.publish(conn, "editorial.updated", {"key": "portal/editorial/current.json"}, source=...)`),
or run `python -m view_builder build`.

## Additions to the fixture shape
Additive only; every fixture key keeps its place and shape.
- `models[].avail` = `{state, eta, next_gate, basis, note?}` — whether the model can be requested, shared by the page
  and the chat. From the gate progress: `available` when G9 is done; `trial` when G8 is in progress; `paused` when a
  gate is blocked (no `eta`; `next_gate` names the blocked gate); otherwise `evaluating` with
  `eta` = build date + the standard days of the gates not done yet (`basis` `"gates"`), unless the editorial overlay
  overrides it (`basis` `"editorial"`). Built 2026-10-30: Sonnet 5.5 and Haiku 5.5 paused at G6 (security), the others
  evaluating with an ETA of 2026-11-30 (G1 done) or 2026-12-03 (G1 in progress).
- `models[].gw` = `{alias, providers: [{svc, model, note}], minVersion, recVersion, caveats}` — gateway name (the
  catalog id, as in the llm-gateway config), LiteLLM provider strings and version notes from the catalog.
- `models[].offs[cloud].zdr` — zero data retention (`available`, `on-request`, `not-available`, `unknown`).
- `gw` (top level) = `{recommended, minimum, avoid, asOf}` from `litellm.json`; `null` without that file.

## Differences from the fixture when built today
Only date-relative fields move (`tests/test_compile.py::test_today_keeps_keys_and_shapes`): `built`; `news` keeps
radar cards last seen in the last `NEWS_WINDOW_DAYS` days up to the build date (on 2026-10-31: 107 instead of 118,
the cards last seen 2026-10-01 drop out); `ret[].days` count from the build date and events more than 14 days past
drop out (85 instead of 86); availability ETAs count from the build date. Without the additions above, keys, record
shapes and every other block are identical.

## Tests
`pytest components/view-builder/tests` (MemoryStore; the fixture comparison needs the llmops-portal checkout at
`LLMOPS_PORTAL_DIR`, default `/home/claude/gh/llmops-portal`; the queue test needs `TEST_DATABASE_URL`).
