"""Course acceptance scenarios crossing service, browser, and mail boundaries."""

import os
import socket
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
from http.client import HTTPConnection, RemoteDisconnected

import httpx
import pytest
from aiosmtpd.controller import Controller
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select

from campus_app import config, domain, worker
from campus_app.models import (
    ApprovalRecord,
    AuditEvent,
    Notification,
    Reservation,
    ResourceHold,
    User,
    WorkOrder,
)
from campus_app.security import daily_room_code

from .conftest import login, serve_http
from .test_business_flows import future_start


def pending_final(client):
    login(client, "student")
    start = future_start()
    result = client.post(
        "/api/v1/classroom-applications",
        json={
            "room_id": "C201",
            "start_at": start,
            "end_at": start[:10] + "T12:00",
            "purpose": "Course meeting",
        },
    )
    assert result.status_code == 201
    aid = result.json()["id"]
    login(client, "approver1")
    assert (
        client.post(
            f"/api/v1/classroom-applications/{aid}/decision", json={"decision": "approve"}
        ).json()["current_stage"]
        == 2
    )
    return aid


def room_seats(client, room="C201"):
    rows = client.get(
        "/api/v1/seats", params={"date": future_start()[:10], "start_time": "10:00"}
    ).json()["seats"]
    return [r for r in rows if r["room_id"] == room]


def test_network_final_approval_outage_and_lost_response(system, resource_url, monkeypatch):
    client, sessions = system
    aid = pending_final(client)
    endpoint = f"/api/v1/classroom-applications/{aid}/decision"
    command = {"decision": "approve", "comment": "Reviewed"}
    internal = f"{resource_url}/internal/v1/classroom-holds/{aid}"
    with httpx.Client(trust_env=False) as wire:
        assert wire.put(internal, json={}).status_code == 401
        assert (
            wire.put(
                internal,
                json={},
                headers={"Authorization": "Bearer " + client.cookies["campus_session"]},
            ).status_code
            == 403
        )
    login(client, "approver2")
    # A bound, non-listening socket reliably refuses the connection.
    with socket.socket() as unavailable, monkeypatch.context() as patch:
        unavailable.bind(("127.0.0.1", 0))
        patch.setattr(
            config, "RESOURCE_SERVICE_URL", f"http://127.0.0.1:{unavailable.getsockname()[1]}"
        )
        assert client.post(endpoint, json=command).status_code == 503
    assert client.get("/api/v1/classroom-applications").json()[0]["status"] == "pending"
    assert all(s["available"] for s in room_seats(client))

    # Let the real service persist the approval, then discard only its HTTP reply.
    original = HTTPConnection.getresponse

    def lose_reply(connection):
        response = original(connection)
        assert response.status == 200
        response.read()
        raise RemoteDisconnected("lost response")

    with monkeypatch.context() as patch:
        patch.setattr(HTTPConnection, "getresponse", lose_reply)
        assert client.post(endpoint, json=command).status_code == 503
    replay = client.post(endpoint, json=command)
    assert replay.status_code == 200 and replay.json()["status"] == "approved"
    assert client.post(endpoint, json={**command, "comment": "different"}).status_code == 409
    with sessions() as db:
        assert db.scalars(select(ResourceHold).where(ResourceHold.source_id == aid)).one().active
        assert (
            len(
                db.scalars(select(ApprovalRecord).where(ApprovalRecord.application_id == aid)).all()
            )
            == 2
        )
        assert db.scalars(
            select(Notification).where(Notification.unique_key == f"classroom:{aid}:approved")
        ).one()
    assert len(room_seats(client)) == 3
    assert all(s["blocked_by"]["source_id"] == aid for s in room_seats(client))


def test_final_approval_conflict_can_retry_after_booking_cancelled(system):
    client, _ = system
    aid = pending_final(client)
    login(client, "student")
    booking = client.post(
        "/api/v1/reservations",
        json={"seat_id": "C201-01", "start_at": future_start(), "duration_hours": 1},
    ).json()
    login(client, "approver2")
    url = f"/api/v1/classroom-applications/{aid}/decision"
    conflict = client.post(url, json={"decision": "approve"})
    assert (
        conflict.status_code == 409
        and conflict.json()["detail"]["source"]["source_id"] == booking["id"]
    )
    login(client, "student")
    assert client.post(f"/api/v1/reservations/{booking['id']}/cancel").status_code == 200
    login(client, "approver2")
    assert client.post(url, json={"decision": "approve"}).json()["status"] == "approved"


