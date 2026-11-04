"""Database records for identity, resource booking, approvals, and repairs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from campus_app.clock import now_local
from campus_app.db import Base


class User(Base):
    """A platform account with one role used by access-control rules."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), index=True)
    department_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)


class RoleDefinition(Base):
    """A configurable role and its permission codes."""

    __tablename__ = "role_definitions"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)


class AppSetting(Base):
    """A small set of administrator-managed system parameters."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[int] = mapped_column(Integer)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_local, onupdate=now_local)


class ApiCredential(Base):
    """A hashed browser session or personal API token."""

    __tablename__ = "api_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="session")
    label: Mapped[str] = mapped_column(String(120), default="")
    resource_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    user: Mapped[User] = relationship()


class Room(Base):
    """A bookable campus room."""

    __tablename__ = "rooms"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    area: Mapped[str] = mapped_column(String(120), index=True)
    room_type: Mapped[str] = mapped_column(String(32), default="study")
    access_scope: Mapped[str] = mapped_column(String(20), default="campus")
    department_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    capacity: Mapped[int] = mapped_column(Integer, default=1)
    open_time: Mapped[str] = mapped_column(String(5), default="07:00")
    close_time: Mapped[str] = mapped_column(String(5), default="23:00")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    seats: Mapped[list[Seat]] = relationship(back_populates="room", cascade="all, delete-orphan")


class Seat(Base):
    """A seat managed by the shared room and seat directory."""

    __tablename__ = "seats"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(40))
    has_power: Mapped[bool] = mapped_column(Boolean, default=False)
    accessible: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    room: Mapped[Room] = relationship(back_populates="seats")


class Reservation(Base):
    """A student's personal seat reservation."""

    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint(
            "duration_hours >= 1 AND duration_hours <= 24", name="ck_reservation_duration"
        ),
        UniqueConstraint("user_id", "idempotency_key", name="uq_reservation_idempotency"),
        Index("ix_reservation_seat_window", "seat_id", "start_at", "end_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    seat_id: Mapped[str] = mapped_column(ForeignKey("seats.id"), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    duration_hours: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="confirmed", index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    checkin_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    violation: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    user: Mapped[User] = relationship()
    seat: Mapped[Seat] = relationship()


class ClassroomApplication(Base):
    """A multi-step request to reserve an entire room."""

    __tablename__ = "classroom_applications"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id"), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    purpose: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    current_stage: Mapped[int] = mapped_column(Integer, default=1)
    timeout_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    applicant: Mapped[User] = relationship()
    room: Mapped[Room] = relationship()
    approvals: Mapped[list[ApprovalRecord]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )


class ApprovalRecord(Base):
    """A decision made at one classroom-application approval stage."""

    __tablename__ = "approval_records"
    __table_args__ = (UniqueConstraint("application_id", "stage", name="uq_approval_stage"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[str] = mapped_column(
        ForeignKey("classroom_applications.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[int] = mapped_column(Integer)
    approver_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(20))
    comment: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    application: Mapped[ClassroomApplication] = relationship(back_populates="approvals")
    approver: Mapped[User] = relationship()


class ResourceHold(Base):
    """A traceable time block created by M1, M2, or M3."""

    __tablename__ = "resource_holds"
    __table_args__ = (
        Index("ix_hold_window", "resource_type", "resource_id", "start_at", "end_at", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resource_type: Mapped[str] = mapped_column(String(16), index=True)
    resource_id: Mapped[str] = mapped_column(String(40), index=True)
    module: Mapped[str] = mapped_column(String(4), index=True)
    source_id: Mapped[str] = mapped_column(String(40), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime)
    end_at: Mapped[datetime] = mapped_column(DateTime)
    reason: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)


class WorkOrder(Base):
    """A reported fault and its repair/acceptance lifecycle."""

    __tablename__ = "work_orders"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    seat_id: Mapped[str | None] = mapped_column(ForeignKey("seats.id"), nullable=True, index=True)
    room_id: Mapped[str | None] = mapped_column(ForeignKey("rooms.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(16), default="normal")
    status: Mapped[str] = mapped_column(String(20), default="submitted", index=True)
    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    escalation_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating_comment: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_local, onupdate=now_local)
    reporter: Mapped[User] = relationship(foreign_keys=[reporter_id])
    assignee: Mapped[User | None] = relationship(foreign_keys=[assignee_id])
    seat: Mapped[Seat | None] = relationship()
    room: Mapped[Room | None] = relationship()


class Notification(Base):
    """An in-app notification with a unique key for safe retries."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(160))
    message: Mapped[str] = mapped_column(String(1000))
    related_id: Mapped[str] = mapped_column(String(40), default="")
    unique_key: Mapped[str] = mapped_column(String(160), unique=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    email_queued_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    email_last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    email_attempts: Mapped[int] = mapped_column(Integer, default=0)
    email_last_error: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    user: Mapped[User] = relationship()


class AuditEvent(Base):
    """A business-action record for accountability and troubleshooting."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    entity_id: Mapped[str] = mapped_column(String(40), index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local, index=True)
    actor: Mapped[User | None] = relationship()


class PendingAgentAction(Base):
    """A previewed agent write action awaiting explicit user confirmation."""

    __tablename__ = "pending_agent_actions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
