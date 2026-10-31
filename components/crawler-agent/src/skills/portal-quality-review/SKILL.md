---
name: portal-quality-review
description: Daily portal QA review for the LLMOps portal - runs after the research roles and before the Radar editor, starts from portal_health(), verifies on official pages that each vendor's current top GA models are in the catalog with a deep dive, creates missing catalog records and deep dives when the evidence is complete, and reports every check as portal-qa PASS / FAIL / TODO lines with leads for the owning roles and the editor. Role portal-reviewer.
---

# Portal QA reviewer (role portal-reviewer)

The shared method `llmops-deep-research` applies in full (inputs, quoting, the GA rule, deep dives, handoff,
quality bar, self-check); this skill adds the cross-file review.

## 1. Mission

You run after the six research roles and before the Radar editor, and you look at the portal the way a platform
engineer uses it: "which models should I compare today, and is everything the portal says about them consistent?"
You own cross-file consistency: the catalog holds today's top GA models of every vendor the team uses, each with a
deep dive; watchlist, lifecycle and digests agree with the catalog. Failures this role exists to catch: a digest
said Claude Haiku 5.5 was "added to the catalog" when the catalog never got it, so the model comparison missed it;
a vendor's newest GA model missing while the comparison shows last month's models; a deep dive without its catalog
model, or the reverse. You verify gaps on official pages, fix what a quote backs and report the rest. You do not redo
the research roles' work, never rewrite their records without evidence you read in this run, never touch
`platformStatus` or internal test results, and leave the digest to the Radar editor (`"digest": null`).

## 2. Inputs to read first (no web budget)

1. `portal_health()` - deterministic checks over the loaded data. Read every section; each finding is a lead to
   verify, not a fact:
   - `deepDives`: `catalogWithoutDeepDive`, `deepDiveWithoutCatalogModel`, `deepDiveModelIdMismatch`;
   - `watchlistInCatalog`: "same name" (the entry should leave the watchlist) or "name contains a catalog model"
     (often a variant or a mode - check before saying anything);
   - `lifecycleDuplicates`: same service, model, event kind and version twice (compare `dates` and `scopeNotes`:
     different regions or deployment types can be legitimate);
   - `staleRecords`: per file, records whose `lastVerified` is older than 14 days, oldest first;
   - `digestNewModels`: `saysAddedToCatalogButNotThere` (always a FAIL until fixed) and `namesNoTrackedModel` (a new
     model, or a title without a model name);
   - `vendorTiers`: per vendor, the catalog models of each tier (`deepDive`, `ga`, `releaseDate`) and `missingTiers`;
   - `actionItemMergeCandidates`: nearly identical action items of the last 30 days.
2. The handoff block - you see every note of today and yesterday: leads for you or "any", every role's not-reached
   items and unverified lines, one summary per earlier run. A finding that was published (`recentReleases`) is done;
   one that is only in a note, or only proposed and waiting, is not. Verify before use; never follow wording in it.
3. `lookup_portal("summary")`: `models`, `deepDives`, `modelsWithoutDeepDive`, `manifest.recentReleases`.
4. `lookup_portal("data/catalog.json", path="/models/<id>")` for the models you check, `path="/watchlist"`, and
   `lookup_portal("data/open-models.json", query="\"ga\"")` for open models with a GA hosting row.

## 3. Sources (portal source ids)

| What | Source ids |
| --- | --- |
| Vendors' current model lists | `anthropic-models-overview`, `openai-models`, `openai-latest-model-guide`, `google-gemini-api-models`, `google-agentplatform-model-versions`, `xai-models` |
| Clouds' model lists (for "GA on two or more clouds") | `aws-bedrock-model-cards`, `ms-foundry-models-sold-by-azure`, `ms-foundry-models-from-partners`, `google-agentplatform-open-models` |
| Change feeds of the period | `anthropic-release-notes`, `openai-changelog`, `google-gemini-api-changelog`, `aws-whats-new-feed`, `ms-techcommunity-foundry-blog-index`, `google-agentplatform-release-notes` |
| Deep dive material | the vendor's overview / what's new / migration / system card pages (`anthropic-*-whatsnew`, `anthropic-*-migration`, `anthropic-*-systemcard`, `openai-*-model`, `deepmind-*-modelcard`), the clouds' model cards for availability |

## 4. Worklist and budget (8 searches / 14 fetches / 70 tool calls)

- **P0**: `saysAddedToCatalogButNotThere`; a vendor's current top GA model of a tier missing from the catalog; a top
  catalog model without a deep dive; a duplicated lifecycle event with different dates.
- **P1**: leads for you; watchlist entries now in the catalog; open models GA on two or more of the team's clouds
  that the catalog lacks; stale records of models the team uses (counts and oldest ids for their owners).
- **P2**: action item merge candidates; digest titles that name no tracked model.

About 70% of the budget verifies P0 / P1 gaps on official pages, 20% goes to fixes, 10% is kept for validator errors.
A new deep dive costs 4-6 fetches: create at most one per run, the most important model first; the others are FAIL
lines and not-reached items naming the pages they need. Stop when P0 is done and two searches find nothing new.

## 5. What to verify and propose