def test_whole_room_repair_preserves_other_holds(system):
    client, _ = system
    login(client, "student")
    booking = client.post(
        "/api/v1/reservations",
        json={"seat_id": "A01", "start_at": future_start(), "duration_hours": 1},
    ).json()
    report = client.post(
        "/api/v1/work-orders",
        json={
            "room_id": "LIB-1",
            "title": "Power failure",
            "description": "Entire room is without power.",
        },
    )
    assert report.status_code == 201
    wid = report.json()["id"]
    url = f"/api/v1/work-orders/{wid}"
    login(client, "repair")
    uid = client.get("/api/v1/auth/me").json()["user"]["id"]
    assert client.post(url + "/assign", json={"assignee_id": uid}).status_code == 200
    for transition in ["start", "resolve"]:
        assert client.post(url + "/" + transition).status_code == 200
        seats = room_seats(client, "LIB-1")
        assert len(seats) == 3 and all(
            s["blocked_by"]["module"] == "M3" and s["blocked_by"]["source_id"] == wid for s in seats
        )
    login(client, "student")
    assert (
        client.post(
            "/api/v1/reservations",
            json={"seat_id": "A02", "start_at": future_start(), "duration_hours": 1},
        ).status_code
        == 409
    )
    assert client.post(url + "/accept").status_code == 200
    seats = {s["id"]: s for s in room_seats(client, "LIB-1")}
    assert seats["A02"]["available"] and seats["A03"]["available"]
    assert seats["A01"]["blocked_by"]["source_id"] == booking["id"]


def test_second_stage_rejection_leaves_room_free(system):
    client, _ = system
    aid = pending_final(client)
    login(client, "approver2")
    reply = client.post(
        f"/api/v1/classroom-applications/{aid}/decision",
        json={"decision": "reject", "comment": "Use a smaller room"},
    )
    assert reply.status_code == 200 and reply.json()["status"] == "rejected"
    assert all(seat["available"] for seat in room_seats(client))


def test_overdue_room_repair_notifies_admin_only_once(system):
    client, sessions = system
    login(client, "student")
    response = client.post(
        "/api/v1/work-orders",
        json={
            "room_id": "LIB-1",
            "title": "Lighting failure",
            "description": "All lights are off.",
        },
    )
    assert response.status_code == 201
    wid = response.json()["id"]
    with sessions() as db:
        due = db.get(WorkOrder, wid).due_at
    assert worker.process_due_events(due - timedelta(seconds=1))["escalations"] == 0
    assert worker.process_due_events(due)["escalations"] == 1
    assert worker.process_due_events(due + timedelta(minutes=1))["escalations"] == 0
    login(client, "admin")
    alerts = [n for n in client.get("/api/v1/notifications").json() if n["kind"] == "escalation"]
    assert len(alerts) == 1 and alerts[0]["related_id"] == wid


def test_room_hours_control_search_booking_and_pending_approvals(system):
    client, sessions = system
    aid = pending_final(client)
    url = "/api/v1/admin/rooms/C201/hours"
    hours = {"open_time": "12:00", "close_time": "18:00"}
    login(client, "student")
    assert client.patch(url, json=hours).status_code == 403
    login(client, "admin")
    assert client.patch(url, json={**hours, "close_time": "10:00"}).status_code == 422
    assert client.patch(url, json=hours).json()["open_time"] == "12:00"
    login(client, "approver2")
    assert (
        client.post(
            f"/api/v1/classroom-applications/{aid}/decision", json={"decision": "approve"}
        ).status_code
        == 409
    )
    login(client, "student")
    assert all(s["blocked_by"]["module"] == "D" for s in room_seats(client))
    assert (
        client.post(
            "/api/v1/reservations",
            json={"seat_id": "C201-02", "start_at": future_start(), "duration_hours": 1},
        ).status_code
        == 422
    )
    with sessions() as db:
        assert (
            db.scalars(select(AuditEvent).where(AuditEvent.action == "room.hours_changed"))
            .one()
            .details["to"]
            == hours
        )


