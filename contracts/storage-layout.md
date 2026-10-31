# Object storage layout

Four buckets, names from `BUCKET_*`. All JSON is UTF-8. The first release of a new platform comes from
`publisher bootstrap --from <llmops-portal checkout>`; view-builder's `seed --no-release` then adds the editorial file,
the security matrix and the last security run (the llmops-deploy bootstrap does both on test clusters). Pointer files are small and overwritten atomically;
everything else is written once under a new key.

## raw (crawler-agent writes; crawler-agent and auditors read)
```
{host}/{YYYY-MM-DD}/{sha256}.{ext}        page bytes as fetched (html, pdf, json, txt)
{host}/{YYYY-MM-DD}/{sha256}.meta.json    {url, final_url, fetched_at, status, content_type, sha256, tier}
```
Retention: 180 days (lifecycle rule).

## curated (the portal's source of truth)
```
research/runs/...            agent run logs           (crawler-agent; layout kept from llmops-research-agent)
research/proposals/...       proposals waiting for the publisher
research/handoff/...         notes between agent roles
portal/releases/{release_id}/schema/*        the portal schema the release was validated against
portal/releases/{release_id}/data/*.json     published data files (catalog, lifecycle, radar, sources, ...)
portal/releases/{release_id}/release.json    {schema: "portal-release/v2", release_id, created_at, source_proposals, held,
                                              parent, source, changes, files: {path: {sha256, bytes}}}
portal/manifest.json                          pointer, written only by the publisher: {schema: "portal-manifest/v2", current,
                                              published_at, sequence, previous, source, release_sha256, history: [...],
                                              held: [held releases waiting for a person]}
pipeline/...                                  the publisher's own records (locks, decisions)
portal/editorial/current.json                 highlights, countdown, plain-language model copy (view-builder input)
portal/security-matrix/current.json           SEC-01..32 matrix (llmops/data/security-matrix-2026-10.json shape)
portal/security/{run_id}.json                 portal security data for one run (results-publisher)
portal/security/current.json                  pointer: {"current": run_id}
portal/views/{view_id}/portal.json            the compiled view model the web page loads (view-builder)
portal/views/current.json                     pointer: {"current": view_id, "release_id", "security_run_id", "built_at"}
```

## datasets (dataset-manager writes after approval; test-runner reads)
```
{SEC-xx}/{dataset_id}/v{N}/cases.jsonl   one case per line: {id, prompt | turns, expected?, meta}
{SEC-xx}/{dataset_id}/v{N}/manifest.json {sec_id, dataset_id, version, source, license, language, modality,
                                         n, sha256, sensitivity, approved_by, approved_at}
index.json                               {"SEC-01": {"dataset_id": "...", "version": 3}, ...} latest approved
```

## runs (test-runner writes; results-publisher and the security team read)
```
{run_id}/run.json                         {run_id, plan_id, created_at, gateway_version, judge_model, routes, items}
{run_id}/{route_id}/{sec_id}.jsonl        case-level lines (redacted responses), one per case x trial
{run_id}/{route_id}/{sec_id}.summary.json {n, trials, failures, errors, rate, upper95, verdict, rule, models_seen, stop_reasons}
```
Retention: case-level lines 90 days; summaries kept.
