"""Business operations shared by the HTTP routes, worker, and DSH tools."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from campus_app.clock import now_local
from campus_app.config import (
    APPROVAL_TIMEOUT_HOURS,
    CHECKIN_LATE_MINUTES,
    SMTP_FROM,
    SMTP_HOST,
    WORK_ORDER_SLA_HOURS,
)
from campus_app.models import (
    ApiCredential,
    ApprovalRecord,
    AuditEvent,
    ClassroomApplication,
    Notification,
    PendingAgentAction,
    Reservation,
    ResourceHold,
    Room,
    Seat,
    User,
    WorkOrder,
)
from campus_app.rbac import can_access_room, has_permission
from campus_app.schemas import (
    AgentActionPreviewInput,
    ClassroomApplicationInput,
    ReservationInput,
    WorkOrderInput,
)
from campus_app.security import new_token, token_hash
from campus_app.settings import setting_int

ACTIVE_RESERVATION_STATES = ("confirmed", "checked_in")


def new_id(prefix: str) -> str:
    """Return a readable prefix followed by a unique opaque identifier."""
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def write_audit(
    db: Session,
    actor_id: int | None,
    action: str,
    entity_type: str,
    entity_id: str,
    details: dict | None = None,
) -> None:
    """Add one audit event to the current database transaction."""
    db.add(
        AuditEvent(
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
        )
    )


def notify(
    db: Session,
    user_id: int,
    kind: str,
    title: str,
    message: str,
    related_id: str,
    unique_key: str,
) -> None:
    """Add an in-app notification only once, even when a worker retries."""
    if db.scalar(select(Notification.id).where(Notification.unique_key == unique_key)):
        return
    recipient = db.scalar(select(User.email).where(User.id == user_id))
    db.add(
        Notification(
            user_id=user_id,
            kind=kind,
            title=title,
            message=message,
            related_id=related_id,
            unique_key=unique_key,
            email_queued_at=now_local() if recipient and SMTP_HOST and SMTP_FROM else None,
        )
    )


def validate_range(start_at: datetime, end_at: datetime | None = None) -> datetime:
    """Reject aware, past, cross-day, or non-hour booking times."""
    if start_at.tzinfo is not None or (end_at is not None and end_at.tzinfo is not None):
        raise HTTPException(422, "请使用学校本地时间，不要带时区偏移")
    if start_at <= now_local():
        raise HTTPException(422, "预约开始时间必须晚于当前时间")
    if start_at.minute != 0 or start_at.second != 0 or start_at.microsecond != 0:
        raise HTTPException(422, "预约开始时间需要选择整点")
    if end_at is not None:
        if end_at <= start_at:
            raise HTTPException(422, "结束时间必须晚于开始时间")
        if end_at.date() != start_at.date():
            raise HTTPException(422, "预约暂不支持跨天")
        if end_at.minute != 0 or end_at.second != 0 or end_at.microsecond != 0:
            raise HTTPException(422, "结束时间需要选择整点")
    return start_at


def room_hours_allow(room: Room, start_at: datetime, end_at: datetime) -> bool:
    """Return whether a time range fits within a room's daily opening hours."""
    return (
        start_at.strftime("%H:%M") >= room.open_time and end_at.strftime("%H:%M") <= room.close_time
    )


def find_seat_conflict(
    db: Session,
    seat: Seat,
    start_at: datetime,
    end_at: datetime,
    exclude_reservation_id: str | None = None,
) -> dict | None:
    """Return the first active booking/hold that overlaps a seat time range."""
    reservation_query = select(Reservation).where(
        Reservation.seat_id == seat.id,
        Reservation.status.in_(ACTIVE_RESERVATION_STATES),
        Reservation.start_at < end_at,
        Reservation.end_at > start_at,
    )
    if exclude_reservation_id:
        reservation_query = reservation_query.where(Reservation.id != exclude_reservation_id)
    reservation = db.scalar(reservation_query.limit(1))
    if reservation is not None:
        return {
            "module": "M1",
            "source_id": reservation.id,
            "reason": "座位已被个人预约",
        }

    hold = db.scalar(
        select(ResourceHold)
        .where(
            ResourceHold.active.is_(True),
            ResourceHold.start_at < end_at,
            ResourceHold.end_at > start_at,
            ((ResourceHold.resource_type == "seat") & (ResourceHold.resource_id == seat.id))
            | ((ResourceHold.resource_type == "room") & (ResourceHold.resource_id == seat.room_id)),
        )
        .order_by(ResourceHold.id)
        .limit(1)
    )
    if hold is not None:
        return {"module": hold.module, "source_id": hold.source_id, "reason": hold.reason}
    return None


