"""Validated request and response models for the HTTP API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UserView(BaseModel):
    """Public account fields returned to clients."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    role: str


class LoginInput(BaseModel):
    """Username and password submitted to the login endpoint."""

    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=8, max_length=200)


class TokenInput(BaseModel):
    """Label for a personal API token."""

    label: str = Field(min_length=2, max_length=120)


class ReservationInput(BaseModel):
    """Seat and time range requested by a student."""

    seat_id: str = Field(min_length=1, max_length=40)
    start_at: datetime
    duration_hours: int = Field(ge=1, le=24)


class CheckInInput(BaseModel):
    """One-time check-in code shown after reservation."""

    code: str = Field(min_length=4, max_length=24)


class ClassroomApplicationInput(BaseModel):
    """Room and time requested for a whole-classroom booking."""

    room_id: str = Field(min_length=1, max_length=40)
    start_at: datetime
    end_at: datetime
    purpose: str = Field(min_length=5, max_length=500)


class FinalApprovalInput(BaseModel):
    comment: str = Field(default="", max_length=500)


class ApprovalInput(FinalApprovalInput):
    """Approve or reject one pending classroom-application stage."""

    decision: str = Field(pattern="^(approve|reject)$")


class WorkOrderInput(BaseModel):
    """A fault report targeting one seat or one entire room."""

    seat_id: str | None = Field(default=None, max_length=40)
    room_id: str | None = Field(default=None, max_length=40)
    title: str = Field(min_length=4, max_length=160)
    description: str = Field(min_length=10, max_length=3000)
    severity: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")

    @model_validator(mode="after")
    def target_exactly_one_resource(self) -> WorkOrderInput:
        if bool(self.seat_id) == bool(self.room_id):
            raise ValueError("请选择一个座位或一个房间作为报修位置")
        return self


class AssignWorkOrderInput(BaseModel):
    """Repair worker selected by an administrator."""

    assignee_id: int


class WorkOrderRatingInput(BaseModel):
    """Reporter feedback after repair acceptance."""

    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=500)


class RoomHoursInput(BaseModel):
    open_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    close_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    @model_validator(mode="after")
    def ordered_hours(self):
        if self.open_time >= self.close_time:
            raise ValueError("开放时间要早于关闭时间")
        return self


class RoomInput(RoomHoursInput):
    """Room master-data fields managed by an administrator."""

    id: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=2, max_length=120)
    area: str = Field(min_length=2, max_length=120)
    room_type: str = Field(default="study", pattern="^(study|classroom)$")
    access_scope: str = Field(default="campus", pattern="^(campus|department)$")
    department_id: str | None = Field(default=None, max_length=120)
    capacity: int = Field(ge=1, le=1000)

    @model_validator(mode="after")
    def department_scope_requires_department(self) -> RoomInput:
        if self.access_scope == "department" and not self.department_id:
            raise ValueError("院系专属空间必须选择所属院系")
        return self


class SeatInput(BaseModel):
    """Seat master-data fields managed by an administrator."""

    id: str = Field(min_length=2, max_length=40)
    room_id: str = Field(min_length=2, max_length=40)
    label: str = Field(min_length=1, max_length=40)
    has_power: bool = False
    accessible: bool = False


class ActiveInput(BaseModel):
    """New active/inactive value for a room or seat."""

    active: bool


class UserCreateInput(BaseModel):
    """New user account created by an administrator."""

    username: str = Field(min_length=2, max_length=80)
    display_name: str = Field(min_length=2, max_length=120)
    email: str | None = Field(default=None, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: str = Field(min_length=8, max_length=200)
    role: str = Field(pattern="^[a-z][a-z0-9_]{1,31}$")
    department_id: str | None = Field(default=None, max_length=120)


class RoleInput(BaseModel):
    """Role change made by an administrator."""

    role: str = Field(pattern="^[a-z][a-z0-9_]{1,31}$")


class RoleCreateInput(BaseModel):
    """A new role created by a system administrator."""

    code: str = Field(pattern="^[a-z][a-z0-9_]{1,31}$")
    name: str = Field(min_length=2, max_length=80)
    permissions: list[str] = Field(min_length=1, max_length=50)


class RoleUpdateInput(BaseModel):
    """Updated label, permission list, or active state for a role."""

    name: str = Field(min_length=2, max_length=80)
    permissions: list[str] = Field(min_length=1, max_length=50)
    active: bool = True


class AppSettingsInput(BaseModel):
    """Administrator-adjustable project parameters."""

    max_booking_hours: int = Field(ge=1, le=24)


class AgentActionPreviewInput(BaseModel):
    """A requested write action that must be previewed before execution."""

    action_type: str = Field(
        pattern="^(reserve_seat|submit_room_application|report_fault|cancel_reservation|cancel_classroom_application|cancel_work_order)$"
    )
    payload: dict = Field(default_factory=dict)


class AgentConfirmInput(BaseModel):
    """Explicit confirmation that the user approved a pending write action."""

    confirmed: bool
