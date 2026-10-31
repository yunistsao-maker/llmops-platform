"""JSON-lines logging to stdout. One object per line: ts, level, component, logger, msg, plus fields.

Usage:
    from llmops_common.log import setup_logging, get_logger, kv
    setup_logging()
    log = get_logger(__name__)
    log.info("job done", extra=kv(job_id=12, queue="publisher"))
Never pass secrets in fields; use redact() for anything that may carry a token.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time

from .config import component_name

__all__ = ["setup_logging", "get_logger", "kv", "redact"]

_RESERVED = {"ts", "level", "component", "logger", "msg"}
_SECRETISH = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}|(sk-[A-Za-z0-9_-]{6})[A-Za-z0-9_-]+")


def redact(text: str | None) -> str | None:
    """Mask bearer tokens and sk- style keys inside free text."""
    if text is None:
        return None
    return _SECRETISH.sub(lambda m: (m.group(1) or m.group(2) or "") + "***", text)


class JsonFormatter(logging.Formatter):
    def __init__(self, component: str):
        super().__init__()
        self.component = component

    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "component": self.component,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            for key, value in fields.items():
                if key not in _RESERVED:
                    out[key] = value
        if record.exc_info:
            out["error"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


def setup_logging(component: str | None = None, level: str | None = None) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(component or component_name()))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel((level or os.environ.get("LOG_LEVEL") or "INFO").upper())
    # third-party chatter
    for noisy in ("botocore", "boto3", "urllib3", "httpx", "httpcore", "s3transfer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def kv(**fields) -> dict:
    """extra= helper: log.info("msg", extra=kv(a=1))."""
    return {"fields": fields}
