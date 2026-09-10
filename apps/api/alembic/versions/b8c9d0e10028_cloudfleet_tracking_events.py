"""persist normalized CloudFleet tracking observations

Revision ID: b8c9d0e10028
Revises: a7b8c9d00027
Create Date: 2026-08-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b8c9d0e10028"
down_revision: str | None = "a7b8c9d00027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cloudfleet_work_orders",
        sa.Column(
            "maintenance_labels",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    op.create_table(
        "cloudfleet_tracking_events",
        sa.Column("work_order_number", sa.BigInteger(), nullable=False),
        sa.Column("tracking_key", sa.String(length=128), nullable=False),
        sa.Column("tracking_id", sa.String(length=128), nullable=True),
        sa.Column("tracking_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tracking_comment", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.String(length=128), nullable=True),
        sa.Column("created_by_name", sa.String(length=320), nullable=True),
        sa.Column("vehicle_code", sa.String(), nullable=True),
        sa.Column("client", sa.String(), nullable=True),
        sa.Column("cd", sa.String(), nullable=True),
        sa.Column("work_order_status", sa.String(), nullable=True),
        sa.Column("work_order_type", sa.String(), nullable=True),
        sa.Column("label_id", sa.Integer(), nullable=True),
        sa.Column("label_name", sa.String(), nullable=True),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_at_source", sa.String(length=40), nullable=True),
        sa.Column("event_time_quality", sa.String(length=40), nullable=True),
        sa.Column("tracking_observed_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tracking_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tracking_observation_kind", sa.String(length=64), nullable=True),
        sa.Column("observation_window_minutes", sa.Float(), nullable=True),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "work_order_number",
            "tracking_key",
            name="uq_cf_tracking_event_order_key",
        ),
    )
    op.create_index(
        op.f("ix_cloudfleet_tracking_events_work_order_number"),
        "cloudfleet_tracking_events",
        ["work_order_number"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cloudfleet_tracking_events_vehicle_code"),
        "cloudfleet_tracking_events",
        ["vehicle_code"],
        unique=False,
    )
    op.create_index(
        "ix_cf_tracking_event_order_event_at",
        "cloudfleet_tracking_events",
        ["work_order_number", "event_at"],
        unique=False,
    )
    op.create_index(
        "ix_cf_tracking_event_label_event_at",
        "cloudfleet_tracking_events",
        ["label_id", "event_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cf_tracking_event_label_event_at",
        table_name="cloudfleet_tracking_events",
    )
    op.drop_index(
        "ix_cf_tracking_event_order_event_at",
        table_name="cloudfleet_tracking_events",
    )
    op.drop_index(
        op.f("ix_cloudfleet_tracking_events_vehicle_code"),
        table_name="cloudfleet_tracking_events",
    )
    op.drop_index(
        op.f("ix_cloudfleet_tracking_events_work_order_number"),
        table_name="cloudfleet_tracking_events",
    )
    op.drop_table("cloudfleet_tracking_events")
    op.drop_column("cloudfleet_work_orders", "maintenance_labels")