def find_room_conflict(
    db: Session, room: Room, start_at: datetime, end_at: datetime
) -> dict | None:
    """Return the first active booking or hold that uses any part of a room."""
    seat_ids = select(Seat.id).where(Seat.room_id == room.id)
    reservation = db.scalar(
        select(Reservation)
        .where(
            Reservation.seat_id.in_(seat_ids),
            Reservation.status.in_(ACTIVE_RESERVATION_STATES),
            Reservation.start_at < end_at,
            Reservation.end_at > start_at,
        )
        .limit(1)
    )
    if reservation is not None:
        return {"module": "M1", "source_id": reservation.id, "reason": "已有个人座位预约"}
    hold = db.scalar(
        select(ResourceHold)
        .where(
            ResourceHold.active.is_(True),
            ResourceHold.start_at < end_at,
            ResourceHold.end_at > start_at,
            ((ResourceHold.resource_type == "room") & (ResourceHold.resource_id == room.id))
            | ((ResourceHold.resource_type == "seat") & (ResourceHold.resource_id.in_(seat_ids))),
        )
        .limit(1)
    )
    if hold is not None:
        return {"module": hold.module, "source_id": hold.source_id, "reason": hold.reason}
    return None


def create_reservation(
    db: Session,
    user: User,
    data: ReservationInput,
    idempotency_key: str | None = None,
) -> Reservation:
    """Create one conflict-free seat booking; check-in codes belong to room screens."""
    if not has_permission(db, user, "reservation:create"):
        raise HTTPException(403, "只有学生账号可以预约座位")
    if idempotency_key:
        # Serialize retries of the same user-scoped key, even across rooms.
        db.scalar(select(User.id).where(User.id == user.id).with_for_update())
        previous = db.scalar(
            select(Reservation).where(
                Reservation.user_id == user.id,
                Reservation.idempotency_key == idempotency_key,
            )
        )
        if previous is not None:
            same_request = (
                previous.seat_id == data.seat_id
                and previous.start_at == data.start_at
                and previous.duration_hours == data.duration_hours
            )
            if not same_request:
                raise HTTPException(409, "该请求编号已用于其他预约内容")
            return previous
    start_at = data.start_at
    end_at = start_at + timedelta(hours=data.duration_hours)
    validate_range(start_at, end_at)
    maximum_hours = setting_int(db, "max_booking_hours")
    if data.duration_hours > maximum_hours:
        raise HTTPException(422, f"当前系统每次最多可预约 {maximum_hours} 小时")
    seat = db.scalar(select(Seat).where(Seat.id == data.seat_id, Seat.active.is_(True)))
    if seat is None:
        raise HTTPException(404, "没有找到可预约的座位")
    room = db.scalar(select(Room).where(Room.id == seat.room_id).with_for_update())
    if room is None or not room.active:
        raise HTTPException(409, "该座位所在空间当前未开放")
    if not can_access_room(db, user, room):
        raise HTTPException(404, "没有找到可预约的座位")
    if not room_hours_allow(room, start_at, end_at):
        raise HTTPException(422, f"该自习室开放时间为 {room.open_time}–{room.close_time}")
    conflict = find_seat_conflict(db, seat, start_at, end_at)
    if conflict:
        raise HTTPException(409, {"message": conflict["reason"], "source": conflict})
    reservation = Reservation(
        id=new_id("RSV"),
        user_id=user.id,
        seat_id=seat.id,
        start_at=start_at,
        end_at=end_at,
        duration_hours=data.duration_hours,
        status="confirmed",
        idempotency_key=idempotency_key,
    )
    db.add(reservation)
    write_audit(
        db,
        user.id,
        "reservation.created",
        "reservation",
        reservation.id,
        {"seat_id": seat.id, "start_at": start_at.isoformat(), "end_at": end_at.isoformat()},
    )
    notify(
        db,
        user.id,
        "reservation",
        "座位预约成功",
        f"座位 {seat.label}，{start_at:%m-%d %H:%M} 开始。请按时签到。",
        reservation.id,
        f"reservation:{reservation.id}:created",
    )
    return reservation


