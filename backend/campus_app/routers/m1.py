"""M1 seat search, booking, check-in, history, and cancellation routes."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Header, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import joinedload

from campus_app.clock import now_local
from campus_app.dependencies import CurrentUser, DbSession
from campus_app.domain import cancel_reservation as cancel_owned_reservation
from campus_app.domain import (
    create_reservation,
    expire_no_show,
    find_seat_conflict,
    room_hours_allow,
    write_audit,
)
from campus_app.models import Reservation, Room, Seat
from campus_app.rbac import has_permission
from campus_app.schemas import CheckInInput, ReservationInput
from campus_app.security import verify_daily_room_code
from campus_app.settings import setting_int
from campus_app.views import reservation_view, seat_view

router = APIRouter(prefix="/api/v1", tags=["M1 · 自习座位预约"])


@router.get("/seats")
def search_seats(
    db: DbSession,
    user: CurrentUser,
    booking_date: date = Query(alias="date"),
    start_time: time = Query(default=time(hour=10)),
    duration_hours: int = Query(default=1, ge=1, le=24),
    area: str | None = None,
    has_power: bool | None = None,
    accessible: bool | None = None,
) -> dict:
    """Find seats with current M1/M2/M3 occupancy sources for a time range."""
    start_at = datetime.combine(booking_date, start_time)
    if start_at.minute or start_at.second or start_at.microsecond:
        raise HTTPException(422, "请选择整点开始时间")
    maximum_hours = setting_int(db, "max_booking_hours")
    if duration_hours > maximum_hours:
        raise HTTPException(422, f"当前系统每次最多可预约 {maximum_hours} 小时")
    end_at = start_at + timedelta(hours=duration_hours)
    if end_at.date() != start_at.date():
        raise HTTPException(422, "预约暂不支持跨天")
    statement = (
        select(Seat)
        .join(Room)
        .options(joinedload(Seat.room))
        .where(Seat.active.is_(True), Room.active.is_(True))
        .order_by(Room.area, Room.name, Seat.label)
    )
    if area:
        statement = statement.where(Room.area == area)
    if not has_permission(db, user, "resource:manage"):
        accessible_rooms = Room.access_scope == "campus"
        if user.department_id:
            accessible_rooms = or_(accessible_rooms, Room.department_id == user.department_id)
        statement = statement.where(accessible_rooms)
    if has_power is not None:
        statement = statement.where(Seat.has_power.is_(has_power))
    if accessible is not None:
        statement = statement.where(Seat.accessible.is_(accessible))
    seats = db.scalars(statement).unique().all()
    result = []
    for seat in seats:
        if room_hours_allow(seat.room, start_at, end_at):
            conflict = find_seat_conflict(db, seat, start_at, end_at)
        else:
            conflict = {
                "module": "D",
                "source_id": seat.room_id,
                "reason": f"该空间开放时间为 {seat.room.open_time}–{seat.room.close_time}",
            }
        result.append(seat_view(seat, conflict))
    return {
        "date": booking_date.isoformat(),
        "start_at": start_at.isoformat(timespec="minutes"),
        "end_at": end_at.isoformat(timespec="minutes"),
        "duration_hours": duration_hours,
        "available_count": sum(item["available"] for item in result),
        "seats": result,
    }


@router.post("/reservations", status_code=201)
def book_seat(
    data: ReservationInput,
    db: DbSession,
    user: CurrentUser,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=120),
) -> dict:
    """Create a reservation; the daily code is displayed at the room."""
    row = create_reservation(db, user, data, idempotency_key)
    db.commit()
    db.refresh(row)
    return reservation_view(row)


@router.get("/reservations")
def list_reservations(
    db: DbSession,
    user: CurrentUser,
    status_filter: str | None = Query(default=None, alias="status"),
    user_id: int | None = None,
) -> list[dict]:
    """List a student's own reservation history or an administrator's view."""
    can_read_all = has_permission(db, user, "reservation:read_all")
    if not can_read_all and not has_permission(db, user, "reservation:read_own"):
        raise HTTPException(403, "当前账号没有查看预约记录的权限")
    if user_id is not None and user_id != user.id:
        if not can_read_all:
            raise HTTPException(403, "只有管理员可以查看其他账号的预约")
    statement = (
        select(Reservation)
        .options(joinedload(Reservation.seat).joinedload(Seat.room))
        .order_by(Reservation.start_at.desc())
    )
    if user_id is not None:
        statement = statement.where(Reservation.user_id == user_id)
    elif not can_read_all:
        statement = statement.where(Reservation.user_id == user.id)
    if status_filter:
        statement = statement.where(Reservation.status == status_filter)
    return [reservation_view(row) for row in db.scalars(statement).unique().all()]


@router.post("/reservations/{reservation_id}/check-in")
def check_in(
    reservation_id: str,
    data: CheckInInput,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    """Check in the reservation owner with today's code from the room screen."""
    row = db.get(Reservation, reservation_id, with_for_update=True)
    if row is None or (
        row.user_id != user.id and not has_permission(db, user, "reservation:manage_all")
    ):
        raise HTTPException(404, "没有找到本人预约")
    if row.status != "confirmed":
        raise HTTPException(409, "预约当前状态不能签到")
    if row.user_id == user.id and not has_permission(db, user, "reservation:checkin"):
        raise HTTPException(403, "当前账号没有签到预约的权限")
    now = now_local()
    room_id = row.seat.room_id if row.seat else ""
    if not verify_daily_room_code(data.code, room_id, now.date()):
        raise HTTPException(403, "签到码不正确，请核对预约房间屏幕上的今日编码")
    if now < row.start_at:
        raise HTTPException(409, f"预约开始时间为 {row.start_at:%H:%M}，请到时再签到")
    if expire_no_show(db, row, now):
        db.commit()
        raise HTTPException(409, "签到时间已过，预约已按违约规则释放")
    row.status = "checked_in"
    row.checked_in_at = now
    write_audit(db, user.id, "reservation.checked_in", "reservation", row.id)
    db.commit()
    db.refresh(row)
    return reservation_view(row)


@router.get("/reservations/{reservation_id}/checkin-code")
def get_checkin_code(reservation_id: str, db: DbSession, user: CurrentUser) -> dict:
    """Return screen instructions without revealing the screen's daily code."""
    row = db.get(Reservation, reservation_id)
    if row is None or (
        row.user_id != user.id and not has_permission(db, user, "reservation:manage_all")
    ):
        raise HTTPException(404, "没有找到本人预约")
    if row.status != "confirmed":
        raise HTTPException(409, "当前预约没有有效签到码")
    return {
        "reservation_id": row.id,
        "room_id": row.seat.room_id if row.seat else "",
        "room_name": row.seat.room.name if row.seat and row.seat.room else "",
        "message": "签到码只显示在预约所在自习室的屏幕上。",
    }


@router.post("/reservations/{reservation_id}/cancel")
def cancel_reservation(
    reservation_id: str,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    """Cancel an upcoming reservation owned by the current student."""
    row = cancel_owned_reservation(db, user, reservation_id)
    db.commit()
    db.refresh(row)
    return reservation_view(row)