@pytest.mark.parametrize("checked_in", [False, True])
def test_timed_reminders_delivered_to_smtp(system, monkeypatch, checked_in):
    client, sessions = system
    messages = []

    class Inbox:
        async def handle_DATA(self, server, session, envelope):
            assert envelope.rcpt_tos == ["student@example.test"]
            messages.append(BytesParser(policy=policy.default).parsebytes(envelope.content))
            return "250 accepted"

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    smtp = Controller(Inbox(), hostname="127.0.0.1", port=port)
    smtp.start()
    try:
        for module in (worker, domain):
            monkeypatch.setattr(module, "SMTP_HOST", "127.0.0.1")
            monkeypatch.setattr(module, "SMTP_FROM", "campus@example.test")
        for name, value in {
            "SMTP_PORT": port,
            "SMTP_USERNAME": "",
            "SMTP_SSL": False,
            "SMTP_STARTTLS": False,
        }.items():
            monkeypatch.setattr(worker, name, value)
        login(client, "student")
        start = datetime.fromisoformat(future_start())
        with sessions.begin() as db:
            db.scalar(select(User).where(User.username == "student")).email = "student@example.test"
        rid = client.post(
            "/api/v1/reservations",
            json={"seat_id": "A02", "start_at": start.isoformat(), "duration_hours": 1},
        ).json()["id"]
        worker.deliver_pending_emails()
        messages.clear()
        for minute in (-16, -15, 0, 9, 10, 14, 15, 16):
            current = start + timedelta(minutes=minute)
            if checked_in and minute == 0:
                monkeypatch.setattr("campus_app.routers.m1.now_local", lambda: start)
                assert (
                    client.post(
                        f"/api/v1/reservations/{rid}/check-in",
                        json={"code": daily_room_code("LIB-1", start.date())},
                    ).status_code
                    == 200
                )
            worker.process_due_events(current)
            worker.process_due_events(current)
            expected = (
                int(minute >= -15)
                + int(not checked_in and minute >= 10)
                + int(not checked_in and minute >= 15)
            )
            assert len(messages) == expected
        subjects = [m["Subject"] for m in messages]
        assert subjects == (
            ["座位预约即将开始"]
            if checked_in
            else ["座位预约即将开始", "请尽快签到", "未签到，预约已释放"]
        )
        with sessions() as db:
            row = db.get(Reservation, rid)
            assert row.status == ("checked_in" if checked_in else "expired") and row.violation == (
                not checked_in
            )
        seats = room_seats(client, "LIB-1")
        assert next(s for s in seats if s["id"] == "A02")["available"] == (not checked_in)
    finally:
        smtp.stop()


@pytest.mark.skipif(
    os.getenv("RUN_BROWSER_TESTS") != "1",
    reason="enable with RUN_BROWSER_TESTS=1 and an installed Chromium",
)
def test_browser_rebooks_original_seat_and_updates_hours(system):
    client, sessions = system
    old = datetime.fromisoformat(future_start()) - timedelta(days=65)
    with sessions.begin() as db:
        student = db.scalar(select(User).where(User.username == "student"))
        db.add(
            Reservation(
                id="RSV-HISTORY",
                user_id=student.id,
                seat_id="A02",
                start_at=old,
                end_at=old + timedelta(hours=1),
                duration_hours=1,
                status="checked_in",
            )
        )
    with serve_http(client.app) as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH"))
        page = browser.new_page(timezone_id="Asia/Shanghai")
        page.goto(url)

        def sign_in(name):
            page.locator("#username").fill(name)
            page.locator("#password").fill("demo-pass-123")
            page.locator("#login-form button[type=submit]").click()
            expect(page.locator("#header-user")).to_be_visible()

        sign_in("student")
        page.locator('[data-action="reuse-reservation"][data-id="RSV-HISTORY"]').click()
        expect(page.locator("#seat-date")).to_have_value(future_start()[:10])
        expect(page.locator("#seat-grid [data-seat]")).to_have_count(1)
        seat = page.locator('#seat-grid [data-seat="A02"]')
        expect(seat).to_be_enabled()
        seat.click()
        expect(seat).to_be_disabled()
        expect(seat).to_contain_text("M1")
        page.locator("#all-seats").click()
        expect(page.locator("#seat-grid [data-seat]")).to_have_count(6)
        page.locator("#logout-button").click()
        sign_in("admin")
        page.locator('#main-nav [data-page="admin"]').click()
        page.locator("#hours-room").select_option("LIB-1")
        page.locator("#hours-open").fill("12:00")
        page.locator("#hours-close").fill("20:00")
        page.locator("#hours-form button[type=submit]").click()
        expect(page.locator("#page-message")).to_contain_text("12:00")
        browser.close()
    login(client, "student")
    bookings = client.get("/api/v1/reservations").json()
    new = [r for r in bookings if r["id"] != "RSV-HISTORY"]
    assert len(new) == 1 and new[0]["seat_id"] == "A02" and new[0]["status"] == "confirmed"
    assert all(s["blocked_by"]["module"] == "D" for s in room_seats(client, "LIB-1"))
