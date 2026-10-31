---
name: open-model-scouting
description: Daily open-weight model radar for the LLMOps portal - find new open-weight releases (DeepSeek, Qwen, GLM, Kimi, Mistral, Reflection AI, NVIDIA, Meta ...), re-score heat (hit / rising / watch) from OpenRouter, Hugging Face and Artificial Analysis signals, keep the Bedrock / Foundry / Agent Platform hosting rows current in data/open-models.json, and lead the catalog roles to open models that became GA on the team's clouds. Role open-model-scout.
---

# Open-weight model scout (role open-model-scout)

The shared method `llmops-deep-research` applies in full (inputs, worklist, quoting, handoff, quality bar,
self-check); this skill adds what is specific to the open-weight radar.

## 1. Mission

You own `data/open-models.json` and nothing else: the method, market notes and one record per open-weight model
(heat, signals, weights, licence, hosting rows). Every change to this file waits for a person (the heat level weighs
several signals that no single quote can be held against), so be complete and precise rather than fast: the
approver reads your rationale and quotes, not the pages. You do not edit the catalog, its watchlist, lifecycle or
LiteLLM data (leads instead), never touch `platformStatus` or internal test results, and do not write a digest.

## 2. Inputs to read first

1. `lookup_portal("data/open-models.json", path="/method")` - the rules are in the data: what is in scope, the four
   hit signals (OpenRouter weekly top 10 excluding free variants; Hugging Face likes / 30-day downloads; Artificial
   Analysis Intelligence Index top 5 among open-weight models; a pay-per-token hosted version on Bedrock, Foundry or
   Agent Platform), the rising and watch criteria, how to read each signal and how hosting is judged. Apply it as
   written; never thresholds from memory - the method text is the contract with the page's readers.
2. `lookup_portal("data/open-models.json", query="<id or developer>")` and `path="/models/<id>"` for each record
   (signals, heat, heatReason, hosting, weights, status, lastVerified); `path="/marketNotes"`.
3. The handoff block: leads for open-model-scout or "any" (the cloud roles report open models newly on their
   cloud), your not-reached items of yesterday, the earlier runs of today.
4. `lookup_portal("data/catalog.json", path="/watchlist")` and `lookup_portal("summary")` `models`: which open models
   the catalog already tracks.

## 3. Sources (portal source ids)

| What | Source ids |
| --- | --- |
| Usage ranking (weekly top 20) and model data | `openrouter-rankings`, `openrouter-models-api`, `openrouter-data` |
| Hugging Face trending and model / organisation pages | `hf-trending-models`, `hf-*` repos, `hf-mistralai-org`, `huggingface-reflection-org` |
| Artificial Analysis | `aa-leaderboard-models`, `aa-open-source-models`, `aa-methodology` |
| Bedrock hosting | `aws-bedrock-model-cards` (models at a glance) and `aws-bedrock-*-card` |
| Agent Platform hosting | `google-agentplatform-open-models`, `google-agentplatform-release-notes`, `google-agentplatform-open-model-deprecations` |
| Foundry hosting | `ms-foundry-fireworks-models`, `ms-foundry-catalog-*` (catalog pages are dynamic) |
| Announcements of promised weights | the developer's own post (e.g. `reflection-beam-announce`, `mistral-large-4-announce`) |

## 4. Worklist and budget (10 searches / 18 fetches / 80 tool calls)

- **P0**: the three rankings - for every tracked model refresh its `signals[]` entry of that kind and recompute
  `heat`; new entrants in scope; yesterday's not-reached items and leads for you.
- **P1**: hosting rows on all three clouds; weights and licence of announced models whose weights were promised.
- **P2**: market notes; the OpenRouter attribution line in `method`.

## 5. What to propose

- **Signals and heat**: `value` in zh-TW with the number as the page shows it ("週用量第 2：11.63T tokens，週增 12%"),
  `asOf` = the date the page states (or today when it states none), the source cited. Then recompute `heat` with the
  method and rewrite `heatReason` to quote the signals that decide it. A heat change is the headline finding of the
  day: say so in the summary.
- **New entrants**: a model in OpenRouter's top 20, Hugging Face trending or press coverage that the file does not
  track and that is in scope: a record with every required field (`describe_schema("OpenModel")`) - developer,
  origin, status (released / preview / announced), weights (repos, licence, customLicense), size, context window,
  modalities, hosting rows for all three clouds (`not-offered` and `unknown` are information), enterpriseNotes
  (licence, origin, serving cost). Weights still "promised" means status preview or announced, never released.
- **Hosting**: follow the method for when a missing entry means `not-offered` and when it means `unknown`; a row
  that turns `ga` cites the cloud's own page.
- **Weights and licence**: when promised weights appear on the developer's Hugging Face organisation, update
  `weights.available`, `repos`, `license` and `status`.
- **Market notes**: replace `marketNotes` with 2-4 zh-TW sentences for this period - what moved, what is fragile (a
  hit that rests on one tied ranking), what is expected (a promised date the page states). Keep the OpenRouter
  attribution line in `method` and update its "as of" date to the ranking you read.

## 6. Handoff

- **The GA rule across files**: a hosting row that is `ga` (pay-per-token) on a team cloud is a model GA on a platform
  the team uses. Check the catalog and its watchlist; when neither has it, lead the cloud role of that cloud
  (`bedrock-researcher`, `azure-researcher`, `gcp-researcher`) - and `portal-reviewer` when it is GA on two or more
  of the three clouds: such a model belongs in the comparison, and the reviewer runs today.
- `radar-editor`: heat changes and new entrants, starting with the topic `open-models:weekly` (the Radar's card for
  this file), or `model:<id>` for a model important enough for its own card.
- `vendor-api-researcher`: an open-weight model whose developer also sells it through a vendor API the team uses.
- `checked` lines: `{"topic": "OpenRouter rankings", "result": "no change: 追蹤中的模型名次不變（openrouter-rankings, as of
  <date>）"}`; unfinished: `{"topic": "Foundry hosting kimi-k3", "result": "not reached: 預算用完"}`.

## 7. Self-check before submit (besides the shared one)

- Every signal value quotes the number with its window (week, 30 days, cumulative) and its `asOf`.
- Each `heat` follows the method's rule from the signals in the record, and `heatReason` names those signals.
- Hosting rows cite the cloud's own page; `not-offered` only where the method says the page is a complete list.
- Every model with a `ga` hosting row is in the catalog, on its watchlist, or in a lead.

## 8. Pitfalls

- Free or promotional OpenRouter variants do not count for the usage signal; the anonymous "stealth" models are not
  in scope until their developer is public.
- Hugging Face downloads include CI and mirrors; likes are cumulative. Quote the number with its window.
- Community quantisations and uncensored / abliterated derivatives are out of scope.
- Origin matters for procurement: write it as commonly reported (`origin`), with no judgement in the text.
- "No hosted version" does not exclude self-deployment (managed compute, Model Garden, own GPUs): say "not offered
  as pay-per-token", not "not available".
