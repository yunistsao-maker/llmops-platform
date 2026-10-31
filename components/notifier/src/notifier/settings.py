"""notifier settings (environment only; see README)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from llmops_common.config import ConfigError, env, env_bool, env_float, env_int, env_list

WEBHOOK_FORMATS = ("text", "adaptivecard")


@dataclass(frozen=True)
class Settings:
    dry_run: bool = True
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_from: str | None = None
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    smtp_username: str | None = None
    smtp_timeout: float = 20.0
    webhook_urls: tuple[str, ...] = ()
    webhook_format: str = "text"
    webhook_events: frozenset | None = None          # None = every event that has a non-personal message
    reviewers: dict = field(default_factory=dict)      # {"G2": (...), "G8": (...), "G9": (...)}
    admins: tuple[str, ...] = ()
    request_url: str | None = None                     # e.g. https://portal.example/#/requests/{request_id}
    model_url: str | None = None                       # e.g. https://portal.example/#/model/{model_id}
    gateway_url: str | None = None
    key_help_url: str | None = None
    english_subject: bool = True
    subject_prefix: str = "【LLMOps】"
    tz_offset_hours: float = 8.0
    tz_label: str = "台北時間"
    max_attempts: int = 8
    health_port: int = 8081
    poll_seconds: float = 2.0

    @property
    def tz(self) -> dt.timezone:
        return dt.timezone(dt.timedelta(hours=self.tz_offset_hours))

    @property
    def email_enabled(self) -> bool:
        return self.dry_run or bool(self.smtp_host)

    @classmethod
    def from_env(cls) -> "Settings":
        smtp_host = env("SMTP_HOST", None)
        webhooks = env_list("WEBHOOK_URLS", ())
        dry_run = env_bool("NOTIFY_DRY_RUN", not (smtp_host or webhooks))
        smtp_ssl = env_bool("SMTP_SSL", False)
        starttls = env_bool("SMTP_STARTTLS", not smtp_ssl)
        if smtp_ssl and starttls:
            raise ConfigError("set either SMTP_SSL (implicit TLS, port 465) or SMTP_STARTTLS, not both")
        smtp_from = env("SMTP_FROM", None)
        if smtp_host and not smtp_from:
            raise ConfigError("SMTP_FROM is required when SMTP_HOST is set")
        fmt = env("WEBHOOK_FORMAT", "text")
        if fmt not in WEBHOOK_FORMATS:
            raise ConfigError(f"WEBHOOK_FORMAT must be one of {', '.join(WEBHOOK_FORMATS)}")
        for url in webhooks:
            if not url.startswith(("https://", "http://")):
                raise ConfigError("WEBHOOK_URLS must be http(s) URLs")
        events = env_list("WEBHOOK_EVENTS", ())
        request_url = env("NOTIFY_REQUEST_URL", None)
        if request_url and "{request_id}" not in request_url:
            raise ConfigError("NOTIFY_REQUEST_URL must contain {request_id}")
        model_url = env("NOTIFY_MODEL_URL", None)
        if model_url and "{model_id}" not in model_url:
            raise ConfigError("NOTIFY_MODEL_URL must contain {model_id}")
        g8 = env_list("NOTIFY_REVIEWERS_G8", ())
        return cls(
            dry_run=dry_run,
            smtp_host=smtp_host,
            smtp_port=env_int("SMTP_PORT", 465 if smtp_ssl else (587 if starttls else 25)),
            smtp_from=smtp_from,
            smtp_starttls=starttls,
            smtp_ssl=smtp_ssl,
            smtp_username=env("SMTP_USERNAME", None),
            smtp_timeout=env_float("SMTP_TIMEOUT", 20.0),
            webhook_urls=webhooks,
            webhook_format=fmt,
            webhook_events=frozenset(events) if events else None,
            reviewers={"G2": env_list("NOTIFY_REVIEWERS_G2", ()), "G8": g8,
                       "G9": env_list("NOTIFY_REVIEWERS_G9", g8)},
            admins=env_list("NOTIFY_ADMINS", ()),
            request_url=request_url,
            model_url=model_url,
            gateway_url=env("NOTIFY_GATEWAY_URL", None),
            key_help_url=env("NOTIFY_KEY_HELP_URL", None),
            english_subject=env_bool("NOTIFY_ENGLISH_SUBJECT", True),
            subject_prefix=env("NOTIFY_SUBJECT_PREFIX", "【LLMOps】"),
            tz_offset_hours=env_float("NOTIFY_TZ_OFFSET_HOURS", 8.0),
            tz_label=env("NOTIFY_TZ_LABEL", "台北時間"),
            max_attempts=env_int("NOTIFY_MAX_ATTEMPTS", 8),
            health_port=env_int("HEALTH_PORT", 8081),
            poll_seconds=env_float("WORKER_POLL_SECONDS", 2.0),
        )
