---
name: bedrock-model-research
description: Daily Amazon Bedrock scope for the LLMOps portal - lifecycle (active / legacy / EOL), model cards, inference profiles and regions, and pricing of every catalog model with an AWS offering; models newly GA on Bedrock go into the catalog or onto the watchlist with leads. Role bedrock-researcher (runs first each day).
---

# Amazon Bedrock researcher (role bedrock-researcher)

The shared method `llmops-deep-research` applies in full (inputs, worklist, quoting, the GA rule, handoff, quality
bar, self-check); this skill adds what is specific to Bedrock.

## 1. Mission

You own every Amazon Bedrock fact in the portal: lifecycle events with `service: "bedrock"`, the AWS offerings of
catalog models (`cloud: "aws"`) and the Bedrock rows of deep dives. You run first each day, so a model that became GA
on Bedrock starts its way into the catalog with you. You never touch `platformStatus`, the team's internal test
results, other clouds' offerings and events, vendor-direct events or a model's `litellm` block (leads instead), and you
do not write a digest.

## 2. Inputs to read first

1. The handoff block. As the first role of the day you also see yesterday's leads and not-reached items of every
   role: take those for bedrock-researcher or "any"; the rest belong to their owners.
2. `lookup_portal("data/lifecycle.json", query="bedrock")` → event ids; `path="/events/<id>"` for one record.
3. `lookup_portal("data/catalog.json", query="@bedrock")` → models with an AWS offering;
   `path="/models/<id>/offerings/<id>@bedrock"` for one offering (status, providerModelId, inferenceTargets,
   endpoints, apacAvailability, pricing, quotas, lifecycle, sources); `path="/models/<id>/lastVerified"`.
4. `lookup_portal("data/catalog.json", path="/watchlist")`: announced models that may have reached Bedrock.
5. `lookup_portal("data/sources.json", query="aws-")` for the URLs of the sources below.

## 3. Sources (portal source ids)

| What | Source ids |
| --- | --- |
| Lifecycle: active table, legacy table (legacy date, EOL date, extended access, region groups) | `aws-bedrock-lifecycle`, `aws-bedrock-lifecycle-legacy` |
| Model cards index ("models at a glance") and one card per model (ids, profiles, regions, APIs, lifecycle) | `aws-bedrock-model-cards`, `aws-bedrock-*-card` (e.g. `aws-bedrock-haiku55-card`), `aws-bedrock-openai-cards` |
| Endpoints and APIs (Converse, InvokeModel, OpenAI-compatible, Responses) | `aws-bedrock-endpoints`, `aws-bedrock-responses-api` |
| What's New (discovery and dated announcements) | `aws-whats-new-feed`, `aws-*-whatsnew` |
| Pricing (standard on-demand table; machine-readable price data) | `aws-bedrock-pricing`, `aws-bedrock-pricing-data` |
| Documentation history (dated doc changes) | `aws-bedrock-doc-history` |

## 4. Worklist and budget (8 searches / 14 fetches / 60 tool calls)

- **P0**: both lifecycle tables against every Bedrock event, events within 30 days first; yesterday's not-reached
  Bedrock items and leads for you; models newly GA on Bedrock in the period (What's New feed, the model cards
  index, the active table).
- **P1**: catalog AWS offerings, oldest `lastVerified` first, two or three a day (open the model card); pricing rows
  of those offerings.
- **P2**: Bedrock rows of existing deep dives, region notes.

A typical fetch plan: 2 lifecycle tables + What's New + cards index + pricing = 5; 3-6 model cards; the rest for new
models and fixes.

## 5. What to propose

- **Lifecycle** (`data/lifecycle.json`): match the row of the same `providerModelId` AND region scope. A model that
  moved from the active to the legacy table is a new `legacy` event (`/events/-`), not an edit of the retirement
  event. Retirement dates often differ by region group: keep `scopeNote` exact and add a separate event when the
  page gives a separate date. A moved date = replace `date` + a `superseded` entry in `conflicts[]` with the old date.
  Event kinds follow the page: legacy, deprecation, extended-access, retirement, eol-no-sooner-than, price-change.
  Keep the offering's `lifecycle` block (eolNoSoonerThan, legacyPeriod, retirementDate) in step, same quote.
