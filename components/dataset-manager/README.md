# dataset-manager

Imports approved security test datasets into the **datasets** bucket and keeps `index.json` pointing at the latest
approved version of each SEC item (`contracts/storage-layout.md`). test-orchestrator plans only items that have an entry
in `index.json`; test-runner reads the cases.

**Kind:** job (run to completion; exit 0 = success, 1 = failure, 2 = refused / bad input). No ports, no probes.

## Commands

```bash
python -m dataset_manager list [--json]                       # the 23 public secsuite datasets, licence, risk, what is active
python -m dataset_manager show <dataset | SEC-xx> [--json]    # one dataset or item with every imported version
python -m dataset_manager import <dataset | SEC-xx | all> --approved-by <alias> [--note TEXT]
python -m dataset_manager import-file <SEC-xx> <dataset_id> <file.jsonl | -> --license TEXT --approved-by <alias> \
        [--sensitivity none|low|medium|medium-high|high|highest] [--language zh-TW] [--source TEXT] [--title TEXT]
```

- `import jbb-harmful` imports one public dataset of the secsuite manifest (`src/dataset_manager/secsuite_manifest.json`,
  a copy of `tools/secsuite/manifest.json`) as dataset `jbb-harmful` under its SEC item.
- `import SEC-01` imports **every** public dataset of that item as one composite, `secsuite-sec-01`. Each case keeps its
  source dataset and `share`, so test-runner samples the sources in secsuite's proportions. `import all` does this for
  SEC-01..15. Use the composite: `index.json` holds one dataset per item (the contract), and that is how an item keeps
  all of secsuite's sources. (SEC-11's MITRE FRR set can be imported, but the platform catalog derives SEC-11 from
  every other result, so test-orchestrator does not run it.)
- `import-file` takes an internal golden set, one case per line: `{"id", "prompt" | "turns", "expected"?, "meta"?}`.
  `meta.scorer` picks the secsuite scorer; without it, the scorer is inferred from `expected` (needle, canary,
  forbidden_tools, rule, answer, ...) or from the item (SEC-01 harm, SEC-04 needle, SEC-10 overrefusal, ...). Each line
  is validated (required fields per scorer, regexes compile, ids unique) before anything is written. Use `-` to read
  stdin (`kubectl run -i ... -- import-file SEC-09 acme-golden - ...`).
- **Every import needs `--approved-by`**: several datasets contain harmful requests in clear text, so a named person
  approves each one. The alias is stored in the manifest.

Each import writes a new version (written once, never overwritten), then points `index.json` at it:

```
{SEC-xx}/{dataset_id}/v{N}/cases.jsonl     {id, prompt | turns, expected?, meta}
{SEC-xx}/{dataset_id}/v{N}/assets/<file>   files a scorer needs at run time (zhtw-terms: the OpenCC dictionaries)
{SEC-xx}/{dataset_id}/v{N}/manifest.json   written last; sec_id, dataset_id, version, source, license, language,
                                           modality, n, sha256 (of cases.jsonl), sensitivity, approved_by, approved_at,
                                           plus sources, files (url, bytes, sha256 of every download), redistribute
index.json                                 {"SEC-01": {"dataset_id": "...", "version": N}, ...}
```

Run one import at a time (`index.json` is read-modify-write). Downloads stay in memory (largest file about 3 MB), are
limited to HTTPS and 64 MB, and are only parsed as CSV / JSON / text.

## Configuration

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `S3_ENDPOINT_URL` | no | AWS S3 | self-hosted S3-compatible store, e.g. `http://object-store:7070` |
| `S3_REGION` | no | `us-east-1` | |
| `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | no | default AWS chain | **secret** (`S3_SECRET_ACCESS_KEY_FILE` works) |
| `S3_FORCE_PATH_STYLE` | no | true with an endpoint | |
| `BUCKET_DATASETS` | no | `llmops-datasets` | |
| `HTTPS_PROXY`, `NO_PROXY` | for `import` | | downloads go through the proxy |
| `SSL_CERT_FILE` | no | | CA bundle of a TLS-inspecting proxy (honoured by httpx) |
| `COMPONENT`, `LOG_LEVEL` | no | `dataset-manager`, `INFO` | |

No database. **Outbound** (only `import` of public datasets): `huggingface.co` and its download CDN (`cdn-lfs*.hf.co`,
`cas-bridge.xethub.hf.co`), `raw.githubusercontent.com`. `import-file`, `list` and `show` talk only to the object store.
Note: `docs/runtime-contract.md` does not list dataset-manager among the components with outbound access; the platform
team needs to allow these hosts for this job (or run imports where the proxy allows them).

**Resources:** requests 100m CPU / 256Mi, limit 1 CPU / 512Mi. **Events:** none (index.json is the hand-over).

## Changes from secsuite

Adapters (`adapters.py`) and the canary generator (`twtext.py`, same seed, so the same synthetic canaries) are ported
unchanged; they read downloaded bytes instead of a local cache. Cases are stored in the platform format (`normalise.py`);
the scorer, source, share, system prompt, tools and max_tokens move to `meta`, multi-turn scripts to `turns`, the variant
prompt of SEC-07 pairs to `meta.prompt_b`. `fetch --approve` became `import --approved-by <alias>`.

## Tests

`pytest components/dataset-manager/tests` (no network: downloads use `httpx.MockTransport`; storage is `MemoryStore`).
