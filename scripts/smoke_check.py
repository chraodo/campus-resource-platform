"""Exercise login, seat search, idempotent booking, and conflict behavior."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from http.cookiejar import CookieJar
from zoneinfo import ZoneInfo

BASE_URL = os.getenv("SMOKE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "demo2026")


def call(opener: urllib.request.OpenerDirector, path: str, data: dict | None = None, **kwargs):
    body = json.dumps(data).encode() if data is not None else None
    headers = kwargs.pop("headers", {})
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{BASE_URL}{path}", data=body, headers=headers, method=kwargs.pop("method", "GET")
    )
    try:
        with opener.open(request, timeout=5) as response:
            payload = response.read()
            return response.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as error:
        payload = error.read()
        return error.code, json.loads(payload) if payload else None


def main() -> int:
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    for _attempt in range(60):
        try:
            status, health = call(opener, "/health")
            if status == 200 and health["status"] == "ok":
                break
        except (OSError, TimeoutError):
            pass
        time.sleep(2)
    else:
        print("API did not become healthy", file=sys.stderr)
        return 1

    status, _login = call(
        opener,
        "/api/v1/auth/login",
        {"username": "student", "password": DEMO_PASSWORD},
        method="POST",
    )
    if status != 200:
        print(f"Demo login failed: HTTP {status}", file=sys.stderr)
        return 1

    tomorrow = (datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(days=1)).date().isoformat()
    query = f"/api/v1/seats?date={tomorrow}&start_time=10:00&duration_hours=1"
    status, result = call(opener, query)
    if status != 200:
        print(f"Seat search failed: HTTP {status}", file=sys.stderr)
        return 1
    available = next((seat for seat in result["seats"] if seat["available"]), None)
    if available is None:
        print("Seed data did not leave an available seat", file=sys.stderr)
        return 1

    start_at = f"{tomorrow}T10:00:00"
    booking = {"seat_id": available["id"], "start_at": start_at, "duration_hours": 1}
    headers = {"Idempotency-Key": "ci-smoke-reservation"}
    created_status, created = call(
        opener, "/api/v1/reservations", booking, method="POST", headers=headers
    )
    repeated_status, repeated = call(
        opener, "/api/v1/reservations", booking, method="POST", headers=headers
    )
    if (
        created_status != 201
        or repeated_status != 201
        or created["id"] != repeated["id"]
    ):
        print("Booking idempotency check failed", file=sys.stderr)
        return 1

    conflict_status, _conflict = call(
        opener,
        "/api/v1/reservations",
        booking,
        method="POST",
        headers={"Idempotency-Key": "ci-smoke-conflicting-reservation"},
    )
    if conflict_status != 409:
        print(f"Expected a conflict, got HTTP {conflict_status}", file=sys.stderr)
        return 1

    print("Smoke check passed: login, seat search, idempotency, and conflict rejection")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