def expire_no_show(db: Session, row: Reservation, current: datetime) -> bool:
    if row.status != "confirmed" or current < row.start_at + timedelta(
        minutes=CHECKIN_LATE_MINUTES
    ):
        return False
    row.status = "expired"
    row.violation = True
    notify(
        db,
        row.user_id,
        "reservation",
        "未签到，预约已释放",
        f"预约 {row.id} 未在签到期限内签到，座位已释放并记录一次违约。",
        row.id,
        f"reservation:{row.id}:no-show",
    )
    write_audit(db, None, "reservation.no_show", "reservation", row.id, {"violation": True})
    return True


def cancel_reservation(db: Session, user: User, reservation_id: str) -> Reservation:
    row = db.get(Reservation, reservation_id, with_for_update=True)
    if row is None or (
        row.user_id != user.id and not has_permission(db, user, "reservation:manage_all")
    ):
        raise HTTPException(404, "没有找到本人预约")
    if row.user_id == user.id and not has_permission(db, user, "reservation:cancel_own"):
        raise HTTPException(403, "当前账号没有取消预约的权限")
    if expire_no_show(db, row, now_local()):
        db.commit()
        raise HTTPException(409, "签到期限已过，预约已释放并记录违约")
    if row.status != "confirmed":
        raise HTTPException(409, "只有未签到的有效预约可以取消")
    row.status = "cancelled"
    write_audit(db, user.id, "reservation.cancelled", "reservation", row.id)
    return row


def expire_application(db: Session, application: ClassroomApplication, current: datetime) -> bool:
    if application.status != "pending" or current < application.timeout_at:
        return False
    application.status = "expired"
    notify(
        db,
        application.applicant_id,
        "approval",
        "教室申请超时撤销",
        f"申请 {application.id} 超时未完成审批，已自动撤销；时段没有被占用。",
        application.id,
        f"classroom:{application.id}:timeout",
    )
    write_audit(db, None, "classroom_application.expired", "classroom_application", application.id)
    return True


def create_classroom_application(
    db: Session, user: User, data: ClassroomApplicationInput
) -> ClassroomApplication:
    """Submit a two-stage classroom request without blocking seats yet."""
    if not has_permission(db, user, "classroom:create"):
        raise HTTPException(403, "只有学生账号可以提交教室申请")
    validate_range(data.start_at, data.end_at)
    room = db.get(Room, data.room_id)
    if room is None or not room.active:
        raise HTTPException(404, "没有找到可申请的空间")
    if not can_access_room(db, user, room):
        raise HTTPException(404, "没有找到可申请的空间")
    if not room_hours_allow(room, data.start_at, data.end_at):
        raise HTTPException(422, f"该教室开放时间为 {room.open_time}–{room.close_time}")
    conflict = find_room_conflict(db, room, data.start_at, data.end_at)
    if conflict:
        raise HTTPException(409, {"message": conflict["reason"], "source": conflict})
    application = ClassroomApplication(
        id=new_id("ROOM"),
        applicant_id=user.id,
        room_id=room.id,
        start_at=data.start_at,
        end_at=data.end_at,
        purpose=data.purpose,
        status="pending",
        current_stage=1,
        timeout_at=now_local() + timedelta(hours=APPROVAL_TIMEOUT_HOURS),
    )
    db.add(application)
    write_audit(
        db, user.id, "classroom_application.created", "classroom_application", application.id
    )
    for approver in db.scalars(select(User).where(User.active.is_(True))):
        if not has_permission(db, approver, "classroom:approve:1"):
            continue
        notify(
            db,
            approver.id,
            "approval",
            "有新的教室预约待审批",
            f"申请 {application.id} 等待第一级审批。",
            application.id,
            f"classroom:{application.id}:stage1:{approver.id}",
        )
    notify(
        db,
        user.id,
        "approval",
        "教室申请已提交",
        f"申请 {application.id} 已进入第一级审批；审批通过前不会占用座位。",
        application.id,
        f"classroom:{application.id}:submitted",
    )
    return application


