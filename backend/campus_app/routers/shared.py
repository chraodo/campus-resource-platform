"""D shared identity, resource directory, notifications, and administration routes."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from campus_app.clock import now_local
from campus_app.dependencies import CurrentUser, DbSession, ScreenCredential
from campus_app.domain import write_audit
from campus_app.models import (
    ApiCredential,
    AppSetting,
    AuditEvent,
    Notification,
    Reservation,
    RoleDefinition,
    Room,
    Seat,
    User,
)
from campus_app.rbac import (
    PERMISSION_CATALOG,
    ROLE_DEFAULTS,
    has_permission,
    role_is_active,
    role_name,
)
from campus_app.schemas import (
    ActiveInput,
    AppSettingsInput,
    RoleCreateInput,
    RoleInput,
    RoleUpdateInput,
    RoomHoursInput,
    RoomInput,
    SeatInput,
    UserCreateInput,
)
from campus_app.security import daily_room_code, hash_password, new_token, token_hash
from campus_app.settings import setting_int
from campus_app.views import notification_view, reservation_view, room_view, user_view

router = APIRouter(prefix="/api/v1", tags=["D · 公共服务"])


@router.get("/public-settings")
def public_settings(db: DbSession, _user: CurrentUser) -> dict:
    """Return harmless settings needed to render client-side limits."""
    return {"max_booking_hours": setting_int(db, "max_booking_hours")}


@router.get("/roles")
def list_role_options(db: DbSession, _user: CurrentUser) -> list[dict]:
    """List active role labels for account forms and role-based navigation."""
    roles = {
        code: {"code": code, "name": name} for code, (name, _permissions) in ROLE_DEFAULTS.items()
    }
    for row in db.scalars(select(RoleDefinition).where(RoleDefinition.active.is_(True))):
        roles[row.code] = {"code": row.code, "name": row.name}
    return sorted(roles.values(), key=lambda role: role["code"])


@router.get("/repairers")
def list_repairers(db: DbSession, user: CurrentUser) -> list[dict]:
    """List active repair assignees to a user allowed to dispatch work."""
    if not has_permission(db, user, "work_order:assign"):
        raise HTTPException(403, "当前账号没有派工权限")
    return [
        user_view(person)
        for person in db.scalars(select(User).where(User.active.is_(True))).all()
        if has_permission(db, person, "work_order:repair")
    ]


@router.get("/admin/roles")
def admin_roles(db: DbSession, user: CurrentUser) -> list[dict]:
    """List role definitions and their permission codes for administrators."""
    if not has_permission(db, user, "user:manage"):
        raise HTTPException(403, "只有账号管理员可以查看角色权限")
    roles = {
        code: {
            "code": code,
            "name": name,
            "permissions": permissions,
            "active": True,
            "system": True,
        }
        for code, (name, permissions) in ROLE_DEFAULTS.items()
    }
    for row in db.scalars(select(RoleDefinition)):
        roles[row.code] = {
            "code": row.code,
            "name": row.name,
            "permissions": list(row.permissions),
            "active": row.active,
            "system": row.system,
        }
    return sorted(roles.values(), key=lambda role: role["code"])


@router.get("/admin/permission-catalog")
def permission_catalog(db: DbSession, user: CurrentUser) -> list[dict]:
    """Return the permission choices available to custom role definitions."""
    if not has_permission(db, user, "user:manage"):
        raise HTTPException(403, "只有账号管理员可以查看权限清单")
    return [{"code": code, "label": label} for code, label in PERMISSION_CATALOG.items()]


@router.post("/admin/roles", status_code=201)
def create_role(data: RoleCreateInput, db: DbSession, user: CurrentUser) -> dict:
    """Create a custom role for this platform's permission codes."""
    if not has_permission(db, user, "user:manage"):
        raise HTTPException(403, "只有账号管理员可以新增角色")
    if data.code in ROLE_DEFAULTS or db.get(RoleDefinition, data.code):
        raise HTTPException(409, "角色编号已存在")
    if not set(data.permissions) <= set(PERMISSION_CATALOG):
        raise HTTPException(422, "角色包含权限清单之外的权限")
    row = RoleDefinition(
        code=data.code,
        name=data.name,
        permissions=sorted(set(data.permissions)),
        active=True,
        system=False,
    )
    db.add(row)
    write_audit(db, user.id, "role.created", "role", row.code, {"permissions": row.permissions})
    db.commit()
    return {
        "code": row.code,
        "name": row.name,
        "permissions": row.permissions,
        "active": row.active,
    }


