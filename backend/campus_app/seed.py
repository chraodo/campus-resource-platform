"""Development-only seed data used for local presentations and acceptance checks."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from sqlalchemy import select

from campus_app.clock import now_local
from campus_app.db import SessionLocal
from campus_app.domain import write_audit
from campus_app.models import (
    ApprovalRecord,
    AppSetting,
    ClassroomApplication,
    Reservation,
    ResourceHold,
    RoleDefinition,
    Room,
    Seat,
    User,
    WorkOrder,
)
from campus_app.rbac import ROLE_DEFAULTS
from campus_app.security import hash_password

DEMO_PASSWORD = "demo2026"


def seed_demo_data() -> None:
    """Create fictional accounts, rooms, and cross-module sample records once."""
    with SessionLocal() as db:
        for code, (name, permissions) in ROLE_DEFAULTS.items():
            if db.get(RoleDefinition, code) is None:
                db.add(
                    RoleDefinition(
                        code=code,
                        name=name,
                        permissions=permissions,
                        active=True,
                        system=True,
                    )
                )
        if db.get(AppSetting, "max_booking_hours") is None:
            db.add(AppSetting(key="max_booking_hours", value=4))
        db.flush()
        if db.scalar(select(User.id).limit(1)):
            db.commit()
            return
        users = [
            User(
                username="student",
                display_name="演示学生",
                password_hash=hash_password(DEMO_PASSWORD),
                role="student",
                department_id="计算机学院",
            ),
            User(
                username="student2",
                display_name="演示学生乙",
                password_hash=hash_password(DEMO_PASSWORD),
                role="student",
                department_id="计算机学院",
            ),
            User(
                username="approver1",
                display_name="一级审批人",
                password_hash=hash_password(DEMO_PASSWORD),
                role="approver_l1",
                department_id="计算机学院",
            ),
            User(
                username="approver2",
                display_name="二级审批人",
                password_hash=hash_password(DEMO_PASSWORD),
                role="approver_l2",
                department_id="计算机学院",
            ),
            User(
                username="repair",
                display_name="维修人员",
                password_hash=hash_password(DEMO_PASSWORD),
                role="repair",
                department_id="计算机学院",
            ),
            User(
                username="admin",
                display_name="系统管理员",
                password_hash=hash_password(DEMO_PASSWORD),
                role="admin",
                department_id="计算机学院",
            ),
        ]
        db.add_all(users)
        db.flush()
        by_name = {user.username: user for user in users}

        library = Room(
            id="LIB-1",
            name="图书馆一层自习区",
            area="邯郸校区 · 图书馆",
            room_type="study",
            capacity=24,
            open_time="07:00",
            close_time="23:00",
        )
        classroom = Room(
            id="C201",
            name="公共教学楼 201",
            area="邯郸校区 · 教学楼",
            room_type="classroom",
            capacity=20,
            open_time="08:00",
            close_time="22:00",
        )
        db.add_all([library, classroom])
        db.flush()
        study_seats = [
            Seat(
                id=f"A{number:02d}",
                room_id=library.id,
                label=f"A{number:02d}",
                has_power=number % 2 == 0,
                accessible=number in {1, 12},
            )
            for number in range(1, 13)
        ]
        class_seats = [
            Seat(
                id=f"C201-{number:02d}",
                room_id=classroom.id,
                label=f"C201-{number:02d}",
                has_power=number % 3 != 0,
            )
            for number in range(1, 21)
        ]
        db.add_all(study_seats + class_seats)
        db.flush()

        tomorrow = now_local().date() + timedelta(days=1)
        room_start = datetime.combine(tomorrow, time(9, 0))
        room_end = datetime.combine(tomorrow, time(12, 0))
        application = ClassroomApplication(
            id="ROOM-DEMO-003",
            applicant_id=by_name["student"].id,
            room_id=classroom.id,
            start_at=room_start,
            end_at=room_end,
            purpose="演示整教室审批占用",
            status="approved",
            current_stage=2,
            timeout_at=now_local() + timedelta(days=1),
        )
        db.add(application)
        db.flush()
        db.add_all(
            [
                ApprovalRecord(
                    application_id=application.id,
                    stage=1,
                    approver_id=by_name["approver1"].id,
                    decision="approve",
                    comment="演示数据",
                ),
                ApprovalRecord(
                    application_id=application.id,
                    stage=2,
                    approver_id=by_name["approver2"].id,
                    decision="approve",
                    comment="演示数据",
                ),
                ResourceHold(
                    resource_type="room",
                    resource_id=classroom.id,
                    module="M2",
                    source_id=application.id,
                    start_at=room_start,
                    end_at=room_end,
                    reason="整教室预约审批通过",
                    active=True,
                ),
            ]
        )

        other_booking_start = datetime.combine(tomorrow, time(10, 0))
        db.add(
            Reservation(
                id="RSV-DEMO-018",
                user_id=by_name["student2"].id,
                seat_id="A07",
                start_at=other_booking_start,
                end_at=other_booking_start + timedelta(hours=1),
                duration_hours=1,
                status="confirmed",
            )
        )

        work_order = WorkOrder(
            id="WO-DEMO-014",
            reporter_id=by_name["student"].id,
            seat_id="A03",
            title="演示：座位电源故障",
            description="演示工单处于维修中，座位由 M3 暂时下架。",
            severity="normal",
            status="in_repair",
            assignee_id=by_name["repair"].id,
            due_at=now_local() + timedelta(hours=8),
        )
        db.add(work_order)
        db.add(
            ResourceHold(
                resource_type="seat",
                resource_id="A03",
                module="M3",
                source_id=work_order.id,
                start_at=datetime.combine(tomorrow, time.min),
                end_at=datetime.combine(tomorrow + timedelta(days=1), time.min),
                reason="物业工单维修中",
                active=True,
            )
        )
        write_audit(db, None, "demo.seeded", "system", "demo-v1", {"fictional_data": True})
        db.commit()


if __name__ == "__main__":
    seed_demo_data()
