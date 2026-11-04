"""Two-step API for safe model-proposed business actions."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from campus_app.clock import now_local
from campus_app.dependencies import CurrentUser, DbSession
from campus_app.domain import execute_agent_action, preview_agent_action, write_audit
from campus_app.models import PendingAgentAction
from campus_app.schemas import AgentActionPreviewInput, AgentConfirmInput

router = APIRouter(prefix="/api/v1/agent", tags=["可选 Agent · 操作确认"])


@router.post("/actions/preview", status_code=201)
def preview_action(
    data: AgentActionPreviewInput,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    """Create a five-minute preview; this endpoint does not mutate business records."""
    row = preview_agent_action(db, user, data)
    db.commit()
    return {
        "id": row.id,
        "action_type": row.action_type,
        "summary": row.summary,
        "status": row.status,
        "expires_at": row.expires_at.isoformat(timespec="minutes"),
        "confirmation_required": True,
    }


@router.post("/actions/{action_id}/confirm")
def confirm_action(
    action_id: str,
    data: AgentConfirmInput,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    """Execute a preview only after the conversation returns explicit confirmation."""
    row = db.scalar(
        select(PendingAgentAction).where(PendingAgentAction.id == action_id).with_for_update()
    )
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "没有找到待确认操作")
    if not data.confirmed:
        if row.status == "pending":
            row.status = "cancelled"
            write_audit(db, user.id, "agent_action.cancelled", "agent_action", row.id)
            db.commit()
        return {"id": row.id, "status": row.status, "message": "操作已取消"}
    result = execute_agent_action(db, user, row)
    db.commit()
    return {"id": row.id, "status": row.status, "result": result}


@router.get("/actions/{action_id}")
def get_action_status(action_id: str, db: DbSession, user: CurrentUser) -> dict:
    """Read only the current user's pending-action status."""
    row = db.scalar(
        select(PendingAgentAction).where(
            PendingAgentAction.id == action_id,
            PendingAgentAction.user_id == user.id,
        )
    )
    if row is None:
        raise HTTPException(404, "没有找到待确认操作")
    return {
        "id": row.id,
        "action_type": row.action_type,
        "summary": row.summary,
        "status": row.status,
        "expired": row.expires_at <= now_local(),
        "result": row.result,
    }
