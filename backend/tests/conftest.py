"""Isolated database and account fixtures for API behavior tests."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from campus_app.db import Base
from campus_app.dependencies import get_db
from campus_app.main import app
from campus_app.models import Room, Seat, User
from campus_app.security import hash_password


@pytest.fixture
def system(monkeypatch) -> Generator[tuple[TestClient, sessionmaker[Session]], None, None]:
    """Provide a fresh in-memory SQLite database and a browser client."""
    monkeypatch.setattr("campus_app.main.SEED_DEMO_DATA", False)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:  # type: ignore[no-untyped-def]
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    testing_sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db() -> Generator[Session, None, None]:
        db = testing_sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with testing_sessions() as db:
        db.add_all(
            [
                User(
                    username=name,
                    display_name=label,
                    password_hash=hash_password("demo-pass-123"),
                    role=role,
                )
                for name, label, role in [
                    ("student", "Student", "student"),
                    ("student2", "Student Two", "student"),
                    ("approver1", "Approver One", "approver_l1"),
                    ("approver2", "Approver Two", "approver_l2"),
                    ("repair", "Repair Worker", "repair"),
                    ("admin", "Administrator", "admin"),
                ]
            ]
        )
        db.add_all(
            [
                Room(
                    id="LIB-1",
                    name="Library Study Room",
                    area="North Campus",
                    room_type="study",
                    capacity=3,
                    open_time="07:00",
                    close_time="23:00",
                ),
                Room(
                    id="C201",
                    name="Classroom 201",
                    area="North Campus",
                    room_type="classroom",
                    capacity=3,
                    open_time="08:00",
                    close_time="22:00",
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                Seat(id="A01", room_id="LIB-1", label="A01", has_power=True),
                Seat(id="A02", room_id="LIB-1", label="A02"),
                Seat(id="A03", room_id="LIB-1", label="A03"),
                Seat(id="C201-01", room_id="C201", label="C201-01"),
                Seat(id="C201-02", room_id="C201", label="C201-02"),
                Seat(id="C201-03", room_id="C201", label="C201-03"),
            ]
        )
        db.commit()

    with TestClient(app) as client:
        yield client, testing_sessions
    app.dependency_overrides.clear()
    engine.dispose()


def login(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "demo-pass-123"},
    )
    assert response.status_code == 200, response.text