@router.put("/admin/roles/{role_code}")
def update_role(role_code: str, data: RoleUpdateInput, db: DbSession, user: CurrentUser) -> dict:
    """Update a role label/permissions or disable a role from future logins."""
    if not has_permission(db, user, "user:manage"):
        raise HTTPException(403, "只有账号管理员可以修改角色权限")
    if role_code == "admin":
        raise HTTPException(409, "系统管理员角色不可停用或移除全部权限")
    row = db.get(RoleDefinition, role_code)
    if row is None:
        default = ROLE_DEFAULTS.get(role_code)
        if default is None:
            raise HTTPException(404, "没有找到该角色")
        row = RoleDefinition(code=role_code, name=default[0], permissions=default[1], system=True)
        db.add(row)
    if not set(data.permissions) <= set(PERMISSION_CATALOG):
        raise HTTPException(422, "角色包含权限清单之外的权限")
    row.name = data.name
    row.permissions = sorted(set(data.permissions))
    row.active = data.active
    write_audit(
        db,
        user.id,
        "role.updated",
        "role",
        row.code,
        {"permissions": row.permissions, "active": row.active},
    )
    db.commit()
    return {
        "code": row.code,
        "name": row.name,
        "permissions": row.permissions,
        "active": row.active,
    }


@router.get("/rooms")
def list_rooms(
    db: DbSession,
    _user: CurrentUser,
    room_type: str | None = Query(default=None, pattern="^(study|classroom)$"),
) -> list[dict]:
    """List active rooms used by booking and repair modules."""
    statement = select(Room).where(Room.active.is_(True)).order_by(Room.area, Room.name)
    if not has_permission(db, _user, "resource:manage"):
        accessible_rooms = Room.access_scope == "campus"
        if _user.department_id:
            accessible_rooms = or_(accessible_rooms, Room.department_id == _user.department_id)
        statement = statement.where(accessible_rooms)
    if room_type:
        statement = statement.where(Room.room_type == room_type)
    return [room_view(room) for room in db.scalars(statement).all()]


@router.post("/admin/rooms/{room_id}/screen-token")
def create_room_screen_token(room_id: str, db: DbSession, user: CurrentUser) -> dict:
    """Issue a room-only token for the physical screen; show it only once."""
    if not has_permission(db, user, "resource:manage"):
        raise HTTPException(403, "只有空间管理员可以设置屏幕令牌")
    room = db.get(Room, room_id)
    if room is None:
        raise HTTPException(404, "没有找到该空间")
    previous = db.scalars(
        select(ApiCredential).where(
            ApiCredential.kind == "screen", ApiCredential.resource_id == room_id
        )
    ).all()
    for credential in previous:
        db.delete(credential)
    secret = new_token()
    expires_at = now_local() + timedelta(days=365)
    db.add(
        ApiCredential(
            token_hash=token_hash(secret),
            user_id=user.id,
            kind="screen",
            label=room.name,
            resource_id=room.id,
            expires_at=expires_at,
        )
    )
    write_audit(db, user.id, "room.screen_token_issued", "room", room.id)
    db.commit()
    return {
        "room_id": room.id,
        "room_name": room.name,
        "screen_token": secret,
        "expires_at": expires_at.isoformat(timespec="minutes"),
        "shown_once": True,
    }


@router.get("/rooms/{room_id}/screen-code")
def room_screen_code(
    room_id: str, db: DbSession, credential: ScreenCredential, response: Response
) -> dict:
    """Return today's HMAC-backed room code to the matching screen only."""
    if credential.resource_id != room_id:
        raise HTTPException(403, "屏幕令牌不属于该空间")
    room = db.get(Room, room_id)
    if room is None or not room.active:
        raise HTTPException(404, "没有找到开放中的空间")
    response.headers["Cache-Control"] = "no-store"
    now = now_local()
    tomorrow = now.date() + timedelta(days=1)
    return {
        "room_id": room.id,
        "room_name": room.name,
        "date": now.date().isoformat(),
        "code": daily_room_code(room.id, now.date()),
        "expires_at": datetime.combine(tomorrow, time.min).isoformat(timespec="minutes"),
    }


