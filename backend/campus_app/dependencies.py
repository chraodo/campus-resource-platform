"""FastAPI dependencies for database access and role checks."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from campus_app.clock import now_local
from campus_app.db import get_db
from campus_app.models import ApiCredential, User
from campus_app.rbac import role_is_active
from campus_app.security import token_hash

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(request: Request, db: DbSession) -> User:
    """Resolve an active user from a browser cookie or bearer token."""
    authorization = request.headers.get("authorization", "")
    bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    raw_token = bearer or request.cookies.get("campus_session", "")
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    credential = db.scalar(
        select(ApiCredential).where(ApiCredential.token_hash == token_hash(raw_token))
    )
    if credential is None or credential.expires_at <= now_local():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录状态已失效")
    if credential.kind == "screen":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="屏幕令牌不能登录用户账号"
        )
    user = db.get(User, credential.user_id)
    if user is None or not user.active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已停用")
    if not role_is_active(db, user.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号角色已停用")
    return user


def get_screen_credential(request: Request, db: DbSession) -> ApiCredential:
    """Resolve a room-scoped credential used only by a physical display."""
    authorization = request.headers.get("authorization", "")
    bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    if not bearer:
        raise HTTPException(status_code=401, detail="需要屏幕专用令牌")
    credential = db.scalar(
        select(ApiCredential).where(ApiCredential.token_hash == token_hash(bearer))
    )
    if credential is None or credential.kind != "screen" or credential.expires_at <= now_local():
        raise HTTPException(status_code=401, detail="屏幕令牌无效或已过期")
    return credential


CurrentUser = Annotated[User, Depends(get_current_user)]
ScreenCredential = Annotated[ApiCredential, Depends(get_screen_credential)]


def require_roles(*roles: str):
    """Build a dependency that allows the listed roles and administrators."""

    def _require(user: CurrentUser) -> User:
        if user.role != "admin" and user.role not in roles:
            raise HTTPException(status_code=403, detail="当前账号没有此操作权限")
        return user

    return _require
