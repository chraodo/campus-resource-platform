"""Password hashing and opaque token helpers."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import date

from campus_app.config import APP_SECRET

PBKDF2_ROUNDS = 420_000


def hash_password(password: str) -> str:
    """Return a salted PBKDF2 hash suitable for storage."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Compare a password with the stored PBKDF2 representation."""
    try:
        algorithm, rounds, salt_hex, expected_hex = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(candidate.hex(), expected_hex)
    except (ValueError, TypeError):
        return False


def new_token() -> str:
    """Create a high-entropy opaque bearer token."""
    return secrets.token_urlsafe(40)


def token_hash(token: str) -> str:
    """Hash a bearer token before it is stored in the database."""
    return hashlib.sha256(token.encode()).hexdigest()


def checkin_code_hash(code: str) -> str:
    """Hash a check-in code before it is stored."""
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def daily_room_code(room_id: str, day: date) -> str:
    """Derive the daily screen code for one room without storing its plaintext."""
    message = f"{room_id}:{day.isoformat()}".encode()
    return hmac.new(APP_SECRET.encode(), message, hashlib.sha256).hexdigest()[:8].upper()


def verify_daily_room_code(code: str, room_id: str, day: date) -> bool:
    """Compare an entered screen code with the room's current daily code."""
    candidate = code.strip().upper()
    return hmac.compare_digest(candidate, daily_room_code(room_id, day))
