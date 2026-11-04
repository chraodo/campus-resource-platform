"""Database engine, sessions, and base class."""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Request
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from campus_app.config import DATABASE_URL


class Base(DeclarativeBase):
    """Base class for mapped database tables."""


def _make_engine(url: str) -> Engine:
    options: dict[str, object] = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **options)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(connection, _record) -> None:  # type: ignore[no-untyped-def]
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = _make_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def begin_sqlite_write(db: Session) -> None:
    """Acquire the SQLite writer slot before reading state used by a write."""
    if db.get_bind().dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))


def get_db(request: Request) -> Generator[Session, None, None]:
    """Yield a database session for one request."""
    session = SessionLocal()
    try:
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            begin_sqlite_write(session)
        yield session
    finally:
        session.close()
