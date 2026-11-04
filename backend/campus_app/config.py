"""Runtime settings shared by the API, worker, and migration tooling."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _default_database_url() -> str:
    path = (DATA_DIR / "campus.db").as_posix()
    return f"sqlite:///{path}"


DATABASE_URL = os.getenv("DATABASE_URL", _default_database_url())
APP_SECRET = os.getenv("APP_SECRET", "local-development-only-change-me")
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Shanghai")
APP_ENV = os.getenv("APP_ENV", "development")
SEED_DEMO_DATA = os.getenv("SEED_DEMO_DATA", "true").lower() in {"1", "true", "yes"}
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "12"))
APPROVAL_TIMEOUT_HOURS = int(os.getenv("APPROVAL_TIMEOUT_HOURS", "24"))
WORK_ORDER_SLA_HOURS = int(os.getenv("WORK_ORDER_SLA_HOURS", "24"))
CHECKIN_LATE_MINUTES = int(os.getenv("CHECKIN_LATE_MINUTES", "15"))
CHECKIN_REMINDER_MINUTES = int(os.getenv("CHECKIN_REMINDER_MINUTES", "10"))
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")
SMTP_STARTTLS = os.getenv("SMTP_STARTTLS", "true").lower() in {"1", "true", "yes"}
SMTP_SSL = os.getenv("SMTP_SSL", "false").lower() in {"1", "true", "yes"}
EMAIL_MAX_ATTEMPTS = int(os.getenv("EMAIL_MAX_ATTEMPTS", "5"))