@router.get("/notifications")
def list_notifications(db: DbSession, user: CurrentUser) -> list[dict]:
    """List the current user's newest in-app notifications."""
    if not has_permission(db, user, "notification:read"):
        raise HTTPException(403, "当前账号没有查看通知的权限")
    rows = db.scalars(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
        .limit(100)
    ).all()
    return [notification_view(row) for row in rows]


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, db: DbSession, user: CurrentUser) -> dict:
    """Mark one owned notification as read."""
    if not has_permission(db, user, "notification:read"):
        raise HTTPException(403, "当前账号没有查看通知的权限")
    row = db.scalar(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user.id
        )
    )
    if row is None:
        raise HTTPException(404, "没有找到该通知")
    row.read_at = now_local()
    db.commit()
    return notification_view(row)


@router.get("/admin/overview")
def admin_overview(db: DbSession, user: CurrentUser) -> dict:
    """Return simple operational counts for administrators."""
    if not has_permission(db, user, "metrics:read"):
        raise HTTPException(403, "只有管理员可以查看系统总览")
    counts = {
        "users": db.scalar(select(func.count()).select_from(User)) or 0,
        "rooms": db.scalar(select(func.count()).select_from(Room)) or 0,
        "seats": db.scalar(select(func.count()).select_from(Seat)) or 0,
        "pending_applications": 0,
        "open_work_orders": 0,
        "active_reservations": 0,
    }
    from campus_app.models import ClassroomApplication, Reservation, WorkOrder

    counts["pending_applications"] = (
        db.scalar(
            select(func.count())
            .select_from(ClassroomApplication)
            .where(ClassroomApplication.status == "pending")
        )
        or 0
    )
    counts["open_work_orders"] = (
        db.scalar(
            select(func.count())
            .select_from(WorkOrder)
            .where(WorkOrder.status.in_(("submitted", "assigned", "in_repair", "resolved")))
        )
        or 0
    )
    counts["active_reservations"] = (
        db.scalar(
            select(func.count())
            .select_from(Reservation)
            .where(Reservation.status.in_(("confirmed", "checked_in")))
        )
        or 0
    )
    return counts


@router.get("/admin/settings")
def admin_settings(db: DbSession, user: CurrentUser) -> dict:
    """Read system settings that affect booking rules."""
    if not has_permission(db, user, "system:config"):
        raise HTTPException(403, "只有系统管理员可以查看系统参数")
    return {"max_booking_hours": setting_int(db, "max_booking_hours")}


@router.put("/admin/settings")
def update_admin_settings(data: AppSettingsInput, db: DbSession, user: CurrentUser) -> dict:
    """Set the maximum duration of one seat reservation and record the change."""
    if not has_permission(db, user, "system:config"):
        raise HTTPException(403, "只有系统管理员可以修改系统参数")
    row = db.get(AppSetting, "max_booking_hours")
    previous = setting_int(db, "max_booking_hours")
    if row is None:
        row = AppSetting(key="max_booking_hours", value=data.max_booking_hours)
        db.add(row)
    else:
        row.value = data.max_booking_hours
    row.updated_by = user.id
    write_audit(
        db,
        user.id,
        "setting.updated",
        "setting",
        "max_booking_hours",
        {"from": previous, "to": data.max_booking_hours},
    )
    db.commit()
    return {"max_booking_hours": row.value}


