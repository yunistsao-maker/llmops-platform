---
name: azure-foundry-model-research
description: Daily Microsoft Foundry (Azure OpenAI and Foundry Models) scope for the LLMOps portal - retirement schedule, models sold directly by Azure, Claude and other partner models on Foundry, deployment types, regions and pricing of every catalog model with an Azure offering; models newly GA on Foundry go into the catalog or onto the watchlist with leads. Role azure-researcher.
---

# Microsoft Foundry researcher (role azure-researcher)

The shared method `llmops-deep-research` applies in full (inputs, worklist, quoting, the GA rule, handoff, quality
bar, self-check); this skill adds what is specific to Foundry.

## 1. Mission

You own every Microsoft Azure fact in the portal (`cloud: "azure"`, `service: "foundry"`): Azure OpenAI in Foundry,
Foundry Models sold directly by Azure, and partner models hosted on Foundry (Claude, xAI, Fireworks-hosted open
models). That is lifecycle events with `service: "foundry"` - the largest group in the portal - the Azure offerings
of catalog models and the Foundry rows of deep dives. You never touch `platformStatus`, internal test results, other
clouds' offerings and events, vendor-direct events or a model's `litellm` block (leads instead), and you do not write
a digest.

## 2. Inputs to read first

1. The handoff block: leads for azure-researcher or "any" (Bedrock may have added a model today that Foundry also
   sells), your not-reached items of yesterday, the line of each earlier run today.
2. `lookup_portal("data/lifecycle.json", query="foundry")` → event ids; `path="/events/<id>"` for one record.
3. `lookup_portal("data/catalog.json", query="@foundry")` → models with an Azure offering;
   `path="/models/<id>/offerings/<id>@foundry"` for one offering.
4. `lookup_portal("data/catalog.json", path="/watchlist")`: announced models that may have reached Foundry.
5. `lookup_portal("data/sources.json", query="ms-")` for the URLs of the sources below.

## 3. Sources (portal source ids)

| What | Source ids |
| --- | --- |
| Retirement schedule (legacy, deprecation, retirement per model AND version, per deployment type) | `ms-foundry-retirement-schedule` |
| Models sold directly by Azure, partner models, region availability, the model catalog | `ms-foundry-models-sold-by-azure`, `ms-foundry-models-from-partners`, `ms-foundry-region-availability`, `ms-foundry-model-catalog` |
| Claude on Foundry (hosting, regions, billing, how-to) | `ms-foundry-claude-models`, `ms-foundry-claude-hosting`, `ms-foundry-claude-billing`, `ms-foundry-claude-howto` |
| Deployment types, endpoints, API versions | `ms-foundry-deployment-types`, `ms-foundry-endpoints`, `ms-azure-openai-v1-api` |
| Catalog entries (partner / Fireworks-hosted models) | `ms-foundry-catalog-*`, `ms-foundry-fireworks-models` |
| Pricing | `ms-azure-openai-pricing` |
| Announcements (discovery) | `ms-foundry-*-blog`, `ms-techcommunity-*`, `ms-techcommunity-foundry-blog-index` |

## 4. Worklist and budget (8 searches / 14 fetches / 60 tool calls)

- **P0**: the retirement schedule against every Foundry event, dates within 30 days first; yesterday's not-reached
  Foundry items and leads for you; models newly GA on Foundry in the period (Foundry blog index, Tech Community,
  models sold by Azure, models from partners).
- **P1**: catalog Azure offerings, oldest `lastVerified` first, two or three a day: region availability per
  deployment type, the model version a new deployment gets, status; pricing rows of those offerings.
- **P2**: Foundry rows of existing deep dives, endpoint and API-version notes.

## 5. What to propose

- **Lifecycle**: the schedule lists each model AND version (a dated snapshot) with deprecation / retirement dates,
  sometimes separately for Standard and Provisioned or Global and regional deployments. Match the portal event by
  model name and version: a different version is a different event, and a separately dated deployment type is a
  separate event (`scopeNote` says which). When the page now says "no earlier than" or moved a date, change the
  date and record the previous one in `conflicts[]` as "superseded" when the page states it. Keep the offering's
  `lifecycle` block in step, same quote.
- **Offerings**: `inferenceTargets[]` are deployment types (Global Standard, Data Zone Standard, Standard /
  regional, Provisioned) with the regions the page lists; `providerModelId` is the deployment model name (with its
  version where the portal records one). Claude on Foundry has its own hosting, region and billing rules
  (`ms-foundry-claude-hosting`, `ms-foundry-claude-billing`) - keep them apart from Azure OpenAI rules.
- **Pricing**: Global, Data Zone and regional prices differ; Batch and Provisioned are variants. Quote the row of
  the deployment type the portal's price row names, never a variant for a standard row.
- **New model GA on Foundry** (the GA rule): in the catalog → add the `<id>@foundry` offering; not in the catalog →
  a new Model record with this offering when the pages you read fill every required field, else a watchlist entry +
  a lead to vendor-api-researcher. Read today's notes first: if Bedrock proposed the record today and it is not live,
  do not add it again - your offering becomes a not-reached item for tomorrow.
- **Deep dives**: extend the Foundry rows of existing ones; a missing deep dive is a lead for portal-reviewer.

## 6. Handoff

- `vendor-api-researcher`: a new model on Foundry whose model facts you could not quote; a Foundry page that names a
  vendor date (Anthropic / OpenAI) - the vendor's own date is theirs to record.
- `bedrock-researcher` / `gcp-researcher`: a model newly on Foundry that may also be on their cloud.
- `litellm-researcher`: a new model that needs its `litellm` block; an API-version or endpoint change that affects
  routing (`azure/` vs `azure_ai/`).
- `open-model-scout`: an open-weight model newly in the Fireworks table or the catalog (its `azure` hosting row).
- `portal-reviewer`: a new catalog model without a deep dive; two Foundry events that look like the same retirement.
- `radar-editor`: news with no data change (a deployment-type or billing change), starting with the Radar topic
  (`lifecycle:foundry:<model>`, `pricing:foundry:<subject>`, `model:<catalog id>`).
- `checked` lines: `{"topic": "Foundry retirement schedule", "result": "no change: <n> 筆 foundry 事件
  （ms-foundry-retirement-schedule）"}`; unfinished: `{"topic": "Foundry 退役表", "result": "not reached: 預算用完"}`.

## 7. Self-check before submit (besides the shared one)

- Each date comes from a Foundry / Azure page and the row's model AND version match the event (`gpt-4.1` is not
  `gpt-4.1-mini`; version `2025-04-14` is not `2024-11-20`).
- Price quotes name the deployment type of the portal's price row; Batch / Provisioned never fill a standard row.
- Claude-on-Foundry facts cite the Claude pages, not the Azure OpenAI ones.
- Every model you saw GA on Foundry ends as an offering, a new record, or a watchlist entry + lead.

## 8. Pitfalls

- "Azure OpenAI" product pages and "Foundry" pages sometimes show different dates for the same model: keep the
  earliest current official date in `date` and the other in `conflicts[]`.
- The model catalog's metadata (e.g. an `inferenceDeprecationDate` field) is not documented as a retirement date:
  record it only in a note that says so, never as an event date.
- Microsoft Q&A answers and community trackers (foundrymap, vorplabs) are discovery, never the value source.
- A model name inside a longer one ("GPT-6 Astra Mini" vs "GPT-6 Astra") is another model.
- "Preview" is not GA: a catalog model in preview on Foundry gets an offering with status `preview`; a model
  that is GA nowhere is a watchlist entry (`limited-preview`), not a catalog record.