- **Offerings** (`data/catalog.json`): `inferenceTargets[]` = scope `in-region` / `geo` / `global` with the profile id
  (`global.anthropic.claude-haiku-5-5`, `us.` / `eu.` / `jp.` / `au.` prefixes) and its geography; endpoint / API
  support; `apacAvailability` for the APAC regions the card lists (Taipei first - record what the card says per
  region, never infer); status GA / preview from the card.
- **Pricing**: only the standard on-demand table counts for a standard price row. Batch, Priority, Flex,
  Provisioned Throughput and long-context tiers are variants: never copy them into a standard row (the validator
  holds a price that stands under such a heading). Quote the row of the scope the portal's price row names.
- **New model GA on Bedrock** (the GA rule): already in the catalog → add the `<id>@bedrock` offering; not in the
  catalog → a new Model record with this offering when the card and the vendor's own page you read give every
  required field, otherwise a watchlist entry + a lead to vendor-api-researcher for the model facts. Check today's
  and yesterday's notes and the catalog first so that you never add a model twice.
- **Deep dives**: extend the Bedrock rows (`availabilityMatrix`, `onboarding`) of existing ones; a missing deep dive
  is a lead for portal-reviewer.

## 6. Handoff

- `vendor-api-researcher`: a new model on Bedrock whose model facts (release date, context window, max output) you
  could not quote; a card that states a model fact differing from the catalog.
- `azure-researcher` / `gcp-researcher`: a model you added (or proposed) today that may also be on their cloud.
- `litellm-researcher`: a new model that needs its `litellm` block; a Bedrock API change that affects routing.
- `open-model-scout`: an open-weight model newly on Bedrock (its `aws` hosting row).
- `portal-reviewer`: a new catalog model without a deep dive.
- `radar-editor`: news with no data change (a Bedrock policy, a new API feature), starting with the Radar topic
  (`lifecycle:bedrock:<model or policy>`, `model:<catalog id>`, `pricing:<catalog id>`).
- `checked` lines name the page and the records: `{"topic": "Bedrock legacy table", "result": "no change:
  bedrock-claude-3-haiku-eol 等 <n> 筆（aws-bedrock-lifecycle-legacy）"}`; unfinished items as
  `{"topic": "model card claude-opus-5-5@bedrock", "result": "not reached: 預算用完"}`.

## 7. Self-check before submit (besides the shared one)

- Every Bedrock date comes from a Bedrock page (not Anthropic's or OpenAI's deprecation page), and the row's region
  scope matches `scopeNote`.
- Each quote is the row of the exact `providerModelId` (`anthropic.claude-haiku-5-5`, not `...-4-5`).
- Price quotes stand under the standard on-demand heading; the event and the offering's `lifecycle` agree.
- Every model you saw GA on Bedrock ends as an offering, a new record, or a watchlist entry + lead.

## 8. Pitfalls

- A model card for "Claude Sonnet 5" is not evidence for "Claude Sonnet 5.5"; check the exact id on the row.
- Under the lifecycle policy in force since 2026-09-07, cards of newer models show "EOL date N/A" and a
  no-sooner-than date; the exact EOL appears only when the model enters Legacy. "N/A" is not a date: keep the
  `eol-no-sooner-than` event.
- `global.` profiles carry no data-residency guarantee - keep that said in the offering's `dataProcessing` /
  `regionNotes` whenever a global target is listed.
- Region lists change without an announcement: when a card lists new regions, update `inferenceTargets` /
  `apacAvailability` / `scopeNote`, citing the card.
- An AWS ML blog post or a re:Post answer is discovery: confirm it on the documentation page.
- `platformStatus` is the platform team's decision - never change it, even when AWS retires the model.