@router.get("/admin/reservations")
def admin_reservations(
    db: DbSession,
    user: CurrentUser,
    booking_date: date | None = Query(default=None, alias="date"),
) -> list[dict]:
    """List booking details for an operations role allowed to inspect usage."""
    if not has_permission(db, user, "reservation:read_all"):
        raise HTTPException(403, "当前账号没有查看全校预约情况的权限")
    statement = (
        select(Reservation)
        .options(joinedload(Reservation.user), joinedload(Reservation.seat).joinedload(Seat.room))
        .order_by(Reservation.start_at.desc())
        .limit(200)
    )
    if booking_date:
        start_at = datetime.combine(booking_date, datetime.min.time())
        end_at = start_at + timedelta(days=1)
        statement = statement.where(
            Reservation.start_at >= start_at,
            Reservation.start_at < end_at,
        )
    return [
        reservation_view(row) | {"student_name": row.user.display_name if row.user else ""}
        for row in db.scalars(statement).unique().all()
    ]


@router.get("/admin/users")
def admin_users(db: DbSession, user: CurrentUser) -> list[dict]:
    """List accounts without password hashes or bearer tokens."""
    if not has_permission(db, user, "user:manage"):
        raise HTTPException(403, "只有管理员可以管理账号")
    return [
        user_view(row) | {"role_name": role_name(db, row.role), "active": row.active}
        for row in db.scalars(select(User).order_by(User.id)).all()
    ]


@router.post("/admin/users", status_code=201)
def create_user(data: UserCreateInput, db: DbSession, user: CurrentUser) -> dict:
    """Create a named account with one of the supported roles."""
    if not has_permission(db, user, "user:manage"):
        raise HTTPException(403, "只有管理员可以创建账号")
    if not role_is_active(db, data.role):
        raise HTTPException(422, "请选择一个已启用的角色")
    if db.scalar(select(User.id).where(User.username == data.username)):
        raise HTTPException(409, "用户名已存在")
    row = User(
        username=data.username,
        display_name=data.display_name,
        password_hash=hash_password(data.password),
        email=data.email,
        role=data.role,
        department_id=data.department_id,
    )
    db.add(row)
    db.flush()
    write_audit(db, user.id, "user.created", "user", str(row.id), {"role": row.role})
    db.commit()
    return user_view(row) | {"role_name": role_name(db, row.role), "active": row.active}


@router.patch("/admin/users/{user_id}/role")
def change_user_role(user_id: int, data: RoleInput, db: DbSession, user: CurrentUser) -> dict:
    """Change an account's role while keeping at least one active administrator."""
    if not has_permission(db, user, "user:manage"):
        raise HTTPException(403, "只有管理员可以修改角色")
    row = db.get(User, user_id)
    if row is None:
        raise HTTPException(404, "没有找到该账号")
    if not role_is_active(db, data.role):
        raise HTTPException(422, "请选择一个已启用的角色")
    if row.role == "admin" and data.role != "admin":
        active_admins = (
            db.scalar(
                select(func.count())
                .select_from(User)
                .where(User.role == "admin", User.active.is_(True))
            )
            or 0
        )
        if active_admins <= 1:
            raise HTTPException(409, "系统至少保留一个管理员")
    old_role = row.role
    row.role = data.role
    write_audit(
        db, user.id, "user.role_changed", "user", str(row.id), {"from": old_role, "to": row.role}
    )
    db.commit()
    return user_view(row) | {"role_name": role_name(db, row.role), "active": row.active}


@router.patch("/admin/users/{user_id}/active")
def change_user_active(user_id: int, data: ActiveInput, db: DbSession, user: CurrentUser) -> dict:
    """Enable or disable an account without deleting its audit history."""
    if not has_permission(db, user, "user:manage"):
        raise HTTPException(403, "只有管理员可以停用账号")
    row = db.get(User, user_id)
    if row is None:
        raise HTTPException(404, "没有找到该账号")
    if row.id == user.id and not data.active:
        raise HTTPException(409, "不能停用当前登录的管理员账号")
    if row.role == "admin" and not data.active:
        active_admins = (
            db.scalar(
                select(func.count())
                .select_from(User)
                .where(User.role == "admin", User.active.is_(True))
            )
            or 0
        )
        if active_admins <= 1:
            raise HTTPException(409, "系统至少保留一个启用状态的管理员")
    row.active = data.active
    write_audit(db, user.id, "user.active_changed", "user", str(row.id), {"active": row.active})
    db.commit()
    return user_view(row) | {"role_name": role_name(db, row.role), "active": row.active}


