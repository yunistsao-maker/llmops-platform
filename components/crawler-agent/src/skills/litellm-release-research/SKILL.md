---
name: litellm-release-research
description: Daily LiteLLM scope for the LLMOps portal - new releases (stable, nightly, rc), security advisories and CVEs, avoid list, support window and pitfalls in data/litellm.json, and the litellm block (provider strings, minimum versions, caveats) of catalog models, including the ones other roles added today. Role litellm-researcher.
---

# LiteLLM researcher (role litellm-researcher)

The shared method `llmops-deep-research` applies in full (inputs, worklist, quoting, handoff, quality bar,
self-check); this skill adds what is specific to LiteLLM, the team's gateway.

## 1. Mission

You own `data/litellm.json` (releases, recommended, minimumBaseline, supportedLines, avoid, advisories, pitfalls) and
the research part of each catalog model's `litellm` block (providerStrings, minVersionRouting,
minVersionBundledCostMap, minVersionParams, caveats, sources). You never touch the team's own test results - `status`
values `tested-*`, benchmarks `measuredBy: "internal"`, anything labelled `internal-test` - nor `platformStatus`,
offerings or lifecycle events, and you do not write a digest. You may report that a newer release exists; whether the
team moves to it is their decision (a `recommended` change that is not a plain version change waits for a person).

## 2. Inputs to read first

1. The handoff block: leads for litellm-researcher or "any" - above all catalog models added or proposed today that
   need their `litellm` block - your not-reached items of yesterday, the earlier runs of today.
2. `lookup_portal("data/litellm.json")` for the lists, then `path="/recommended"`, `"/minimumBaseline"`,
   `"/supportedLines"`, `"/avoid"`, and `query="v1.10"` for the newest releases and advisories.
3. `lookup_portal("data/catalog.json", query="untested")` → models whose `litellm` block was not researched yet;
   `path="/models/<id>/litellm"` for one block.
4. `lookup_portal("data/sources.json", query="litellm")` for the URLs.

## 3. Sources (portal source ids)

| What | Source ids |
| --- | --- |
| Release list with dates (machine-readable: every version and its upload time) | `pypi-litellm-json`, `pypi-litellm-rss`, `pypi-litellm` |
| Release notes | `litellm-release-notes`, `litellm-v1-*` pages, GitHub releases `litellm-github-releases`, `litellm-v*-release` |
| Support policy (supported lines, minimum baseline) | `litellm-version-support` |
| Advisories | `litellm-security-advisories` (GitHub), `osv-litellm-list`, `osv-api-litellm`, `gitlab-litellm-list`, `osv-*` / `gitlab-cve-*` / `litellm-ghsa-*` entries |
| Model support | `litellm-blog` and its per-model posts (`litellm-<model>`), `litellm-cost-map-main` (the cost map on main), provider docs `litellm-bedrock-docs`, `litellm-azure`, `litellm-vertex-partner` |
| Incidents and regressions | the project's own posts (e.g. `litellm-oom-incident`) |

## 4. Worklist and budget (8 searches / 14 fetches / 60 tool calls)

- **P0**: new advisories (GHSA / CVE / OSV) for litellm - one whose affected range covers `recommended` or
  `minimumBaseline` is the most important finding of the day; every version uploaded to PyPI since the newest
  release in the portal; leads for you (models that need their `litellm` block); yesterday's not-reached items.
- **P1**: release notes of the new versions (avoid list, pitfalls, model support); the support policy page; the
  `litellm` block of catalog models with status `untested`, newest models first.
- **P2**: caveats and config snippets of older blocks.

## 5. What to propose

- **Releases** (`/releases/-`): the PyPI row with the version and its date on the same line. Classify by the version
  string as the portal does: stable `v1.104.2`, release candidates `-rc.N`, nightly `-nightly`; a version is compared
  whole (`v1.102.1-rc.1` is not `v1.102.1`).
- **Advisories** (`/advisories/-`): filed under the id the advisory gives (`CVE-...`, `GHSA-...`), with the affected
  range and the fixed version exactly as the advisory names them, from an advisory database or the project's own
  security notice. An id of your own making waits for a person.
- **Avoid list and pitfalls**: release notes or the project's own incident posts that name broken versions (memory
  leaks, regressions) → `avoid[]` with versions written as versions or ranges ("v1.81.16 – v1.83.6", "< v1.84.0").
  A GitHub issue alone is community tier: `unverified`, with the issue as where you saw it.
- **Support window**: `supportedLines` / `minimumBaseline` from `litellm-version-support` (quote the line).
- **A model's `litellm` block**: provider strings per service (`bedrock/...`, `azure_ai/...`, `vertex_ai/...`,
  `anthropic/...`), the oldest version that routes the model, the oldest version whose bundled cost map has it,
  caveats - each from a LiteLLM page that names the model; set `status` to `researched` when the block rests on
  those pages. Never write `tested-*`.

## 6. Handoff

- `radar-editor`: an advisory that touches `recommended` / `minimumBaseline` (topic `cve:<cve id>`, action item
  `litellm:upgrade`); a new release (topic `litellm:release:<major.minor>`, e.g. `litellm:release:1.105`); a change of
  the recommended version or support window (topic `litellm:recommended-version`).
- `vendor-api-researcher` / the cloud roles: a model LiteLLM routes (cost map, release notes) that the catalog does
  not have - discovery, not GA evidence.
- `portal-reviewer`: a catalog model whose `litellm` block you could not research, and why.
- `checked` lines: `{"topic": "PyPI litellm", "result": "added: v1.105.1（pypi-litellm-json）"}`,
  `{"topic": "OSV litellm", "result": "no change: 沒有新的公告（osv-litellm-list）"}`; unfinished:
  `{"topic": "litellm block claude-haiku-5-5", "result": "not reached: 預算用完"}`.

## 7. Self-check before submit (besides the shared one)

- Every version in an op is a LiteLLM version on a LiteLLM page or advisory (not "Python 3.9", "boto3>=1.40.0",
  "litellm-proxy-extras 0.2.1").
- Release dates are the PyPI rows, written in full; advisories carry the advisory's own id.
- No `tested-*`, `internal`, or `internal-test` value was added, changed or removed.

## 8. Pitfalls

- PyPI upload time is UTC; write the date the page shows, in full.
- Release notes pages are sometimes published days after the PyPI upload: the PyPI row is the release date. A release
  on PyPI without notes yet is reported as such ("先不要升") - never guess its content.
- "*-stable" or "all versions" names no version: an avoid / affected / fixedIn value needs real versions or ranges.
- The cost map on main is not a release: a model in it is routable only from the release that ships it (or with a
  remote cost-map reload - say which).
