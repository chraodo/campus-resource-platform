"""M3 property fault reporting, assignment, repair, and acceptance routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from campus_app.dependencies import CurrentUser, DbSession
from campus_app.domain import (
    cancel_work_order,
    create_work_order,
    notify,
    release_work_order_holds,
    start_work_order,
    write_audit,
)
from campus_app.models import User, WorkOrder
from campus_app.rbac import has_permission
from campus_app.schemas import AssignWorkOrderInput, WorkOrderInput, WorkOrderRatingInput
from campus_app.views import work_order_view

router = APIRouter(prefix="/api/v1", tags=["M3 · 物业报修工单"])


def _load_work_order(db: DbSession, work_order_id: str) -> WorkOrder:
    row = db.scalar(
        select(WorkOrder)
        .options(
            joinedload(WorkOrder.reporter),
            joinedload(WorkOrder.assignee),
            joinedload(WorkOrder.seat),
            joinedload(WorkOrder.room),
        )
        .where(WorkOrder.id == work_order_id)
        .with_for_update()
    )
    if row is None:
        raise HTTPException(404, "没有找到该报修工单")
    return row


@router.post("/work-orders", status_code=201)
def report_fault(data: WorkOrderInput, db: DbSession, user: CurrentUser) -> dict:
    """Submit a fault report for one seat or one room."""
    if not has_permission(db, user, "work_order:create"):
        raise HTTPException(403, "当前账号没有上报故障的权限")
    row = create_work_order(db, user, data)
    db.commit()
    return work_order_view(_load_work_order(db, row.id))


@router.get("/work-orders")
def list_work_orders(db: DbSession, user: CurrentUser) -> list[dict]:
    """List a student's own reports or all tickets visible to repair/admin."""
    statement = (
        select(WorkOrder)
        .options(
            joinedload(WorkOrder.reporter),
            joinedload(WorkOrder.assignee),
            joinedload(WorkOrder.seat),
            joinedload(WorkOrder.room),
        )
        .order_by(WorkOrder.created_at.desc())
    )
    if has_permission(db, user, "work_order:read_all"):
        pass
    elif has_permission(db, user, "work_order:read_own"):
        statement = statement.where(WorkOrder.reporter_id == user.id)
    else:
        raise HTTPException(403, "当前账号没有查看物业工单的权限")
    return [work_order_view(row) for row in db.scalars(statement).unique().all()]


@router.post("/work-orders/{work_order_id}/cancel")
def cancel_report(work_order_id: str, db: DbSession, user: CurrentUser) -> dict:
    """Cancel an owned report before repair begins."""
    row = _load_work_order(db, work_order_id)
    cancel_work_order(db, user, row)
    db.commit()
    return work_order_view(_load_work_order(db, row.id))


@router.post("/work-orders/{work_order_id}/assign")
def assign_work_order(
    work_order_id: str,
    data: AssignWorkOrderInput,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    """Assign or reassign a submitted ticket to an active repair worker."""
    if not has_permission(db, user, "work_order:assign"):
        raise HTTPException(403, "只有物业管理员或维修人员可以派单")
    row = _load_work_order(db, work_order_id)
    if row.status not in {"submitted", "assigned"}:
        raise HTTPException(409, "工单已进入维修流程，不能重新派单")
    if not has_permission(db, user, "work_order:assign:any") and data.assignee_id != user.id:
        raise HTTPException(403, "维修人员只能认领自己的工单")
    assignee = db.get(User, data.assignee_id)
    if (
        assignee is None
        or not assignee.active
        or not has_permission(db, assignee, "work_order:repair")
    ):
        raise HTTPException(422, "请选择有效的维修人员")
    row.assignee_id = assignee.id
    row.status = "assigned"
    write_audit(
        db, user.id, "work_order.assigned", "work_order", row.id, {"assignee_id": assignee.id}
    )
    notify(
        db,
        assignee.id,
        "work_order",
        "你收到一个维修工单",
        f"工单 {row.id}：{row.title}",
        row.id,
        f"work-order:{row.id}:assigned:{assignee.id}",
    )
    db.commit()
    return work_order_view(_load_work_order(db, row.id))


@router.post("/work-orders/{work_order_id}/start")
def start_repair(work_order_id: str, db: DbSession, user: CurrentUser) -> dict:
    """Start repair and place the target room/seat out of service."""
    if not has_permission(db, user, "work_order:repair"):
        raise HTTPException(403, "只有维修人员可以开始维修")
    row = _load_work_order(db, work_order_id)
    result = start_work_order(db, user, row)
    db.commit()
    return work_order_view(_load_work_order(db, result.id))


@router.post("/work-orders/{work_order_id}/resolve")
def resolve_repair(work_order_id: str, db: DbSession, user: CurrentUser) -> dict:
    """Mark work as repaired while keeping the resource unavailable for acceptance."""
    row = _load_work_order(db, work_order_id)
    if not has_permission(db, user, "work_order:repair") or (
        not has_permission(db, user, "work_order:manage_all") and row.assignee_id != user.id
    ):
        raise HTTPException(403, "只有负责该工单的维修人员可以提交维修完成")
    if row.status != "in_repair":
        raise HTTPException(409, "工单必须处于维修中")
    row.status = "resolved"
    write_audit(db, user.id, "work_order.resolved", "work_order", row.id)
    notify(
        db,
        row.reporter_id,
        "work_order",
        "维修已完成，等待验收",
        f"工单 {row.id} 已提交维修完成；验收前资源仍不可预约。",
        row.id,
        f"work-order:{row.id}:resolved",
    )
    db.commit()
    return work_order_view(_load_work_order(db, row.id))


@router.post("/work-orders/{work_order_id}/accept")
def accept_repair(work_order_id: str, db: DbSession, user: CurrentUser) -> dict:
    """Accept the repair and restore the affected resource to service."""
    row = _load_work_order(db, work_order_id)
    if not has_permission(db, user, "work_order:manage_all") and (
        row.reporter_id != user.id or not has_permission(db, user, "work_order:accept_own")
    ):
        raise HTTPException(403, "只有报修人或管理员可以验收")
    if row.status != "resolved":
        raise HTTPException(409, "工单尚未提交维修完成")
    row.status = "accepted"
    release_work_order_holds(db, row.id)
    write_audit(db, user.id, "work_order.accepted", "work_order", row.id)
    notify(
        db,
        row.assignee_id or row.reporter_id,
        "work_order",
        "维修工单已验收",
        f"工单 {row.id} 已验收，受影响资源已恢复。",
        row.id,
        f"work-order:{row.id}:accepted",
    )
    db.commit()
    return work_order_view(_load_work_order(db, row.id))


@router.post("/work-orders/{work_order_id}/rating")
def rate_repair(
    work_order_id: str,
    data: WorkOrderRatingInput,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    """Let the reporting student rate an accepted repair."""
    row = _load_work_order(db, work_order_id)
    if row.reporter_id != user.id or not has_permission(db, user, "work_order:rate_own"):
        raise HTTPException(403, "只有报修人可以评价")
    if row.status != "accepted":
        raise HTTPException(409, "验收后才可以评价")
    if row.rating is not None:
        raise HTTPException(409, "该工单已评价")
    row.rating = data.rating
    row.rating_comment = data.comment
    write_audit(db, user.id, "work_order.rated", "work_order", row.id, {"rating": data.rating})
    db.commit()
    return work_order_view(_load_work_order(db, row.id))