@router.post("/admin/rooms", status_code=201)
def create_room(data: RoomInput, db: DbSession, user: CurrentUser) -> dict:
    """Add a room to the shared resource directory."""
    if not has_permission(db, user, "resource:manage"):
        raise HTTPException(403, "只有管理员可以新增空间")
    if db.get(Room, data.id):
        raise HTTPException(409, "空间编号已存在")
    row = Room(**data.model_dump())
    db.add(row)
    write_audit(db, user.id, "room.created", "room", row.id)
    db.commit()
    return room_view(row)


@router.patch("/admin/rooms/{room_id}/hours")
def change_room_hours(room_id: str, data: RoomHoursInput, db: DbSession, user: CurrentUser) -> dict:
    if not has_permission(db, user, "resource:manage"):
        raise HTTPException(403, "没有空间管理权限")
    room = db.get(Room, room_id, with_for_update=True)
    if room is None:
        raise HTTPException(404, "空间不存在")
    previous = {"open_time": room.open_time, "close_time": room.close_time}
    room.open_time, room.close_time = data.open_time, data.close_time
    write_audit(
        db,
        user.id,
        "room.hours_changed",
        "room",
        room_id,
        {"from": previous, "to": data.model_dump()},
    )
    db.commit()
    return room_view(room)


@router.post("/admin/seats", status_code=201)
def create_seat(data: SeatInput, db: DbSession, user: CurrentUser) -> dict:
    """Add a seat to an existing room."""
    if not has_permission(db, user, "resource:manage"):
        raise HTTPException(403, "只有管理员可以新增座位")
    room = db.get(Room, data.room_id)
    if room is None:
        raise HTTPException(404, "没有找到所属空间")
    if db.get(Seat, data.id):
        raise HTTPException(409, "座位编号已存在")
    row = Seat(**data.model_dump())
    db.add(row)
    write_audit(db, user.id, "seat.created", "seat", row.id, {"room_id": room.id})
    db.commit()
    return {
        "id": row.id,
        "room_id": row.room_id,
        "label": row.label,
        "has_power": row.has_power,
        "accessible": row.accessible,
        "active": row.active,
    }


@router.patch("/admin/rooms/{room_id}/active")
def change_room_active(room_id: str, data: ActiveInput, db: DbSession, user: CurrentUser) -> dict:
    """Open or close a room in the resource directory."""
    if not has_permission(db, user, "resource:manage"):
        raise HTTPException(403, "只有管理员可以修改空间状态")
    row = db.get(Room, room_id)
    if row is None:
        raise HTTPException(404, "没有找到该空间")
    row.active = data.active
    write_audit(db, user.id, "room.active_changed", "room", row.id, {"active": row.active})
    db.commit()
    return room_view(row)


@router.patch("/admin/seats/{seat_id}/active")
def change_seat_active(seat_id: str, data: ActiveInput, db: DbSession, user: CurrentUser) -> dict:
    """Open or close a seat without erasing its booking history."""
    if not has_permission(db, user, "resource:manage"):
        raise HTTPException(403, "只有管理员可以修改座位状态")
    row = db.scalar(select(Seat).options(joinedload(Seat.room)).where(Seat.id == seat_id))
    if row is None:
        raise HTTPException(404, "没有找到该座位")
    row.active = data.active
    write_audit(db, user.id, "seat.active_changed", "seat", row.id, {"active": row.active})
    db.commit()
    return {
        "id": row.id,
        "room_id": row.room_id,
        "room_name": row.room.name if row.room else "",
        "label": row.label,
        "active": row.active,
    }


@router.get("/admin/audit")
def admin_audit(
    db: DbSession,
    user: CurrentUser,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    """Read recent business audit events; access is administrator-only."""
    if not has_permission(db, user, "audit:read"):
        raise HTTPException(403, "只有管理员可以查看操作审计")
    rows = db.scalars(
        select(AuditEvent)
        .options(joinedload(AuditEvent.actor))
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": row.id,
            "actor": row.actor.display_name if row.actor else "系统任务",
            "action": row.action,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "details": row.details,
            "created_at": row.created_at.isoformat(timespec="minutes"),
        }
        for row in rows
    ]