def decide_classroom_application(
    db: Session,
    user: User,
    application: ClassroomApplication,
    decision: str,
    comment: str,
) -> ClassroomApplication:
    """Record one approval decision and hold the whole room after stage two."""
    if application.status != "pending":
        raise HTTPException(409, "这条申请已处理")
    stage = application.current_stage
    if not has_permission(db, user, "classroom:approve") or not (
        has_permission(db, user, f"classroom:approve:{stage}")
        or has_permission(db, user, "classroom:approve:any")
    ):
        raise HTTPException(403, f"当前申请需要第 {stage} 级审批")
    if expire_application(db, application, now_local()):
        db.commit()
        raise HTTPException(409, "申请已超过审批期限并撤销")
    db.add(
        ApprovalRecord(
            application_id=application.id,
            stage=stage,
            approver_id=user.id,
            decision=decision,
            comment=comment,
        )
    )
    if decision == "reject":
        application.status = "rejected"
        notify(
            db,
            application.applicant_id,
            "approval",
            "教室预约申请被驳回",
            comment or f"申请 {application.id} 未通过审批。",
            application.id,
            f"classroom:{application.id}:rejected",
        )
    elif application.current_stage == 1:
        application.current_stage = 2
        for approver in db.scalars(select(User).where(User.active.is_(True))):
            if not has_permission(db, approver, "classroom:approve:2"):
                continue
            notify(
                db,
                approver.id,
                "approval",
                "教室预约待第二级审批",
                f"申请 {application.id} 已通过第一级审批。",
                application.id,
                f"classroom:{application.id}:stage2:{approver.id}",
            )
        notify(
            db,
            application.applicant_id,
            "approval",
            "教室申请已通过第一级审批",
            f"申请 {application.id} 正等待第二级审批。",
            application.id,
            f"classroom:{application.id}:stage1-approved",
        )
    else:
        room = db.scalar(select(Room).where(Room.id == application.room_id).with_for_update())
        if room is None:
            raise HTTPException(404, "教室资料不存在")
        conflict = find_room_conflict(db, room, application.start_at, application.end_at)
        if conflict:
            raise HTTPException(
                409, {"message": "教室时段已有占用，申请仍可重新处理", "source": conflict}
            )
        application.status = "approved"
        db.add(
            ResourceHold(
                resource_type="room",
                resource_id=room.id,
                module="M2",
                source_id=application.id,
                start_at=application.start_at,
                end_at=application.end_at,
                reason="整教室预约审批通过",
                active=True,
            )
        )
        notify(
            db,
            application.applicant_id,
            "approval",
            "教室预约申请已通过",
            f"教室 {room.name} 已在批准时段锁定。",
            application.id,
            f"classroom:{application.id}:approved",
        )
    write_audit(
        db,
        user.id,
        f"classroom_application.{decision}",
        "classroom_application",
        application.id,
        {"stage": stage, "comment": comment},
    )
    return application


def create_work_order(db: Session, user: User, data: WorkOrderInput) -> WorkOrder:
    """Report a fault and set its first response deadline."""
    if not has_permission(db, user, "work_order:create"):
        raise HTTPException(403, "当前账号没有上报故障的权限")
    if data.seat_id:
        seat = db.get(Seat, data.seat_id)
        if seat is None or not seat.active:
            raise HTTPException(404, "没有找到该座位")
        room_id = seat.room_id
        room = db.get(Room, room_id)
        if room is None or not can_access_room(db, user, room):
            raise HTTPException(404, "没有找到可报修的座位")
    else:
        room = db.get(Room, data.room_id)
        if room is None or not room.active:
            raise HTTPException(404, "没有找到该房间")
        if not can_access_room(db, user, room):
            raise HTTPException(404, "没有找到可报修的房间")
        room_id = room.id
    work_order = WorkOrder(
        id=new_id("WO"),
        reporter_id=user.id,
        seat_id=data.seat_id,
        room_id=room_id if data.room_id else None,
        title=data.title,
        description=data.description,
        severity=data.severity,
        status="submitted",
        due_at=now_local() + timedelta(hours=WORK_ORDER_SLA_HOURS),
    )
    db.add(work_order)
    write_audit(db, user.id, "work_order.created", "work_order", work_order.id)
    for repair_user in db.scalars(
        select(User).where(User.role.in_(("repair", "admin")), User.active.is_(True))
    ):
        notify(
            db,
            repair_user.id,
            "work_order",
            "有新的物业报修工单",
            f"工单 {work_order.id}：{work_order.title}",
            work_order.id,
            f"work-order:{work_order.id}:created:{repair_user.id}",
        )
    return work_order


