"""reservation idempotency

Revision ID: bde575f280e9
Revises: 24d3005ea3f6
Create Date: 2026-11-03 22:43:40.710532
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "bde575f280e9"
down_revision = "24d3005ea3f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("reservations", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=120), nullable=True))
        batch_op.create_unique_constraint(
            "uq_reservation_idempotency", ["user_id", "idempotency_key"]
        )


def downgrade() -> None:
    with op.batch_alter_table("reservations", recreate="always") as batch_op:
        batch_op.drop_constraint("uq_reservation_idempotency", type_="unique")
        batch_op.drop_column("idempotency_key")
