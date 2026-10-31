# assistant-api

The portal's 「問問看 / Ask」 chat and the request-form helper, moved from the prototype page (which called a
claude.ai runtime) to the server. Answers come only from the current portal view, through the LLM gateway.

**Kind:** service (FastAPI + uvicorn), `PORT` 8080. Run 2+ replicas; note that the per-user rate limit is kept in
memory per replica.

## API (every route needs a signed-in user)
`POST /api/chat` `{"question": str, "history": [{"role": "user"|"assistant", "content": str}], "lang": "zh"|"en"}`
→ `{"answer": str, "links": [{"route": "#m-claude-sonnet-5-5", "title": "Claude Sonnet 5.5"}], "truncated": bool}`
1. Retrieval over the view (port of the page's `buildIndex()` / `search()`): Latin tokens plus their dotted/dashed
   parts, CJK bigrams, the synonym map, BM25-like scoring (idf · f/(f+1.2)), title words ×2, a model named in the
   question ×3 for its model page, month intent ("11 月", "November") for dated documents; top 8 snippets.
   Each model has three documents, one per layer of its page: `#m-<id>` (with its availability, e.g.
   「申請狀態：尚未開放，預計 2026 年 11 月下旬可以申請（依導入關卡估算；下一關：法遵審查）」, so the chat can say when a
   model can be requested), `#m-<id>/security` (security summary, data regions, gateway name and LiteLLM strings) and
   `#m-<id>/rollout` (availability and gate states). `tests/test_retrieval.py` checks the top 8 hits and the context
   block for 31 questions against the prototype's own JavaScript run on the same view (identical).
2. Prompt: the prototype's `rules()` as the system message (including the layer routes and the rule to answer
   "when can I request it" from the availability and to say the date is an estimate), the last `HISTORY_TURNS` turns, then the question with
   the snippets. Model `LLM_MODEL` (default `portal-quick`), `max_tokens` `CHAT_MAX_TOKENS`, no sampling settings.
3. The answer is markdown-lite text. Links survive only as `[text](#route)` where the route matches the page's
   `ROUTE_OK` (now including `#m-<id>/security` and `#m-<id>/rollout`) **and** names a model / news item / matrix
   item that exists in the view; other links become plain text.
   `links` = up to 4 related pages from retrieval, titled in `lang`.

`POST /api/assist/form` `{"messages": [...turns, last one from the user], "form": {request fields}, "lang"}`
→ `{"reply": str, "questions": [≤ 2], "form": {...}}`
The prototype's `formRules()` (catalog lines with each model's availability, request rules, the form so far) with
`chat_json`. The suggested form is
sanitised (port of `sanitizeForm()`): model ids must be in the catalog (≤ 3), enums and number ranges follow
`contracts/schemas/request.schema.json`, and it comes back in that schema's names (`models, use_case, data_class,
data_class_other, cloud, rpd, tokens_in, tokens_out, external, taiwan_only, target_date, note`).
The Taiwan-processing sentence of the prompt is computed from the catalog (the prototype had it hard-coded).

Errors: `{"error": {"code", "message"}, "links": [...]}` (links when retrieval already ran, so the page can still
point somewhere):
| Status | code | When |
|---|---|---|
| 401 | `unauthorized` | no or bad token |
| 413 | `too_large` | body over `MAX_BODY_BYTES` |
| 422 | `invalid_request`, `question_too_long`, `refused` | bad body; question over `MAX_QUESTION_CHARS`; the model or gateway content filter declined |
| 429 | `rate_limited` | over `RATE_LIMIT_PER_MINUTE` for this user, or the gateway's own 429 (`Retry-After`) |
| 502 | `gateway_error`, `gateway_auth`, `invalid_reply` | gateway 5xx/4xx, our key rejected (logged as error), unusable model output |
| 503 | `gateway_unavailable`, `busy`, `no_content` | gateway unreachable / timeout; all `MAX_CONCURRENT_CALLS` slots busy for `QUEUE_WAIT_SECONDS`; no view built yet |

The search index is rebuilt in the background when `portal/views/current.json` names a new view (polled every
`INDEX_POLL_SECONDS`); a bad pointer never replaces a good index.

## Configuration
| Variable | Required | Default | Meaning |
|---|---|---|---|
| `PORT` | no | `8080` | |
| `LLM_GATEWAY_URL` | **yes** | — | e.g. `http://llm-gateway:4000` |
| `LLM_GATEWAY_KEY` (or `_FILE`) | yes in practice | — | this component's virtual key. **Secret** |
| `LLM_MODEL` | no | `portal-quick` | gateway alias for chat |
| `LLM_FORM_MODEL` | no | `LLM_MODEL` | alias for the form helper |
| `LLM_TIMEOUT` | no | `30` | seconds per gateway attempt |
| `LLM_MAX_RETRIES` | no | `1` | retries on 408/429/5xx/transport errors |
| `CHAT_MAX_TOKENS`, `FORM_MAX_TOKENS` | no | `1024`, `1500` | answer length caps |
| `RATE_LIMIT_PER_MINUTE` | no | `10` | per user, chat + helper together (`0` = off) |
| `MAX_CONCURRENT_CALLS`, `QUEUE_WAIT_SECONDS` | no | `8`, `5` | gateway calls in flight per replica |
| `MAX_BODY_BYTES` | no | `65536` | request body cap |
| `MAX_QUESTION_CHARS`, `MAX_TURN_CHARS`, `HISTORY_TURNS` | no | `1000`, `4000`, `6` | input caps |
| `INDEX_POLL_SECONDS` | no | `30` | pointer poll |
| `PORTAL_TZ` | no | `Asia/Taipei` | "today" in the prompts |
| `AUTH_MODE`, `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL`, `OIDC_GROUPS_CLAIM`, `DEV_USER_*` | as content-api | `oidc` | `oidc` needs issuer and audience |
| `S3_*`, `BUCKET_CURATED` | as content-api | | read-only: `GetObject` on `portal/views/*` |
| `COMPONENT`, `LOG_LEVEL` | no | `assistant-api`, `INFO` | logs carry hit counts, model, finish reason, latency, token use; never questions, answers or users |
| `HTTPS_PROXY`, `NO_PROXY` | no | — | honoured by every HTTP client |

## Probes, network, resources
- Probes: `GET /healthz`; `GET /readyz` = object storage reachable (the gateway is not probed, so a gateway outage
  does not take the pods out of service; calls then answer 503 with related links).
- Outbound: object storage, the LLM gateway (in cluster), the OIDC issuer's JWKS (`oidc` mode). Never a cloud model.
- Image: UID 10001, read-only root filesystem (`/tmp` emptyDir optional). SIGTERM: in-flight requests finish (≤ 25 s).
- Suggested resources: requests 200m CPU / 256Mi, limits 1 CPU / 512Mi (the index of one view is ~350 documents).
- Events: none consumed or published.

## Tests
`pytest components/assistant-api/tests` (MemoryStore, `httpx.MockTransport` for the gateway, golden retrieval results
in `tests/golden/search.json` generated from the prototype's `app3.html` under Node).
