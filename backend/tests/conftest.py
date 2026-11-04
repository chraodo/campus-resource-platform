"""Isolated database and account fixtures for API behavior tests."""

from __future__ import annotations

import os
from collections.abc import Generator
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from campus_app.db import _make_engine
from campus_app.main import app
from campus_app.models import Room, Seat, User
from campus_app.security import hash_password


@pytest.fixture
def database_url(tmp_path):
    """Use a private SQLite file or a new schema in the supplied PostgreSQL test DB."""
    postgres = os.getenv("TEST_POSTGRES_URL")
    if not postgres:
        yield f"sqlite:///{(tmp_path / 'campus.db').as_posix()}"
        return
    schema = f"campus_test_{uuid4().hex}"
    admin = create_engine(postgres)
    with admin.begin() as conn:
        conn.execute(CreateSchema(schema))
    try:
        scoped = make_url(postgres).update_query_dict({"options": f"-csearch_path={schema}"})
        yield scoped.render_as_string(hide_password=False)
    finally:
        with admin.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
        admin.dispose()


@pytest.fixture
def system(
    monkeypatch, database_url
) -> Generator[tuple[TestClient, sessionmaker[Session]], None, None]:
    """Exercise the real request dependency and schema built by the production migrations."""
    monkeypatch.setattr("campus_app.main.SEED_DEMO_DATA", False)
    monkeypatch.setattr("campus_app.config.DATABASE_URL", database_url)
    config = Config()
    config.set_main_option("script_location", "backend/migrations")
    command.upgrade(config, "head")
    engine = _make_engine(database_url)
    testing_sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("campus_app.db.SessionLocal", testing_sessions)
    monkeypatch.setattr("campus_app.worker.SessionLocal", testing_sessions)
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
    engine.dispose()


def login(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "demo-pass-123"},
    )
    assert response.status_code == 200, response.text
