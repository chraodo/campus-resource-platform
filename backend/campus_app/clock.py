"""Local wall-clock helper used for the campus time zone."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from campus_app.config import APP_TIMEZONE


def now_local() -> datetime:
    """Return a naive wall-clock timestamp in the configured campus time zone."""
    return datetime.now(ZoneInfo(APP_TIMEZONE)).replace(tzinfo=None)
