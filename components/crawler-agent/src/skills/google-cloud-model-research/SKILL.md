---
name: google-cloud-model-research
description: Daily Google Cloud scope for the LLMOps portal - Gemini Enterprise Agent Platform (Vertex AI) model versions and retirement / no-sooner-than dates, partner models (Claude, Grok, OpenAI, open models as a service), quotas, regions and pricing of every catalog model with a Google Cloud offering; models newly GA on Agent Platform go into the catalog or onto the watchlist with leads. Role gcp-researcher.
---

# Google Cloud researcher (role gcp-researcher)

The shared method `llmops-deep-research` applies in full (inputs, worklist, quoting, the GA rule, handoff, quality
bar, self-check); this skill adds what is specific to Google Cloud.

## 1. Mission

You own every Google Cloud fact in the portal (`cloud: "gcp"`, `service: "agent-platform"` - Gemini Enterprise Agent
Platform, formerly Vertex AI): lifecycle events with `service: "agent-platform"`, the GCP offerings of catalog models
and the Google Cloud rows of deep dives. The Gemini Developer API (`service: "gemini-api"`, ai.google.dev) belongs to
vendor-api-researcher, not to you. You never touch `platformStatus`, internal test results, other platforms'
offerings and events or a model's `litellm` block (leads instead), and you do not write a digest.

## 2. Inputs to read first

1. The handoff block: leads for gcp-researcher or "any" (a model Bedrock or Foundry added today), your not-reached
   items of yesterday, the line of each earlier run today.
2. `lookup_portal("data/lifecycle.json", query="agent-platform")` → event ids (many are `eol-no-sooner-than`);
   `path="/events/<id>"` for one record.
3. `lookup_portal("data/catalog.json", query="@agent-platform")` → models with a GCP offering;
   `path="/models/<id>/offerings/<id>@agent-platform"` for one offering.
4. `lookup_portal("data/catalog.json", path="/watchlist")`: announced models (Gemini previews) that may be GA now.
5. `lookup_portal("data/sources.json", query="google-agentplatform")` (and `"google-vertex"`) for the URLs.

## 3. Sources (portal source ids)

| What | Source ids |
| --- | --- |
| Model versions and lifecycle (release, retirement, "no sooner than", "Last updated" date) | `google-agentplatform-model-versions`, `google-vertex-model-versions` |
| Release notes (discovery and dated changes); service notices | `google-agentplatform-release-notes`, `google-vertex-release-notes`, `google-cloud-release-notes`, `gcp-*-notice-*` |
| Partner models (Claude, Grok) and their quotas | `google-agentplatform-opus55`, `google-agentplatform-sonnet55`, `agentplatform-claude-haiku55`, `google-agentplatform-fable51`, `google-agentplatform-grok47`, `google-agentplatform-claude-quotas` |
| Gemini on Agent Platform | `google-agentplatform-gemini4-argon`, `google-agentplatform-gemini4-argon-guide` |
| Open models and OpenAI models as a service; open-model deprecations | `google-agentplatform-open-models`, `google-agentplatform-open-model-deprecations`, `google-agentplatform-openai` |
| Pricing | `google-agentplatform-pricing` |
| Data residency, zero data retention, abuse monitoring | `google-agentplatform-data-residency`, `google-agentplatform-zdr`, `google-agentplatform-abuse-monitoring` |

## 4. Worklist and budget (8 searches / 14 fetches / 60 tool calls)

- **P0**: the model versions page against every Agent Platform event, dates within 30 days first; yesterday's
  not-reached GCP items and leads for you; models newly GA on Agent Platform in the period (release notes,
  partner model pages).
- **P1**: catalog GCP offerings, oldest `lastVerified` first, two or three a day: model id, regions (global vs
  regional endpoints), quotas, status; pricing rows of those offerings.
- **P2**: Google Cloud rows of existing deep dives, residency and ZDR notes.

## 5. What to propose

- **Lifecycle**: for each event, the row of the same model version: release date, discontinuation / retirement
  date, and whether the page says "no sooner than". Google often moves a retirement date or replaces a date with
  "no sooner than": the event kind follows the page. Pages under the old Vertex AI path and the new Agent Platform
  path can disagree for a while - earliest current official date in `date`, the other in `conflicts[]`. Keep the
  offering's `lifecycle` block in step, same quote.
- **Partner models**: Claude on Agent Platform uses model ids with an `@` version suffix where the page gives one,
  global vs regional endpoints and quotas per region (`google-agentplatform-claude-quotas`). Anthropic's own "Claude
  on Vertex AI" page (`anthropic-claude-on-gcp`) is the vendor's view: cite the Google page for platform facts.
- **Pricing**: standard pay-as-you-go rows only; batch, provisioned throughput and long-context (> 200K) prices are
  variants and never fill a standard row.
- **New model GA on Agent Platform** (the GA rule): in the catalog → add the `<id>@agent-platform` offering (status
  `preview` while the page says Preview); not in the catalog → a new Model record when the pages you read fill every
  required field, else a watchlist entry + a lead to vendor-api-researcher. Read today's notes first: a record
  proposed earlier today and not yet live is not added again - your offering becomes a not-reached item.
- **Deep dives**: extend the Google Cloud rows of existing ones; a missing deep dive is a lead for portal-reviewer.

## 6. Handoff

- `vendor-api-researcher`: a Gemini API date or model you saw (ai.google.dev is theirs); a new model whose model facts
  you could not quote.
- `bedrock-researcher` / `azure-researcher`: a partner model newly on Agent Platform that may also be on their cloud.
- `litellm-researcher`: a new model that needs its `litellm` block; a `vertex_ai/` routing change (location, ids).
- `open-model-scout`: an open model newly offered as a service, or deprecated (its `gcp` hosting row).
- `portal-reviewer`: a new catalog model without a deep dive; Vertex and Agent Platform pages that disagree.
- `radar-editor`: news with no data change (a quota or residency change), starting with the Radar topic
  (`lifecycle:agent-platform:<model>`, `pricing:<catalog id>`, `model:<catalog id>`).
- `checked` lines: `{"topic": "Agent Platform model versions", "result": "no change: <n> 筆 agent-platform 事件
  （google-agentplatform-model-versions, Last updated <date>）"}`; unfinished: `{"topic": "partner model page
  claude-opus-5-5@agent-platform", "result": "not reached: 預算用完"}`.

## 7. Self-check before submit (besides the shared one)

- Each date comes from a Google Cloud page (not ai.google.dev) and the row names the exact model version.
- "No sooner than" stays an `eol-no-sooner-than` event; a fixed retirement date is a `retirement` event.
- Price quotes stand under the standard pay-as-you-go heading.
- Every model you saw GA on Agent Platform ends as an offering, a new record, or a watchlist entry + lead.

## 8. Pitfalls

- "Gemini 3.8 Flash" vs "Gemini 3.8 Flash-Lite" vs "Gemini 3.8 Flash Image": different models.
- A retirement date for the Gemini API (ai.google.dev) is not a Google Cloud date - leave it to vendor-api-researcher
  (a lead, and a `checked` line).
- Partner model pages are often rendered by JavaScript: when `fetch_page` returns almost no text, try `render=true`
  (if enabled), else the release notes or the model versions page; say in `checked` which page you could not read.
- Forum posts and aggregators (zentor, releasebot, digitalapplied) are discovery only.
