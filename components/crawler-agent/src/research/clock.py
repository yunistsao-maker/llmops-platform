"""Time helpers.  Portal dates are Taipei calendar dates; timestamps are UTC."""

from __future__ import annotations

import datetime as dt
import os

TAIPEI = dt.timezone(dt.timedelta(hours=8), "Asia/Taipei")


def now_utc() -> dt.datetime:
    forced = os.environ.get("RESEARCH_NOW")  # e.g. 2026-10-02T03:00:00Z (tests, replays)
    if forced:
        return dt.datetime.fromisoformat(forced.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    return dt.datetime.now(dt.timezone.utc)


def today() -> dt.date:
    forced = os.environ.get("RESEARCH_TODAY")
    if forced:
        return dt.date.fromisoformat(forced)
    return now_utc().astimezone(TAIPEI).date()


def iso_now() -> str:
    return now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
