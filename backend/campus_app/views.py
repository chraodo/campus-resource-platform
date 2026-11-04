"""JSON projections that keep internal model fields out of public responses."""

from __future__ import annotations

from campus_app.models import (
    ClassroomApplication,
    Notification,
    Reservation,
    Room,
    Seat,
    User,
    WorkOrder,
)


def user_view(user: User) -> dict:
    """Return the safe public account fields."""
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "role": user.role,
        "department_id": user.department_id,
    }


def room_view(room: Room) -> dict:
    """Return public room details and opening hours."""
    return {
        "id": room.id,
        "name": room.name,
        "area": room.area,
        "room_type": room.room_type,
        "access_scope": room.access_scope,
        "department_id": room.department_id,
        "capacity": room.capacity,
        "open_time": room.open_time,
        "close_time": room.close_time,
        "active": room.active,
    }


def reservation_view(reservation: Reservation) -> dict:
    """Return a reservation projection without exposing room screen credentials."""
    view = {
        "id": reservation.id,
        "user_id": reservation.user_id,
        "seat_id": reservation.seat_id,
        "seat_label": reservation.seat.label if reservation.seat else reservation.seat_id,
        "room_name": reservation.seat.room.name
        if reservation.seat and reservation.seat.room
        else "",
        "start_at": reservation.start_at.isoformat(timespec="minutes"),
        "end_at": reservation.end_at.isoformat(timespec="minutes"),
        "duration_hours": reservation.duration_hours,
        "status": reservation.status,
        "checked_in_at": reservation.checked_in_at.isoformat(timespec="minutes")
        if reservation.checked_in_at
        else None,
        "violation": reservation.violation,
        "created_at": reservation.created_at.isoformat(timespec="minutes"),
    }
    return view


def seat_view(seat: Seat, blocked_by: dict | None) -> dict:
    """Return a seat and the module/source that currently blocks it."""
    room = seat.room
    return {
        "id": seat.id,
        "label": seat.label,
        "room_id": seat.room_id,
        "room_name": room.name if room else "",
        "area": room.area if room else "",
        "has_power": seat.has_power,
        "accessible": seat.accessible,
        "available": blocked_by is None,
        "blocked_by": blocked_by,
    }


def application_view(application: ClassroomApplication) -> dict:
    """Return an application, its current approval stage, and decision history."""
    return {
        "id": application.id,
        "applicant_id": application.applicant_id,
        "applicant_name": application.applicant.display_name if application.applicant else "",
        "room_id": application.room_id,
        "room_name": application.room.name if application.room else "",
        "start_at": application.start_at.isoformat(timespec="minutes"),
        "end_at": application.end_at.isoformat(timespec="minutes"),
        "purpose": application.purpose,
        "status": application.status,
        "current_stage": application.current_stage,
        "timeout_at": application.timeout_at.isoformat(timespec="minutes"),
        "approvals": [
            {
                "stage": decision.stage,
                "decision": decision.decision,
                "comment": decision.comment,
                "approver_name": decision.approver.display_name if decision.approver else "",
                "created_at": decision.created_at.isoformat(timespec="minutes"),
            }
            for decision in sorted(application.approvals, key=lambda item: item.stage)
        ],
    }


def work_order_view(work_order: WorkOrder) -> dict:
    """Return a work order and its target/assignment state."""
    return {
        "id": work_order.id,
        "reporter_id": work_order.reporter_id,
        "reporter_name": work_order.reporter.display_name if work_order.reporter else "",
        "seat_id": work_order.seat_id,
        "room_id": work_order.room_id,
        "target_label": work_order.seat.label
        if work_order.seat
        else (work_order.room.name if work_order.room else ""),
        "title": work_order.title,
        "description": work_order.description,
        "severity": work_order.severity,
        "status": work_order.status,
        "assignee_id": work_order.assignee_id,
        "assignee_name": work_order.assignee.display_name if work_order.assignee else "",
        "due_at": work_order.due_at.isoformat(timespec="minutes"),
        "escalation_sent": work_order.escalation_sent,
        "rating": work_order.rating,
        "rating_comment": work_order.rating_comment,
        "created_at": work_order.created_at.isoformat(timespec="minutes"),
    }


def notification_view(notification: Notification) -> dict:
    """Return one in-app notification."""
    return {
        "id": notification.id,
        "kind": notification.kind,
        "title": notification.title,
        "message": notification.message,
        "related_id": notification.related_id,
        "read": notification.read_at is not None,
        "created_at": notification.created_at.isoformat(timespec="minutes"),
    }
