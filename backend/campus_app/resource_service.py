"""Private HTTP endpoint used by M2 to acquire a D room hold."""

from fastapi import FastAPI, HTTPException

from campus_app.dependencies import CurrentUser, DbSession
from campus_app.domain import decide_classroom_application
from campus_app.models import ClassroomApplication
from campus_app.rbac import has_permission
from campus_app.schemas import FinalApprovalInput
from campus_app.views import application_view

app = FastAPI(title="校园空间占用服务", docs_url=None, redoc_url=None)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.put("/internal/v1/classroom-holds/{application_id}")
def acquire_classroom_hold(
    application_id: str, data: FinalApprovalInput, db: DbSession, user: CurrentUser
):
    if not has_permission(db, user, "classroom:approve") or not (
        has_permission(db, user, "classroom:approve:2")
        or has_permission(db, user, "classroom:approve:any")
    ):
        raise HTTPException(403, "需要终审权限")
    row = db.get(ClassroomApplication, application_id, with_for_update=True)
    if row is None:
        raise HTTPException(404, "没有找到教室申请")
    if row.status == "approved" and any(
        approval.stage == 2 and approval.approver_id == user.id and approval.comment == data.comment
        for approval in row.approvals
    ):
        return application_view(row)
    if row.current_stage != 2 or row.status != "pending":
        raise HTTPException(409, "申请当前不可终审")
    decide_classroom_application(db, user, row, "approve", data.comment)
    db.flush()
    response = application_view(row)
    db.commit()
    return response
