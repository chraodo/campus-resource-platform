"""Login, logout, current-user, and personal-token routes."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select

from campus_app.clock import now_local
from campus_app.config import APP_ENV, SESSION_HOURS
from campus_app.dependencies import CurrentUser, DbSession
from campus_app.domain import issue_api_token, write_audit
from campus_app.models import ApiCredential, User
from campus_app.rbac import has_permission, permissions_for, role_name
from campus_app.schemas import LoginInput, TokenInput
from campus_app.security import new_token, token_hash, verify_password
from campus_app.views import user_view

router = APIRouter(prefix="/api/v1/auth", tags=["D · 身份与权限"])


def account_view(db: DbSession, user: User) -> dict:
    """Return user fields together with the active role's display name and rights."""
    return user_view(user) | {
        "role_name": role_name(db, user.role),
        "permissions": permissions_for(db, user),
    }


@router.post("/login")
def login(data: LoginInput, db: DbSession, response: Response) -> dict:
    """Verify a password and issue an HTTP-only browser session cookie."""
    user = db.scalar(select(User).where(User.username == data.username, User.active.is_(True)))
    if user is None or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "用户名或密码不正确")
    raw_token = new_token()
    db.add(
        ApiCredential(
            token_hash=token_hash(raw_token),
            user_id=user.id,
            kind="session",
            label="浏览器登录",
            expires_at=now_local() + timedelta(hours=SESSION_HOURS),
        )
    )
    write_audit(db, user.id, "auth.login", "user", str(user.id))
    db.commit()
    response.set_cookie(
        "campus_session",
        raw_token,
        max_age=SESSION_HOURS * 3600,
        httponly=True,
        secure=APP_ENV == "production",
        samesite="lax",
        path="/",
    )
    return {"user": account_view(db, user)}


@router.post("/logout")
def logout(db: DbSession, response: Response, request: Request, user: CurrentUser) -> dict:
    """Revoke the current browser session and clear its cookie."""
    authorization = request.headers.get("authorization", "")
    bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    raw_token = bearer or request.cookies.get("campus_session", "")
    if raw_token:
        credential = db.scalar(
            select(ApiCredential).where(ApiCredential.token_hash == token_hash(raw_token))
        )
        if credential is not None:
            db.delete(credential)
    response.delete_cookie("campus_session", path="/")
    write_audit(db, user.id, "auth.logout", "user", str(user.id))
    db.commit()
    return {"ok": True}


@router.get("/me")
def current_account(db: DbSession, user: CurrentUser) -> dict:
    """Return the current user's public account fields and role."""
    return {"user": account_view(db, user)}


@router.post("/tokens", status_code=201)
def create_personal_token(data: TokenInput, db: DbSession, user: CurrentUser) -> dict:
    """Create a personal API token for an approved DSH profile or script."""
    if not has_permission(db, user, "token:manage"):
        raise HTTPException(403, "当前账号不能创建 API 令牌")
    raw = issue_api_token(db, user, data.label)
    db.commit()
    return {"token": raw, "label": data.label, "expires_in_days": 90, "shown_once": True}


@router.get("/tokens")
def list_personal_tokens(db: DbSession, user: CurrentUser) -> list[dict]:
    """List token labels and dates without returning token secrets."""
    if not has_permission(db, user, "token:manage"):
        raise HTTPException(403, "当前账号不能查看 API 令牌")
    rows = db.scalars(
        select(ApiCredential)
        .where(ApiCredential.user_id == user.id, ApiCredential.kind == "personal")
        .order_by(ApiCredential.created_at.desc())
    ).all()
    return [
        {
            "id": row.id,
            "label": row.label,
            "created_at": row.created_at.isoformat(timespec="minutes"),
            "expires_at": row.expires_at.isoformat(timespec="minutes"),
        }
        for row in rows
    ]


@router.delete("/tokens/{token_id}", status_code=204)
def revoke_personal_token(token_id: int, db: DbSession, user: CurrentUser) -> Response:
    """Revoke one personal token owned by the current user."""
    if not has_permission(db, user, "token:manage"):
        raise HTTPException(403, "当前账号不能撤销 API 令牌")
    row = db.scalar(
        select(ApiCredential).where(
            ApiCredential.id == token_id,
            ApiCredential.user_id == user.id,
            ApiCredential.kind == "personal",
        )
    )
    if row is None:
        raise HTTPException(404, "没有找到该令牌")
    db.delete(row)
    write_audit(db, user.id, "api_token.revoked", "api_token", str(token_id))
    db.commit()
    return Response(status_code=204)
