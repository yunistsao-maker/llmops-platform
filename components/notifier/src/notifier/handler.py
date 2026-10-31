"""Queue `notifier`: event jobs fan out into one delivery job per (message, channel) on the same queue.

    event job     {"event", "event_id", "payload"}  -> render -> enqueue delivery jobs (one transaction, deduped)
    delivery job  {"delivery": "email"|"webhook", ...} -> send (or log in dry-run mode)
Splitting them means a failing webhook is retried on its own without mailing people twice.
model.status_changed (to trial / available) goes to every subscriber of the model, read from
requests.subscriptions in the same transaction: one delivery per subscriber, deduped per (event id, subscriber).
Unknown events are acknowledged without sending anything.
"""
from __future__ import annotations

import dataclasses
import hashlib
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

import httpx

from llmops_common import db, queue
from llmops_common.config import env
from llmops_common.log import get_logger, kv
from llmops_common.queue import Job, PermanentError, RetryLater

from .messages import RENDERERS, Message, render
from .settings import Settings

QUEUE = "notifier"
log = get_logger("notifier")


def url_id(url: str) -> str:
    """Stable id of a webhook URL (the URL itself carries a token, so it never goes into a job or a log)."""
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def mask(address: str) -> str:
    name, _, domain = address.partition("@")
    return (name[:1] + "***@" + domain) if domain else "***"