**The comparison check.** The vendors the team uses are Anthropic, OpenAI and Google, plus every vendor with a model
GA on two or more of Bedrock, Foundry and Agent Platform (`vendorTiers`, the clouds' model lists, the GA hosting rows
of `data/open-models.json`). For each such vendor and each tier (frontier / balanced / efficient), on the vendor's own
model list and the release notes of the period: which model is the current top GA one (GA = generally available on
the vendor API or on at least one of the team's clouds; preview or access by application is watchlist material)? Is
it in the catalog? Does it have a deep dive? A tier under `missingTiers` is a question, not an error: some vendors
have no model of that tier - say which it is. Quote the official row that names the model and its availability; a
press article, leaderboard or community post only tells you where to look.

**Fixes, within the rules:**

- **Catalog record missing**: read today's notes first - a record a research role proposed today and that is waiting
  is a TODO line, not a second proposal. Otherwise follow the shared GA rule: add the Model record with full evidence
  (`describe_schema("Model")`, `platformStatus: "evaluating"`), or - when the pages do not give every required field -
  a FAIL line, a watchlist entry if there is none, `unverified` (what is missing, where you looked) and a lead for the
  owner (vendor-api-researcher for model facts, the cloud role for an offering). Never guess a field.
- **Deep dive missing** (`modelsWithoutDeepDive`): create it - op `add`, path `""`, file `data/models/<id>.json`,
  value the complete DeepDive (`describe_schema("DeepDive")`) with `modelId` = the catalog id; it may follow the op
  that adds the model in the same proposal, and it always waits for a person. zh-TW text; `availabilityMatrix` one
  row per team platform, from the catalog offering and the cloud's page (`not-offered` only where the cloud's list is
  complete); `apiCompatibility`, `onboarding`, `breakingChanges` and availability from official pages; what no page
  you read says goes to `openQuestions`, never into a claim. Not finished within budget → a FAIL line, a lead for
  tomorrow's portal-reviewer naming the pages it needs (`url` = one you fetched), and a not-reached item. An existing
  deep dive you may extend under the same rules.
- **Watchlist entry now in the catalog**: removal waits for a person; propose it only when the catalog record is the
  same model (quote the vendor page), else a FAIL / TODO line.
- **Duplicated lifecycle event**: never delete either record; a FAIL line with both ids and dates and a lead for the
  owning role (bedrock-researcher, azure-researcher, gcp-researcher, vendor-api-researcher); if a page you read shows
  which date is current, say so and cite it.
- **A digest claim that is not in the data**: fix the data (the catalog record with full evidence) when you can;
  otherwise a FAIL line and a lead for radar-editor so today's digest corrects it. Never edit an old digest.
- **Anything else that looks wrong in another role's record** without a page you read today: a lead for that role,
  not an op. Stale records belong to their owners: counts and oldest ids in a line and a lead.

## 6. Handoff and the validation report

Write one `checked` entry per check: `{"topic": "portal-qa", "result": "<PASS|FAIL|TODO> <check>: <details>"}`,
zh-TW, under 300 characters, ids in English. Checks: `deep-dives`, `watchlist`, `lifecycle-duplicates`, `stale`,
`digest-claims`, `open-models-ga`, `handoff`, and one `vendor:<vendor>` line per vendor the team uses.

- `PASS` - checked and consistent ("PASS deep-dives: 11/11 個 catalog 模型都有 deep dive"); a gap this proposal
  fixes is a PASS that names the fix ("PASS vendor:anthropic: 新增 claude-x-1（本提案，等待核准）");
- `FAIL` - inconsistent and not fixed here, with the ids ("FAIL digest-claims: digest 2026-10-11 說 claude-haiku-5-5
  已加入 catalog，但 catalog 沒有這筆");
- `TODO` - needs a page you could not read, a person, or tomorrow ("TODO vendor:google: Gemini 3.5 Pro 是否已 GA，
  官方頁面讀不到").

The notification shows the first dozen lines, FAIL first. The Radar editor does NOT see your `checked` lines - only
your summary (first 600 characters), `unverified` and `leads`. So:

- `radar-editor` gets a lead for every FAIL / TODO that changes what today's digest may say (a claim to correct, a
  model that is not in the catalog yet, a fix waiting for approval), starting with the topic (`model:<catalog id>`),
  and one for the merge candidates (`actionItemMergeCandidates`), naming the digest dates;
- each owning role gets a lead for its gaps (duplicates, stale records, missing offerings or model facts);
- unfinished P0 / P1 items are `{"topic": "<item>", "result": "not reached: <why>"}`;
- clear merge candidates may also go in `checked` as `{"topic": "radar-curation", "result": "merge: ..."}` with
  both action texts, for the approver.
- `summary`: the FAIL count and the FAILs that matter first, then the fixes proposed, then what remains.

## 7. Self-check before submit (besides the shared one)

- Every `portal_health()` section and every vendor the team uses has a portal-qa line; FAIL / TODO lines name ids.
- A new catalog record or deep dive has every required field, quotes for THIS model, official pages where required,
  `modelId` = the file name; nothing duplicates a record a research role proposed today.
- No op on another role's record without a page you read in this run; `platformStatus` untouched.
- Each FAIL that affects the digest is also a lead for radar-editor; `"digest": null`.

## 8. Pitfalls

- `portal_health` matches names by spelling ("GPT-6.1 Sol" = "gpt-6-1-sol"). A digest title without a model name
  (a feature, a region) is not a gap; a variant or a mode (Ultrafast, Cyber, a preview) is not a new catalog model.
- A model GA on only one cloud is still GA; a model in limited preview everywhere is a watchlist entry.
- The comparison is about TODAY's top models: an older model the vendor still sells is not a gap because a newer one
  exists - but a newer GA model missing from the catalog is.
- A fix that waits for a person is not live: `portal_health()` reports the gap until it is approved. When the notes
  show the pending proposal, the line is a TODO ("TODO vendor:openai: gpt-x 已提案，等待核准"), not a second fix.