def start_work_order(db: Session, user: User, work_order: WorkOrder) -> WorkOrder:
    """Mark a ticket in repair and place a source-traceable resource hold."""
    if work_order.status != "assigned":
        raise HTTPException(409, "工单必须先派单")
    if not has_permission(db, user, "work_order:manage_all") and work_order.assignee_id != user.id:
        raise HTTPException(403, "只有被派单的维修人员可以开始维修")
    if work_order.seat_id:
        target_type, target_id = "seat", work_order.seat_id
        seat = db.get(Seat, work_order.seat_id)
        if seat is None:
            raise HTTPException(404, "报修座位资料不存在")
        room_id = seat.room_id
    else:
        target_type, target_id = "room", work_order.room_id or ""
        room_id = work_order.room_id
    if room_id:
        db.scalar(select(Room).where(Room.id == room_id).with_for_update())
    db.add(
        ResourceHold(
            resource_type=target_type,
            resource_id=target_id,
            module="M3",
            source_id=work_order.id,
            start_at=now_local(),
            end_at=now_local() + timedelta(days=3650),
            reason="物业工单维修中",
            active=True,
        )
    )
    work_order.status = "in_repair"
    write_audit(db, user.id, "work_order.started", "work_order", work_order.id)
    notify(
        db,
        work_order.reporter_id,
        "work_order",
        "工单已开始维修",
        f"工单 {work_order.id} 正在维修；受影响资源暂不可预约。",
        work_order.id,
        f"work-order:{work_order.id}:started",
    )
    return work_order


def release_work_order_holds(db: Session, work_order_id: str) -> None:
    """Release every active M3 hold made by a ticket after acceptance."""
    for hold in db.scalars(
        select(ResourceHold).where(
            ResourceHold.module == "M3",
            ResourceHold.source_id == work_order_id,
            ResourceHold.active.is_(True),
        )
    ):
        hold.active = False


def cancel_classroom_application(
    db: Session, user: User, application: ClassroomApplication
) -> ClassroomApplication:
    """Cancel an owned pending/approved request and release its approved hold."""
    if application.applicant_id != user.id and not has_permission(db, user, "classroom:manage_all"):
        raise HTTPException(404, "没有找到本人申请")
    if application.applicant_id == user.id and not (
        has_permission(db, user, "classroom:cancel_own")
        or has_permission(db, user, "classroom:manage_all")
    ):
        raise HTTPException(403, "当前账号没有取消教室申请的权限")
    if expire_application(db, application, now_local()):
        db.commit()
        raise HTTPException(409, "申请已超过审批期限并撤销")
    if application.status not in {"pending", "approved"}:
        raise HTTPException(409, "当前申请状态不能取消")
    was_approved = application.status == "approved"
    application.status = "cancelled"
    if was_approved:
        for hold in db.scalars(
            select(ResourceHold).where(
                ResourceHold.module == "M2",
                ResourceHold.source_id == application.id,
                ResourceHold.active.is_(True),
            )
        ):
            hold.active = False
    write_audit(
        db, user.id, "classroom_application.cancelled", "classroom_application", application.id
    )
    notify(
        db,
        application.applicant_id,
        "approval",
        "教室预约已取消",
        f"申请 {application.id} 已取消；如已批准，教室时段已释放。",
        application.id,
        f"classroom:{application.id}:cancelled",
    )
    return application


def cancel_work_order(db: Session, user: User, work_order: WorkOrder) -> WorkOrder:
    """Cancel a report before repair starts; an active repair must be accepted."""
    if work_order.reporter_id != user.id and not has_permission(db, user, "work_order:manage_all"):
        raise HTTPException(404, "没有找到本人报修工单")
    if work_order.reporter_id == user.id and not (
        has_permission(db, user, "work_order:cancel_own")
        or has_permission(db, user, "work_order:manage_all")
    ):
        raise HTTPException(403, "当前账号没有取消报修工单的权限")
    if work_order.status not in {"submitted", "assigned"}:
        raise HTTPException(409, "工单已开始维修，不能取消")
    work_order.status = "cancelled"
    write_audit(db, user.id, "work_order.cancelled", "work_order", work_order.id)
    if work_order.assignee_id:
        notify(
            db,
            work_order.assignee_id,
            "work_order",
            "报修工单已取消",
            f"工单 {work_order.id} 已由报修人取消。",
            work_order.id,
            f"work-order:{work_order.id}:cancelled",
        )
    return work_order


