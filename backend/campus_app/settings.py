"""Database-backed settings with conservative code defaults."""

from __future__ import annotations

from sqlalchemy.orm import Session

from campus_app.models import AppSetting

DEFAULT_SETTINGS = {"max_booking_hours": 4}


def setting_int(db: Session, key: str) -> int:
    """Read one integer setting or fall back to its safe default."""
    setting = db.get(AppSetting, key)
    return int(setting.value) if setting else DEFAULT_SETTINGS[key]
