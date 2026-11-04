"""Regressions for booking races, deadlines, availability and Agent authorization."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from time import sleep

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from campus_app import domain, worker
from campus_app.clock import now_local
from campus_app.models import (
    ApprovalRecord,
    AuditEvent,
    ClassroomApplication,
    Notification,
    Reservation,
    ResourceHold,
    WorkOrder,
)

from .conftest import login
from .test_business_flows import future_start


def test_overlapping_concurrent_requests_share_one_seat(system, monkeypatch):
    client, sessions = system
    login(client, "student")
    start = datetime.fromisoformat(future_start())
    entrants = Barrier(2)
    check_conflict = domain.find_seat_conflict

    def hold_after_check(*args):
        # Widen the original read-before-insert race; a serialized writer never
        # reaches this point until the previous request has committed.
        conflict = check_conflict(*args)
        if conflict is None:
            sleep(0.4)
        return conflict

    monkeypatch.setattr(domain, "find_seat_conflict", hold_after_check)

    def book(browser, when):
        entrants.wait(timeout=5)
        return browser.post(
            "/api/v1/reservations",
            json={"seat_id": "A01", "start_at": when.isoformat(), "duration_hours": 2},
        ).status_code

    with TestClient(client.app) as other, ThreadPoolExecutor(2) as pool:
        login(other, "student2")
        first = pool.submit(book, client, start)
        second = pool.submit(book, other, start + timedelta(hours=1))
        assert sorted([first.result(), second.result()]) == [201, 409]
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Reservation)) == 1


def test_concurrent_idempotent_retries_return_the_same_booking(system):
    client, sessions = system
    login(client, "student")
    payload = {"seat_id": "A01", "start_at": future_start(), "duration_hours": 1}
    ready = Barrier(2)

    def submit(browser):
        ready.wait(timeout=5)
        response = browser.post(
            "/api/v1/reservations", json=payload, headers={"Idempotency-Key": "retry"}
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    with TestClient(client.app) as other, ThreadPoolExecutor(2) as pool:
        login(other, "student")
        ids = list(pool.map(submit, [client, other]))
    assert ids[0] == ids[1]
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Reservation)) == 1


def test_search_and_booking_agree_on_opening_hours(system):
    client, _ = system
    login(client, "student")
    day = future_start()[:10]

    def search(hour):
        return client.get(
            "/api/v1/seats", params={"date": day, "start_time": hour, "duration_hours": 1}
        ).json()

    closed = search("03:00")
    assert closed["available_count"] == 0
    assert all(not seat["available"] and seat["blocked_by"]["reason"] for seat in closed["seats"])
    assert (
        client.post(
            "/api/v1/reservations",
            json={"seat_id": "A01", "start_at": day + "T03:00", "duration_hours": 1},
        ).status_code
        == 422
    )
    early = {s["id"]: s["available"] for s in search("07:00")["seats"]}
    assert early["A01"] and not early["C201-01"]


def test_agent_rechecks_permissions_for_pending_writes(system):
    client, sessions = system
    login(client, "student")
    uid = client.get("/api/v1/auth/me").json()["user"]["id"]
    start = future_start()
    booking = {"seat_id": "A01", "start_at": start, "duration_hours": 1}
    rid = client.post("/api/v1/reservations", json=booking).json()["id"]
    previews = []
    for kind, payload in [
        ("reserve_seat", {**booking, "seat_id": "A02"}),
        (
            "submit_room_application",
            {
                "room_id": "C201",
                "start_at": start,
                "end_at": start[:10] + "T12:00",
                "purpose": "Team meeting",
            },
        ),
        (
            "report_fault",
            {"seat_id": "A02", "title": "Broken outlet", "description": "There is no power."},
        ),
        ("cancel_reservation", {"reservation_id": rid}),
    ]:
        res = client.post(
            "/api/v1/agent/actions/preview", json={"action_type": kind, "payload": payload}
        )
        assert res.status_code == 201, res.text
        previews.append(res.json()["id"])
    with TestClient(client.app) as admin:
        login(admin, "admin")
        assert (
            admin.patch(f"/api/v1/admin/users/{uid}/role", json={"role": "repair"}).status_code
            == 200
        )
    for aid in previews:
        res = client.post(f"/api/v1/agent/actions/{aid}/confirm", json={"confirmed": True})
        assert res.status_code == 403
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Reservation)) == 1
        assert db.get(Reservation, rid).status == "confirmed"
        assert db.scalar(select(func.count()).select_from(ClassroomApplication)) == 0
        assert db.scalar(select(func.count()).select_from(WorkOrder)) == 0


@pytest.mark.parametrize("via_agent", [False, True])
def test_late_cancel_records_one_no_show_before_worker(system, via_agent):
    client, sessions = system
    login(client, "student")
    rid = client.post(
        "/api/v1/reservations",
        json={"seat_id": "A01", "start_at": future_start(), "duration_hours": 1},
    ).json()["id"]
    url = f"/api/v1/reservations/{rid}/cancel"
    body = None
    if via_agent:
        aid = client.post(
            "/api/v1/agent/actions/preview",
            json={"action_type": "cancel_reservation", "payload": {"reservation_id": rid}},
        ).json()["id"]
        url = f"/api/v1/agent/actions/{aid}/confirm"
        body = {"confirmed": True}
    with sessions.begin() as db:
        row = db.get(Reservation, rid)
        row.start_at = now_local() - timedelta(minutes=15)
        row.end_at = row.start_at + timedelta(hours=1)
    assert client.post(url, json=body).status_code == 409
    assert worker.process_due_events()["no_shows"] == 0
    with sessions() as db:
        row = db.get(Reservation, rid)
        assert row.status == "expired" and row.violation
        assert db.scalars(
            select(AuditEvent.id).where(
                AuditEvent.entity_id == rid, AuditEvent.action == "reservation.no_show"
            )
        ).one()
        assert db.scalars(
            select(Notification.id).where(Notification.unique_key == f"reservation:{rid}:no-show")
        ).one()


@pytest.mark.parametrize("stage", [1, 2])
def test_expired_approval_cannot_advance_before_worker(system, stage):
    client, sessions = system
    login(client, "student")
    start = future_start()
    aid = client.post(
        "/api/v1/classroom-applications",
        json={
            "room_id": "C201",
            "start_at": start,
            "end_at": start[:10] + "T12:00",
            "purpose": "Team meeting",
        },
    ).json()["id"]
    url = f"/api/v1/classroom-applications/{aid}/decision"
    if stage == 2:
        login(client, "approver1")
        assert client.post(url, json={"decision": "approve"}).status_code == 200
    login(client, f"approver{stage}")
    with sessions.begin() as db:
        db.get(ClassroomApplication, aid).timeout_at = now_local()
    assert client.post(url, json={"decision": "approve"}).status_code == 409
    assert worker.process_due_events()["expired_applications"] == 0
    with sessions() as db:
        assert db.get(ClassroomApplication, aid).status == "expired"
        assert (
            len(
                db.scalars(select(ApprovalRecord).where(ApprovalRecord.application_id == aid)).all()
            )
            == stage - 1
        )
        assert not db.scalar(select(ResourceHold.id).where(ResourceHold.source_id == aid))
        assert db.scalars(
            select(Notification.id).where(Notification.unique_key == f"classroom:{aid}:timeout")
        ).one()
