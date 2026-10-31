# notifier

Sends short Traditional Chinese notifications by email (SMTP) and to team chat webhooks (Google Chat or
Microsoft Teams style JSON) for platform events. Subjects get an English suffix by default. A message never
contains a LiteLLM key: `key.provisioned` tells the requester where the key is stored and how to fetch it.

| | |
|---|---|
| Kind | worker on queue `notifier` |
| Image | `docker build -f components/notifier/Dockerfile .` (repo root); entrypoint `python -m notifier`, CMD `run` |
| Probes | `HEALTH_PORT` (8081): `GET /healthz`, `GET /readyz` (database reachable) |
| Runtime | UID/GID 10001, read-only root filesystem, writes nothing |
| Shutdown | SIGTERM: finishes the current delivery and exits |

Other commands: `python -m notifier check-config` validates the settings and the database connection.

## Messages

| Event | To | Message (subject before ` \| English`) |
|---|---|---|
| `request.submitted` | reviewers of `next_gate` (`NOTIFY_REVIEWERS_G2/G8`), webhooks | 【LLMOps】待審：req-1a2b3c4d 智慧製造處（法遵審查（G2）） — team, alias, models, data class, requests/day, external / Taiwan-only, gate flow, review link. Nothing is sent when no review is needed. |
| `request.decided` | requester | approve: 已通過〈gate〉 + next step and comment. reject: 未通過〈gate〉 + reason and reviewer contact. request_changes: 需要補件 + what to change and a link. |
| `request.decided` (approve, more review needed) | reviewers of the next gate, webhooks | 待審 … 輪到〈next gate〉審查 |
| `request.approved` | requester | 已核准，正在開通試用／正式金鑰 — models, budget (trial total or per period), rpm / tpm, expiry |
| `key.provisioned` | requester | 試用／正式金鑰已開通 — key alias, secret_ref, fetch hint, models, expiry in Taipei time, gateway URL, "never paste the key into email, chat, tickets or code", help link |
| `key.provisioned` (trial) | `NOTIFY_REVIEWERS_G9` (default: the G8 reviewers), webhooks | 試用開始 — decide launch (G9) before the trial ends |
| `key.failed` | `NOTIFY_ADMINS`, webhooks | 金鑰開通失敗 — the error and how to retry (`POST /api/requests/{id}/reprovision`) |
| `release.published` | admins, webhooks | 資料發布 〈release〉（暫停，待確認 when held） — counts of new routes / price changes / retirements |
| `gateway.version_changed` | admins, webhooks | Gateway 版本變更：〈gateway〉 old → new |
| `test.run.completed` | admins, webhooks | 安全測試完成／部分完成／失敗：〈run_id〉 |
| `model.status_changed` (`to` = `trial` or `available`) | every follower of the model (`requests.subscriptions`), one email each | 〈model〉 開放試用申請 \| …is open for trial requests, or 〈model〉 開放申請 \| …can now be requested — status change, optional `eta` (預計時程), model page link (`NOTIFY_MODEL_URL`), how to unfollow. `paused`, `retired`, `watch` and `evaluating` send nothing. |

Other events are acknowledged without sending anything.

Messages to the requester personally go only by email, never to the shared chat. `WEBHOOK_EVENTS` narrows
further which events reach the chat.