class Notifier:
    def __init__(self, settings: Settings, connection_factory=None, smtp_factory=None,
                 http: httpx.Client | None = None):
        self.settings = settings
        self.connection = connection_factory or db.connection
        self.smtp_factory = smtp_factory
        self.http = http or httpx.Client(timeout=10.0)

    # -- event -> deliveries ----------------------------------------------------------------------------
    def deliveries(self, event: str, natural_id, messages: list[Message]) -> list[tuple[dict, str]]:
        s = self.settings
        out = []
        for i, msg in enumerate(messages):
            key = msg.key or str(i)
            # no "event" key: delivery jobs must not look like event jobs
            base = {"source_event": event, "audience": msg.audience, "subject": msg.subject, "body": msg.body}
            to = list(dict.fromkeys(a.strip() for a in msg.to if a and a.strip()))
            if to and s.email_enabled:
                out.append(({"delivery": "email", "to": to, **base}, f"notify:{natural_id}:{key}:email"))
            elif to:
                log.warning("email recipients configured but SMTP_HOST is not set; email skipped",
                            extra=kv(event=event, audience=msg.audience))
            if msg.webhook and (s.webhook_events is None or event in s.webhook_events):
                for url in s.webhook_urls:
                    uid = url_id(url)
                    out.append(({"delivery": "webhook", "webhook": uid, **base},
                                f"notify:{natural_id}:{key}:webhook:{uid}"))
        return out

    @staticmethod
    def expand_subscribers(conn, event: str, data: dict, messages: list[Message]) -> list[Message]:
        """Replace a "subscribers" template by one message per subscriber of the model that has an email."""
        out: list[Message] = []
        for msg in messages:
            if msg.audience != "subscribers":
                out.append(msg)
                continue
            model_id = data.get("model_id")
            if not model_id:
                raise PermanentError(f"{event} without model_id")
            rows = conn.execute("SELECT subscriber_sub, subscriber_email FROM requests.subscriptions "
                                "WHERE model_id = %s ORDER BY created_at, subscriber_sub", (model_id,)).fetchall()
            sendable = [r for r in rows if (r["subscriber_email"] or "").strip()]
            for r in sendable:
                sub_id = hashlib.sha256(r["subscriber_sub"].encode()).hexdigest()[:16]
                out.append(dataclasses.replace(msg, audience="subscriber", to=(r["subscriber_email"].strip(),),
                                               key=f"sub:{sub_id}"))
            log.info("model subscribers", extra=kv(event=event, model_id=model_id, subscribers=len(rows),
                                                    without_email=len(rows) - len(sendable)))
        return out

    def handle(self, job: Job) -> None:
        if "delivery" in job.payload and "event" not in job.payload:
            return self.deliver(job.payload)
        event = job.event
        if event not in RENDERERS:
            log.info("event acknowledged, nothing to send", extra=kv(job_id=job.id, event=event))
            return
        messages = render(event, job.data, self.settings)
        if not messages:
            log.info("nothing to send for event", extra=kv(event=event))
            return
        natural_id = job.payload.get("event_id") or f"job{job.id}"
        with self.connection() as conn:     # subscriber lookup and fan-out in one transaction
            planned = self.deliveries(event, natural_id, self.expand_subscribers(conn, event, job.data, messages))
            for payload, dedupe in planned:
                queue.enqueue(conn, QUEUE, payload, dedupe_key=dedupe, max_attempts=self.settings.max_attempts)
        if planned:
            log.info("notifications queued", extra=kv(event=event, deliveries=len(planned)))
        else:
            log.info("no recipients for event", extra=kv(event=event, messages=len(messages)))

    # -- delivery ----------------------------------------------------------------------------------------
    def deliver(self, d: dict) -> None:
        channel = d.get("delivery")
        if self.settings.dry_run:
            log.info("dry-run notification", extra=kv(channel=channel, event=d.get("source_event"),
                                                      to=[mask(a) for a in d.get("to") or []], webhook=d.get("webhook"),
                                                      subject=d.get("subject"), body=(d.get("body") or "")[:1000]))
            return
        if channel == "email":
            return self.send_email(d["to"], d["subject"], d["body"])
        if channel == "webhook":
            return self.post_webhook(d["webhook"], d["subject"], d["body"])
        raise PermanentError(f"unknown delivery channel {channel!r}")

    def _smtp(self):
        s = self.settings
        if self.smtp_factory:
            return self.smtp_factory()
        context = ssl.create_default_context()   # honours SSL_CERT_FILE for an internal CA
        if s.smtp_ssl:
            return smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout, context=context)
        smtp = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout)
        try:
            smtp.ehlo()
            if s.smtp_starttls:
                smtp.starttls(context=context)
                smtp.ehlo()
        except BaseException:
            smtp.close()
            raise
        return smtp

    def send_email(self, to: list[str], subject: str, body: str) -> None:
        s = self.settings
        if not s.smtp_host:
            raise PermanentError("SMTP_HOST is not set")
        msg = EmailMessage()
        msg["From"] = s.smtp_from
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        msg["Date"] = formatdate(usegmt=True)
        msg["Message-ID"] = make_msgid(domain=(parseaddr(s.smtp_from)[1].partition("@")[2] or None))
        msg["Auto-Submitted"] = "auto-generated"     # RFC 3834: no auto-replies
        msg.set_content(body)
        try:
            with self._smtp() as smtp:
                if s.smtp_username:
                    smtp.login(s.smtp_username, env("SMTP_PASSWORD", "") or "")
                refused = smtp.send_message(msg)
        except smtplib.SMTPAuthenticationError as exc:
            raise RetryLater(f"smtp authentication failed ({exc.smtp_code})") from None
        except smtplib.SMTPRecipientsRefused as exc:
            raise PermanentError(f"smtp refused all {len(exc.recipients)} recipients") from None
        except smtplib.SMTPResponseException as exc:
            if 500 <= exc.smtp_code < 600:
                raise PermanentError(f"smtp rejected the message ({exc.smtp_code})") from None
            raise RetryLater(f"smtp temporary failure ({exc.smtp_code})") from None
        except (smtplib.SMTPException, OSError) as exc:
            raise RetryLater(f"smtp unavailable: {type(exc).__name__}") from None
        if refused:
            log.warning("some recipients refused", extra=kv(refused=len(refused), sent=len(to) - len(refused)))
        log.info("email sent", extra=kv(recipients=len(to) - len(refused or {}), subject=subject))

    def webhook_payload(self, subject: str, body: str) -> dict:
        if self.settings.webhook_format == "adaptivecard":     # Teams workflows ("When a webhook request is received")
            card = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard",
                    "version": "1.4", "body": [{"type": "TextBlock", "text": subject, "weight": "Bolder", "wrap": True},
                                               {"type": "TextBlock", "text": body, "wrap": True}]}
            return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive",
                                                         "contentUrl": None, "content": card}]}
        return {"text": f"{subject}\n\n{body}"}             # Google Chat, Teams connectors, Slack-compatible

    def post_webhook(self, webhook: str, subject: str, body: str) -> None:
        url = next((u for u in self.settings.webhook_urls if url_id(u) == webhook), None)
        if url is None:
            log.warning("webhook no longer configured; delivery dropped", extra=kv(webhook=webhook))
            return
        host = httpx.URL(url).host
        try:
            resp = self.http.post(url, json=self.webhook_payload(subject, body))
        except httpx.HTTPError as exc:
            raise RetryLater(f"webhook {host} unreachable: {type(exc).__name__}") from None
        if resp.status_code < 300:
            log.info("webhook posted", extra=kv(host=host, status=resp.status_code))
            return
        if resp.status_code in (408, 429) or resp.status_code >= 500:
            retry_after = resp.headers.get("retry-after", "")
            raise RetryLater(f"webhook {host} HTTP {resp.status_code}",
                             delay=float(retry_after) if retry_after.isdigit() else None)
        raise PermanentError(f"webhook {host} HTTP {resp.status_code}")

    def ready(self) -> None:
        db.ping()