def preview_agent_action(
    db: Session, user: User, data: AgentActionPreviewInput
) -> PendingAgentAction:
    """Validate a model-proposed write action and save a human-readable preview."""
    action = data.action_type
    payload = data.payload
    if action == "reserve_seat":
        if not has_permission(db, user, "reservation:create"):
            raise HTTPException(403, "当前账号没有预约座位的权限")
        try:
            command = ReservationInput.model_validate(payload)
        except ValidationError as error:
            raise HTTPException(422, error.errors()) from error
        start, end = command.start_at, command.start_at + timedelta(hours=command.duration_hours)
        validate_range(start, end)
        maximum_hours = setting_int(db, "max_booking_hours")
        if command.duration_hours > maximum_hours:
            raise HTTPException(422, f"当前系统每次最多可预约 {maximum_hours} 小时")
        seat = db.scalar(select(Seat).where(Seat.id == command.seat_id, Seat.active.is_(True)))
        if seat is None:
            raise HTTPException(404, "没有找到该座位")
        room = db.get(Room, seat.room_id)
        if room is None or not room.active or not room_hours_allow(room, start, end):
            raise HTTPException(409, "该空间在此时段未开放")
        if not can_access_room(db, user, room):
            raise HTTPException(404, "没有找到该座位")
        conflict = find_seat_conflict(db, seat, start, end)
        if conflict:
            raise HTTPException(409, {"message": conflict["reason"], "source": conflict})
        summary = f"预约座位 {seat.label}，{start:%Y-%m-%d %H:%M} 至 {end:%H:%M}"
    elif action == "submit_room_application":
        if not has_permission(db, user, "classroom:create"):
            raise HTTPException(403, "当前账号没有提交教室申请的权限")
        try:
            command = ClassroomApplicationInput.model_validate(payload)
        except ValidationError as error:
            raise HTTPException(422, error.errors()) from error
        validate_range(command.start_at, command.end_at)
        room = db.get(Room, command.room_id)
        if room is None or not room.active:
            raise HTTPException(404, "没有找到可申请的空间")
        if not can_access_room(db, user, room):
            raise HTTPException(404, "没有找到可申请的空间")
        if not room_hours_allow(room, command.start_at, command.end_at):
            raise HTTPException(422, f"该空间开放时间为 {room.open_time}–{room.close_time}")
        conflict = find_room_conflict(db, room, command.start_at, command.end_at)
        if conflict:
            raise HTTPException(409, {"message": conflict["reason"], "source": conflict})
        summary = f"提交教室申请：{room.name}，{command.start_at:%Y-%m-%d %H:%M} 至 {command.end_at:%H:%M}；用途：{command.purpose}"
    elif action == "report_fault":
        if not has_permission(db, user, "work_order:create"):
            raise HTTPException(403, "当前账号没有提交报修的权限")
        try:
            command = WorkOrderInput.model_validate(payload)
        except ValidationError as error:
            raise HTTPException(422, error.errors()) from error
        target = command.seat_id or command.room_id or ""
        if command.seat_id:
            seat = db.get(Seat, command.seat_id)
            room = db.get(Room, seat.room_id) if seat else None
            if seat is None or room is None or not can_access_room(db, user, room):
                raise HTTPException(404, "没有找到报修座位")
        else:
            room = db.get(Room, command.room_id)
            if room is None or not can_access_room(db, user, room):
                raise HTTPException(404, "没有找到报修空间")
        summary = f"提交报修：{target}，{command.title}（{command.severity}）"
    elif action == "cancel_classroom_application":
        application_id = str(payload.get("application_id", ""))
        application = db.get(ClassroomApplication, application_id)
        if application is None or (
            application.applicant_id != user.id
            and not has_permission(db, user, "classroom:manage_all")
        ):
            raise HTTPException(404, "没有找到可取消的本人教室申请")
        if application.status not in {"pending", "approved"}:
            raise HTTPException(409, "当前教室申请状态不能取消")
        summary = f"取消教室申请 {application.id}（{application.room_id}）"
        payload = {"application_id": application.id}
    elif action == "cancel_work_order":
        work_order_id = str(payload.get("work_order_id", ""))
        work_order = db.get(WorkOrder, work_order_id)
        if work_order is None or (
            work_order.reporter_id != user.id
            and not has_permission(db, user, "work_order:manage_all")
        ):
            raise HTTPException(404, "没有找到可取消的本人报修工单")
        if work_order.status not in {"submitted", "assigned"}:
            raise HTTPException(409, "工单已开始维修，不能取消")
        summary = f"取消报修工单 {work_order.id}（{work_order.title}）"
        payload = {"work_order_id": work_order.id}
    else:
        reservation_id = str(payload.get("reservation_id", ""))
        reservation = db.get(Reservation, reservation_id)
        if reservation is None or (
            reservation.user_id != user.id
            and not has_permission(db, user, "reservation:manage_all")
        ):
            raise HTTPException(404, "没有找到可取消的本人预约")
        if reservation.status != "confirmed":
            raise HTTPException(409, "只有未签到的有效预约可以取消")
        summary = f"取消预约 {reservation.id}（座位 {reservation.seat_id}，{reservation.start_at:%Y-%m-%d %H:%M}）"
        payload = {"reservation_id": reservation.id}
    pending = PendingAgentAction(
        id=new_id("ACT"),
        user_id=user.id,
        action_type=action,
        payload=payload,
        summary=summary,
        status="pending",
        expires_at=now_local() + timedelta(minutes=5),
    )
    db.add(pending)
    write_audit(
        db, user.id, "agent_action.previewed", "agent_action", pending.id, {"action": action}
    )
    return pending


