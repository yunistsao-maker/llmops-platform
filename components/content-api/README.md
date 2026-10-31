# content-api

Read-only HTTP access to the compiled portal view (written by view-builder to the curated bucket). The portal page
loads its data from here through portal-web.

**Kind:** service (FastAPI + uvicorn), `PORT` 8080. Stateless; run 2+ replicas for availability.

## API (every route needs a signed-in user)
| Route | Response |
|---|---|
| `GET /api/content/portal.json` | the current view. `ETag: "<view_id>"`, `Cache-Control: no-cache`, `X-View-Id`. `If-None-Match` (weak comparison) → `304`. gzip when the client accepts it (`Vary: Accept-Encoding`). |
| `GET /api/content/views/{view_id}/portal.json` | one view by id; immutable: `Cache-Control: private, max-age=31536000, immutable`, ETag/304 as above, `404` for unknown ids |
| `GET /api/content/manifest` | `{"current": release_id, "history": [...], "view": <portal/views/current.json>}`, `Cache-Control: no-cache` |
| `GET /healthz`, `GET /readyz` | liveness; readiness = object storage reachable (a missing pointer still counts as reachable) |

Errors use one shape: `{"error": {"code", "message"}}` — `401 unauthorized`, `404 not_found`,
`503 no_view` (nothing built yet, `Retry-After: 30`), `503 store_unavailable`.

Caching: the pointer (`portal/views/current.json`) and the manifest are re-read at most every
`POINTER_TTL_SECONDS`; if the store is down, the last copy keeps being served. View bodies are immutable, so the
last `VIEW_CACHE_SIZE` views stay in memory together with their gzip form (a 400 KB view is ~90 KB gzipped).
After view-builder moves the pointer, clients see the new view within the TTL.

## Configuration
| Variable | Required | Default | Meaning |
|---|---|---|---|
| `PORT` | no | `8080` | listen port |
| `AUTH_MODE` | no | `oidc` | `oidc`, `dev` (every request is `DEV_USER_EMAIL`; local / AWS test only) or `none` |
| `OIDC_ISSUER`, `OIDC_AUDIENCE` | with `oidc` | — | token issuer and audience; the service refuses to start without them |
| `OIDC_JWKS_URL` | no | discovered from the issuer | |
| `OIDC_GROUPS_CLAIM` | no | `groups` | |
| `DEV_USER_EMAIL`, `DEV_USER_GROUPS` | no | `dev@example.local`, `llmops-admins` | `dev` mode only |
| `S3_ENDPOINT_URL`, `S3_REGION`, `S3_FORCE_PATH_STYLE` | no | AWS S3, `us-east-1`, auto | object storage |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` (or `_FILE`) | no | default AWS credential chain | **Secret**; read-only rights suffice |
| `BUCKET_CURATED` | no | `llmops-curated` | |
| `POINTER_TTL_SECONDS` | no | `30` | pointer / manifest cache |
| `VIEW_CACHE_SIZE` | no | `4` | views kept in memory |
| `COMPONENT`, `LOG_LEVEL` | no | `content-api`, `INFO` | JSON logs on stdout (one access line per request, no headers or tokens) |
| `HTTPS_PROXY`, `NO_PROXY` | no | — | honoured by the S3 client and the JWKS fetch |

Object-storage rights: `GetObject` on `portal/views/*` and `portal/manifest.json` (nothing else, no writes).

## Probes, network, resources
- Probes: `GET /healthz`, `GET /readyz` on `PORT`.
- Outbound: object storage; the OIDC issuer's JWKS endpoint (`oidc` mode).
- Image: UID 10001, read-only root filesystem (`/tmp` emptyDir optional). SIGTERM: in-flight requests finish (≤ 25 s).
- Suggested resources: requests 100m CPU / 192Mi, limits 500m / 384Mi.
- Events: none consumed or published.

## Tests
`pytest components/content-api/tests` (MemoryStore, no network).
