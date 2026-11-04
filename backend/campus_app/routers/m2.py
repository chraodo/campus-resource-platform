"""M2 whole-classroom request and two-stage approval routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from campus_app.dependencies import CurrentUser, DbSession, request_credential
from campus_app.domain import (
    cancel_classroom_application,
    create_classroom_application,
    decide_classroom_application,
)
from campus_app.models import ApprovalRecord, ClassroomApplication
from campus_app.rbac import has_permission
from campus_app.resource_client import acquire_classroom_hold
from campus_app.schemas import ApprovalInput, ClassroomApplicationInput
from campus_app.views import application_view

router = APIRouter(prefix="/api/v1", tags=["M2 · 整教室预约审批"])


@router.post("/classroom-applications", status_code=201)
def submit_application(
    data: ClassroomApplicationInput,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    """Submit a room request; a pending request does not block seats."""
    row = create_classroom_application(db, user, data)
    db.commit()
    db.refresh(row)
    return application_view(row)


@router.get("/classroom-applications")
def list_applications(
    db: DbSession,
    user: CurrentUser,
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[dict]:
    """List a student's requests or all requests visible to approvers/admins."""
    statement = (
        select(ClassroomApplication)
        .options(
            joinedload(ClassroomApplication.applicant),
            joinedload(ClassroomApplication.room),
            joinedload(ClassroomApplication.approvals).joinedload(ApprovalRecord.approver),
        )
        .order_by(ClassroomApplication.created_at.desc())
    )
    if has_permission(db, user, "classroom:read_all"):
        pass
    elif has_permission(db, user, "classroom:read_own"):
        statement = statement.where(ClassroomApplication.applicant_id == user.id)
    else:
        raise HTTPException(403, "当前账号没有查看教室申请的权限")
    if status_filter:
        statement = statement.where(ClassroomApplication.status == status_filter)
    return [application_view(row) for row in db.scalars(statement).unique().all()]


@router.post("/classroom-applications/{application_id}/decision")
def decide_application(
    application_id: str,
    data: ApprovalInput,
    request: Request,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    """Approve the current stage or reject the application with a reason."""
    if not has_permission(db, user, "classroom:approve"):
        raise HTTPException(403, "只有审批人可以处理教室申请")
    row = db.get(ClassroomApplication, application_id, with_for_update=True)
    if row is None:
        raise HTTPException(404, "没有找到该教室申请")
    if (
        data.decision == "approve"
        and row.current_stage == 2
        and row.status in {"pending", "approved"}
    ):
        # Release both the application lock and SQLite writer slot before the remote transaction.
        db.rollback()
        return acquire_classroom_hold(application_id, data.comment, request_credential(request))
    result = decide_classroom_application(db, user, row, data.decision, data.comment)
    db.commit()
    db.refresh(result)
    return application_view(result)


@router.post("/classroom-applications/{application_id}/cancel")
def cancel_application(
    application_id: str,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    """Cancel an applicant's pending request or an approved booking."""
    row = db.get(ClassroomApplication, application_id, with_for_update=True)
    if row is None:
        raise HTTPException(404, "没有找到本人申请")
    cancel_classroom_application(db, user, row)
    db.commit()
    db.refresh(row)
    return application_view(row)
