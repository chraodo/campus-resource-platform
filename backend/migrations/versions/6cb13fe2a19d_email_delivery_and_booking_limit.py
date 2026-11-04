"""Email delivery fields and adjustable booking ceiling.

Revision ID: 6cb13fe2a19d
Revises: 09bfcfef59c9
Create Date: 2026-11-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "6cb13fe2a19d"
down_revision = "09bfcfef59c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.String(length=254), nullable=True))
    op.add_column("notifications", sa.Column("email_queued_at", sa.DateTime(), nullable=True))
    op.add_column("notifications", sa.Column("email_sent_at", sa.DateTime(), nullable=True))
    op.add_column("notifications", sa.Column("email_last_attempt_at", sa.DateTime(), nullable=True))
    op.add_column(
        "notifications",
        sa.Column("email_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "notifications",
        sa.Column("email_last_error", sa.String(length=500), server_default="", nullable=False),
    )
    with op.batch_alter_table("reservations") as batch_op:
        batch_op.drop_constraint("ck_reservation_duration", type_="check")
        batch_op.create_check_constraint(
            "ck_reservation_duration", "duration_hours >= 1 AND duration_hours <= 24"
        )


def downgrade() -> None:
    connection = op.get_bind()
    long_bookings = connection.scalar(
        sa.text("SELECT COUNT(*) FROM reservations WHERE duration_hours > 4")
    )
    if long_bookings:
        raise RuntimeError("Cannot downgrade: reservations longer than four hours exist")
    with op.batch_alter_table("reservations") as batch_op:
        batch_op.drop_constraint("ck_reservation_duration", type_="check")
        batch_op.create_check_constraint(
            "ck_reservation_duration", "duration_hours >= 1 AND duration_hours <= 4"
        )
    op.drop_column("notifications", "email_last_error")
    op.drop_column("notifications", "email_attempts")
    op.drop_column("notifications", "email_last_attempt_at")
    op.drop_column("notifications", "email_sent_at")
    op.drop_column("notifications", "email_queued_at")
    op.drop_column("users", "email")
