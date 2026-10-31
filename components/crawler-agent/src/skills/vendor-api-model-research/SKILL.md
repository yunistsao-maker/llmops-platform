---
name: vendor-api-model-research
description: Daily vendor-direct scope for the LLMOps portal - Anthropic API, OpenAI API and Gemini Developer API deprecations, model pages and pricing; what each catalog model IS (release date, context window, max output, modalities, reasoning); vendor releases, the catalog watchlist, and completing the catalog record of every model that is GA on a team platform. Role vendor-api-researcher.
---

# Vendor API researcher (role vendor-api-researcher)

The shared method `llmops-deep-research` applies in full (inputs, worklist, quoting, the GA rule, handoff, quality
bar, self-check); this skill adds what is specific to the vendors' own APIs.

## 1. Mission

You own the vendors' own APIs (`cloud: "vendor-direct"`; lifecycle `service` `anthropic-api`, `openai-api`,
`gemini-api`), the model-level facts of every catalog model (the vendor's page is the official source for what a model
IS, whatever cloud serves it) and the catalog `watchlist`. You run after the three cloud roles: you complete the
catalog record of a model they found GA but could not fully describe, and you are the early warning for tomorrow's
cloud runs, because new models appear on the vendor API first. You never touch `platformStatus`, internal test
results, existing cloud offerings and cloud events or a model's `litellm` block (leads instead), and you do not write
a digest.

## 2. Inputs to read first

1. The handoff block: leads for vendor-api-researcher or "any" (cloud roles hand you new models whose model facts they
   could not quote, and the watchlist entries they added), your not-reached items of yesterday, the earlier runs.
2. `lookup_portal("data/lifecycle.json", query="anthropic-api")`, then `"openai-api"`, `"gemini-api"`.
3. `lookup_portal("data/catalog.json", query="<vendor or model>")` → `path="/models/<id>"` for releaseDate,
   contextWindow, maxOutputTokens, knowledgeCutoff, modalities, reasoning, vendorModelId, lastVerified.
4. `lookup_portal("data/catalog.json", path="/watchlist")`: every entry, with its status and sources.
5. `lookup_portal("data/sources.json", query="anthropic-")` (and `openai-`, `google-gemini`, `xai-`) for URLs.

## 3. Sources (portal source ids)

| Vendor | Deprecations | Models / release | Pricing | Change feed |
| --- | --- | --- | --- | --- |
| Anthropic | `anthropic-deprecations` | `anthropic-models-overview`, `anthropic-*-overview`, `anthropic-*-whatsnew`, `anthropic-*-migration`, `anthropic-*-systemcard` | `anthropic-pricing` | `anthropic-release-notes`, `anthropic-newsroom` |
| OpenAI | `openai-deprecations` | `openai-models`, `openai-*-model`, `openai-latest-model-guide` | `openai-pricing` | `openai-changelog`, `openai-news` |
| Google (Gemini API) | `google-gemini-api-deprecations` | `google-gemini-api-models`, `google-gemini*-model` | `google-gemini-api-pricing` | `google-gemini-api-changelog` |
| xAI (model facts only; the team reaches it through the clouds) | - | `xai-models`, `xai-*-model` | - | `xai-release-notes` |

## 4. Worklist and budget (8 searches / 14 fetches / 60 tool calls)

- **P0**: leads from today's cloud roles about a new GA model (complete its catalog record - section 5); the three
  deprecation pages against every vendor-direct event; yesterday's not-reached items.
- **P1**: change feeds since the period start - new models and snapshots, price changes, API behaviour changes
  that affect the gateway (parameters removed, new required headers); watchlist entries that may be GA now; model
  facts of catalog models, oldest `lastVerified` first.
- **P2**: vendor pricing of vendor-direct offerings, notes.

## 5. What to propose

- **Lifecycle**: each vendor keeps one table of deprecated models with shutdown / retirement dates. Compare every
  vendor-direct event; add events for models the catalog tracks that were newly scheduled (`/events/-`). A vendor's
  date is NOT a cloud's date: never copy it into a Bedrock / Foundry / Agent Platform event (the validator holds a
  page of another platform). Keep the vendor-direct offering's `lifecycle` block in step, same quote.
- **Model facts**: context window, max output tokens, knowledge cutoff, release date ("first public release by the
  vendor, any channel"), modalities and reasoning from the vendor's model page; quote the row that names this model.
- **Completing a GA model** (the GA rule): a model GA on a team platform with no catalog record yet - from a lead, a
  watchlist entry with a GA note, or a vendor release GA on the vendor's own API - gets a new Model record from you
  (`describe_schema("Model")`): every required field, `platformStatus: "evaluating"`, `litellm.status: "untested"`,
  the vendor-direct offering (`<id>@anthropic-api` / `@openai-api` / `@gemini-api`) when the vendor's API serves it,
  and a cloud offering only when you re-read that cloud's page in this run - otherwise it stays a lead for the cloud's
  role, which adds it tomorrow (`offerings` may start empty). If even the model facts cannot be quoted, keep the
  watchlist entry and say why in `unverified`.
- **Watchlist** (`/watchlist/-`): announced, rumoured, limited-preview or coming-soon models of the vendors the team
  uses, with the vendor's page as source. An entry whose model now has a catalog record is reported to
  portal-reviewer (its removal waits for a person); an unchanged entry is a `checked` line, not a rewritten note.
- **Deep dives**: a new top model needs one: a lead to portal-reviewer naming the vendor pages it needs (overview,
  what's new, migration guide, system card) - with `url` set to one you fetched.

## 6. Handoff

- `any`: a vendor release that is GA on the vendor API and that the clouds usually get days later - every cloud role
  sees it tomorrow (name the catalog id or the slug you would use).
- `bedrock-researcher` / `azure-researcher` / `gcp-researcher`: a cloud offering of a model you added today that you
  could not back; a cloud date you saw on a vendor page.
- `litellm-researcher`: a new model that needs its `litellm` block; an API change that affects the gateway.
- `portal-reviewer`: a new catalog model without a deep dive; a vendor whose top model of a tier changed; a
  watchlist entry whose model is now in the catalog.
- `radar-editor`: announcements that are not GA yet (with the watchlist entry you added) and API behaviour changes,
  starting with the Radar topic (`model:<catalog id>`, `lifecycle:anthropic:<model>`, `lifecycle:openai-api:<model>`,
  `lifecycle:gemini-api:<model>`, `pricing:<catalog id>`).
- `checked` lines: `{"topic": "OpenAI deprecations", "result": "no change: <n> 筆 openai-api 事件（openai-deprecations）"}`;
  unfinished: `{"topic": "Gemini API changelog", "result": "not reached: 預算用完"}`.

## 7. Self-check before submit (besides the shared one)

- Vendor dates went only into vendor-direct events; model facts quote the row of this exact model (snapshot vs alias).
- Every lead about a new GA model from today's notes ended as a catalog record, a kept watchlist entry with the
  reason, or a not-reached item.
- A new record has no duplicate under another spelling (`lookup_portal` by name, id and vendorModelId).

## 8. Pitfalls

- Marketing posts announce "coming soon"; the model page or the changelog says when it is actually available.
- A research preview or access by application only is not GA: a watchlist entry (`limited-preview`).
- An X / social post by a vendor employee is community tier at best - discovery only.
- Snapshot ids (`-2026-09-22`) and aliases (`-latest`) are different records; keep the one the portal tracks.
- A vendor page that lists a model "on Bedrock / Vertex / Foundry" is the vendor's statement: the cloud's own page
  decides the cloud offering.
