"""estado durable de lecturas enviadas a CloudFleet

Revision ID: a7b8c9d00027
Revises: z6a7b8c90026
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7b8c9d00027"
down_revision: str | None = "z6a7b8c90026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cloudfleet_meter_sync_state",
        sa.Column("vehicle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("meter_type", sa.String(length=16), nullable=False),
        sa.Column("meter_value", sa.Numeric(precision=16, scale=3), nullable=False),
        sa.Column("meter_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "meter_type IN ('distance', 'hours')",
            name="ck_cf_meter_state_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed', 'uncertain')",
            name="ck_cf_meter_state_status",
        ),
        sa.CheckConstraint("meter_value >= 0", name="ck_cf_meter_state_value_nonnegative"),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vehicle_id", "meter_type", name="uq_cf_meter_state_vehicle_type"),
    )
    op.create_index(
        "ix_cf_meter_state_status",
        "cloudfleet_meter_sync_state",
        ["status", "updated_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cloudfleet_meter_sync_state_vehicle_id"),
        "cloudfleet_meter_sync_state",
        ["vehicle_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_cloudfleet_meter_sync_state_vehicle_id"),
        table_name="cloudfleet_meter_sync_state",
    )
    op.drop_index("ix_cf_meter_state_status", table_name="cloudfleet_meter_sync_state")
    op.drop_table("cloudfleet_meter_sync_state")