Followers of a model are read from `requests.subscriptions` (written by request-api's `/api/subscriptions`).
The read happens in the same transaction as the fan-out. Each follower with an email gets their own delivery
job, deduped per (event id, follower), so nobody sees other followers' addresses or gets a duplicate when the
event is redelivered.

The requester's address comes from the event payloads. request-api adds `requester_email` to
`request.decided` and `request.approved`, and key-provisioner copies it into `key.provisioned`. Reviewer and
admin addresses come from the environment.

## Delivery

An event job is rendered into messages and fanned out, in one transaction, into one **delivery job** per
(message × channel) on the same queue. Each job carries:
- for email: the recipients, subject and body;
- for webhooks: a hash of the URL, never the URL, because webhook URLs carry tokens.

Each delivery is retried on its own, up to `NOTIFY_MAX_ATTEMPTS` with back-off of 10 s, 20 s, … up to 1 h.
A failing webhook therefore never mails people twice. Dedupe keys stop a redelivered event from fanning out
twice.

- SMTP: STARTTLS (default) or implicit TLS (`SMTP_SSL`), optional login. The message is UTF-8 with
  `Auto-Submitted: auto-generated`. A 5xx rejection or all recipients refused is permanent; 4xx, auth
  errors and connection errors are retried.
- Webhooks: `POST` JSON. With `WEBHOOK_FORMAT=text` the body is `{"text": "<subject>\n\n<body>"}` (Google
  Chat, Teams incoming-webhook connectors, Slack-compatible). With `adaptivecard` it is a message with an
  Adaptive Card attachment (Teams Workflows "When a Teams webhook request is received"). 429/5xx are retried
  (honouring `Retry-After`); other 4xx are permanent. Only the host is logged.
- Dry run (`NOTIFY_DRY_RUN=true`): every delivery is logged (channel, masked recipients, subject, body)
  instead of sent. It is the default when neither `SMTP_HOST` nor `WEBHOOK_URLS` is set, i.e. in local
  development; the worker logs a warning at start.

## Configuration

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `DATABASE_URL` / `_FILE` | yes | – | PostgreSQL (`platform.events/jobs`; reads `requests.subscriptions`) |
| `NOTIFY_DRY_RUN` | no | `true` if no SMTP and no webhook is set, else `false` | log instead of sending |
| `SMTP_HOST` | for email | – | SMTP relay |
| `SMTP_PORT` | no | `587` (STARTTLS) / `465` (`SMTP_SSL`) / `25` | port |
| `SMTP_FROM` | with `SMTP_HOST` | – | sender, e.g. `LLMOps 平台 <llmops@example.com>` |
| `SMTP_STARTTLS` | no | `true` unless `SMTP_SSL` | upgrade with STARTTLS |
| `SMTP_SSL` | no | `false` | implicit TLS |
| `SMTP_USERNAME`, `SMTP_PASSWORD` / `SMTP_PASSWORD_FILE` | no | – | SMTP login |
| `SMTP_TIMEOUT` | no | `20` | seconds |
| `WEBHOOK_URLS` / `WEBHOOK_URLS_FILE` | no | – | comma-separated incoming-webhook URLs (secret: they carry tokens) |
| `WEBHOOK_FORMAT` | no | `text` | `text` or `adaptivecard` |
| `WEBHOOK_EVENTS` | no | every event with a team message | comma-separated events posted to webhooks |
| `NOTIFY_REVIEWERS_G2`, `NOTIFY_REVIEWERS_G8` | no | – | comma-separated reviewer emails per gate |
| `NOTIFY_REVIEWERS_G9` | no | `NOTIFY_REVIEWERS_G8` | who decides launch after a trial |
| `NOTIFY_ADMINS` | no | – | comma-separated admin emails |
| `NOTIFY_REQUEST_URL` | no | – | link template, must contain `{request_id}`, e.g. `https://llmops.example/#/requests/{request_id}` |
| `NOTIFY_MODEL_URL` | no | – | model page link template, must contain `{model_id}`, e.g. `https://llmops.example/#/model/{model_id}` |
| `NOTIFY_GATEWAY_URL` | no | – | gateway base URL shown with a new key |
| `NOTIFY_KEY_HELP_URL` | no | – | how-to page for using keys |
| `NOTIFY_ENGLISH_SUBJECT` | no | `true` | append ` \| <English>` to subjects |
| `NOTIFY_SUBJECT_PREFIX` | no | `【LLMOps】` | subject prefix |
| `NOTIFY_TZ_OFFSET_HOURS`, `NOTIFY_TZ_LABEL` | no | `8`, `台北時間` | how expiry times are shown |
| `NOTIFY_MAX_ATTEMPTS` | no | `8` | attempts per delivery |
| `HEALTH_PORT` | no | `8081` | probes |
| `WORKER_POLL_SECONDS` | no | `2` | queue poll interval when idle |
| `DB_POOL_MAX`, `LOG_LEVEL` | no | `5`, `INFO` | shared settings |
| `SSL_CERT_FILE` | no | – | CA bundle for an internal SMTP / webhook certificate |
| `HTTPS_PROXY`, `NO_PROXY` | no | – | webhooks go through the proxy; SMTP does not use it |

## Secrets

All of these can be mounted with `*_FILE`:
- `SMTP_PASSWORD`;
- `WEBHOOK_URLS`, since webhook URLs embed tokens;
- `DATABASE_URL`.

Logs mask recipient addresses (`a***@example.com`) and never contain webhook URLs.

Database grants:
- `SELECT, INSERT, UPDATE` on `platform.jobs` (queue and deliveries), `USAGE` on its sequence;
- `SELECT` on `requests.subscriptions`.

## Events consumed

`request.submitted`, `request.decided`, `request.approved`, `key.provisioned`, `key.failed`, `release.published`,
`gateway.version_changed`, `test.run.completed`, `model.status_changed` (the `notifier` subscriptions in
`contracts/events.md`). The
fields used are those in the contract plus the additions documented in the request-api and key-provisioner
READMEs. Notifier publishes no events.

## Outbound connections

- The SMTP relay (usually on-prem).
- The webhook hosts (e.g. `chat.googleapis.com`, `*.webhook.office.com` / `*.logic.azure.com`) through
  `HTTPS_PROXY`.
- PostgreSQL.

## Resources

About 30 MiB RSS measured. Suggested:
- requests `cpu: 20m, memory: 64Mi`, limits `cpu: 200m, memory: 128Mi`;
- 1 replica (more are safe).

## Tests

```
TEST_DATABASE_URL=postgresql://… pytest components/notifier/tests
```

The tests cover:
- every message;
- `contracts/events.md` matches `SUBSCRIPTIONS`, and the notifier renders exactly its subscribed events;
- the fan-out and dedupe through the real queue, including one mail per model follower;
- dry-run logging;
- SMTP against a small in-process SMTP server (accept, 550, 451, refused recipient, connection refused);
- webhooks through `httpx.MockTransport` (both formats, retry and permanent errors).
