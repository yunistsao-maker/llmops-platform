"""Identifiers and time helpers (always UTC)."""
from __future__ import annotations

import datetime as _dt
import secrets

__all__ = ["utcnow", "iso", "new_run_id", "new_release_id"]


def utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def iso(value: _dt.datetime | None = None) -> str:
    return (value or utcnow()).astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id(now: _dt.datetime | None = None) -> str:
    """20261031T061346-a55616 (same shape as the existing security runs)."""
    return (now or utcnow()).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)


def new_release_id(now: _dt.datetime | None = None) -> str:
    """20261031T061346Z (same shape as the existing data-plane releases)."""
    return (now or utcnow()).strftime("%Y%m%dT%H%M%SZ")
