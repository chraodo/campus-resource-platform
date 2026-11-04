"""Permission catalog and role lookup for configurable RBAC."""

from __future__ import annotations

from sqlalchemy.orm import Session

from campus_app.models import RoleDefinition, Room, User

PERMISSION_CATALOG = {
    "reservation:create": "创建座位预约",
    "reservation:read_own": "查看本人预约",
    "reservation:read_all": "查看全校预约与使用情况",
    "reservation:cancel_own": "取消本人预约",
    "reservation:checkin": "签到本人预约",
    "reservation:manage_all": "管理所有预约",
    "classroom:create": "提交整教室申请",
    "classroom:read_own": "查看本人教室申请",
    "classroom:read_all": "查看所有教室申请",
    "classroom:approve": "审批教室申请",
    "classroom:approve:1": "执行第一级审批",
    "classroom:approve:2": "执行第二级审批",
    "classroom:approve:any": "执行任一级审批",
    "classroom:cancel_own": "取消本人教室申请",
    "classroom:manage_all": "管理所有教室申请",
    "work_order:create": "提交物业报修",
    "work_order:read_own": "查看本人报修",
    "work_order:read_all": "查看所有报修工单",
    "work_order:cancel_own": "取消本人未开工的报修",
    "work_order:assign": "派发或认领维修工单",
    "work_order:assign:any": "派工给其他维修人员",
    "work_order:repair": "执行维修流程",
    "work_order:manage_all": "管理所有报修工单",
    "work_order:accept_own": "验收本人报修",
    "work_order:rate_own": "评价本人报修",
    "resource:manage": "管理空间与座位",
    "user:manage": "管理账号与角色权限",
    "metrics:read": "查看运营统计",
    "audit:read": "查看操作审计",
    "notification:read": "查看通知",
    "token:manage": "管理个人 API 令牌",
    "system:config": "修改系统预约参数",
}


ROLE_DEFAULTS: dict[str, tuple[str, list[str]]] = {
    "student": (
        "学生",
        [
            "reservation:create",
            "reservation:read_own",
            "reservation:cancel_own",
            "reservation:checkin",
            "classroom:create",
            "classroom:read_own",
            "classroom:cancel_own",
            "work_order:create",
            "work_order:read_own",
            "work_order:cancel_own",
            "work_order:accept_own",
            "work_order:rate_own",
            "notification:read",
            "token:manage",
        ],
    ),
    "approver_l1": (
        "一级审批人",
        [
            "classroom:approve",
            "classroom:approve:1",
            "classroom:read_all",
            "notification:read",
            "token:manage",
        ],
    ),
    "approver_l2": (
        "二级审批人",
        [
            "classroom:approve",
            "classroom:approve:2",
            "classroom:read_all",
            "notification:read",
            "token:manage",
        ],
    ),
    "repair": (
        "维修人员",
        [
            "work_order:read_all",
            "work_order:assign",
            "work_order:repair",
            "notification:read",
            "token:manage",
        ],
    ),
    "room_manager": (
        "空间管理员",
        [
            "resource:manage",
            "metrics:read",
            "reservation:read_all",
            "notification:read",
            "token:manage",
        ],
    ),
    "operations_manager": (
        "运营管理员",
        [
            "metrics:read",
            "reservation:read_all",
            "classroom:read_all",
            "work_order:read_all",
            "audit:read",
            "notification:read",
            "token:manage",
        ],
    ),
    "admin": (
        "系统管理员",
        ["*"],
    ),
}


def permissions_for(db: Session, user: User) -> list[str]:
    """Return the active database role permissions with built-in fallback."""
    role = db.get(RoleDefinition, user.role)
    if role is not None:
        return list(role.permissions) if role.active else []
    default = ROLE_DEFAULTS.get(user.role)
    return list(default[1]) if default else []


def has_permission(db: Session, user: User, permission: str) -> bool:
    """Check one permission; the wildcard is reserved for the system administrator."""
    permissions = permissions_for(db, user)
    return "*" in permissions or permission in permissions


def role_name(db: Session, role_code: str) -> str:
    """Return the configured role label or its built-in label."""
    role = db.get(RoleDefinition, role_code)
    if role is not None:
        return role.name
    default = ROLE_DEFAULTS.get(role_code)
    return default[0] if default else role_code


def role_is_active(db: Session, role_code: str) -> bool:
    """Check that a configured role exists and is enabled."""
    role = db.get(RoleDefinition, role_code)
    return role.active if role else role_code in ROLE_DEFAULTS


def can_access_room(db: Session, user: User, room: Room) -> bool:
    """Apply campus-wide or department-only visibility to a space."""
    if room.access_scope != "department":
        return True
    if has_permission(db, user, "resource:manage"):
        return True
    return bool(user.department_id and user.department_id == room.department_id)