def execute_agent_action(db: Session, user: User, pending: PendingAgentAction) -> dict:
    """Execute a confirmed action once, rechecking domain rules before commit."""
    if pending.user_id != user.id:
        raise HTTPException(404, "没有找到待确认操作")
    if pending.status == "executed" and pending.result is not None:
        return pending.result
    if pending.status != "pending" or pending.expires_at <= now_local():
        raise HTTPException(409, "待确认操作已失效，请重新发起")
    if pending.action_type == "reserve_seat":
        row = create_reservation(
            db,
            user,
            ReservationInput.model_validate(pending.payload),
            idempotency_key=f"agent-action:{pending.id}",
        )
        result = {"id": row.id, "seat_id": row.seat_id, "status": row.status}
    elif pending.action_type == "submit_room_application":
        row = create_classroom_application(
            db, user, ClassroomApplicationInput.model_validate(pending.payload)
        )
        result = {"id": row.id, "status": row.status, "current_stage": row.current_stage}
    elif pending.action_type == "report_fault":
        row = create_work_order(db, user, WorkOrderInput.model_validate(pending.payload))
        result = {"id": row.id, "status": row.status}
    elif pending.action_type == "cancel_classroom_application":
        application = db.get(
            ClassroomApplication, pending.payload["application_id"], with_for_update=True
        )
        if application is None:
            raise HTTPException(404, "没有找到教室申请")
        cancel_classroom_application(db, user, application)
        result = {"id": application.id, "status": application.status}
    elif pending.action_type == "cancel_work_order":
        work_order = db.get(WorkOrder, pending.payload["work_order_id"], with_for_update=True)
        if work_order is None:
            raise HTTPException(404, "没有找到报修工单")
        cancel_work_order(db, user, work_order)
        result = {"id": work_order.id, "status": work_order.status}
    else:
        row = cancel_reservation(db, user, pending.payload["reservation_id"])
        result = {"id": row.id, "status": row.status}
    pending.status = "executed"
    pending.result = result
    write_audit(db, user.id, "agent_action.executed", "agent_action", pending.id, result)
    return result


def issue_api_token(db: Session, user: User, label: str) -> str:
    """Create a 90-day personal token; the caller receives it only once."""
    raw = new_token()
    db.add(
        ApiCredential(
            token_hash=token_hash(raw),
            user_id=user.id,
            kind="personal",
            label=label,
            expires_at=now_local() + timedelta(days=90),
        )
    )
    write_audit(db, user.id, "api_token.created", "api_token", label)
    return raw
