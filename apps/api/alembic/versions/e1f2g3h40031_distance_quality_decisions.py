"""add append-only distance quality decisions

Revision ID: e1f2g3h40031
Revises: d0e1f2a30030
Create Date: 2026-08-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1f2g3h40031"
down_revision: str | None = "d0e1f2a30030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "distance_quality_decisions",
        sa.Column("fact_row_id", sa.String(length=128), nullable=False),
        sa.Column("analytics_vehicle_id", sa.String(length=128), nullable=False),
        sa.Column("date_key", sa.BigInteger(), nullable=False),
        sa.Column("observation_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("justification", sa.Text(), nullable=True),
        sa.Column("observed_kms_ecm", sa.Float(), nullable=True),
        sa.Column("observed_kms_gps", sa.Float(), nullable=True),
        sa.Column("threshold_version", sa.String(length=32), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_email", sa.String(length=320), nullable=True),
        sa.Column("dedupe_key", sa.String(length=128), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "origin IN ('automatic', 'manual')", name="ck_distance_decision_origin"
        ),
        sa.CheckConstraint(
            "action IN ('use_ecm', 'use_gps', 'exclude', 'restore_auto')",
            name="ck_distance_decision_action",
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index(
        "ix_distance_decision_fact_created",
        "distance_quality_decisions",
        ["fact_row_id", "created_at"],
    )
    op.create_index(
        "ix_distance_decision_vehicle_date",
        "distance_quality_decisions",
        ["analytics_vehicle_id", "date_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_distance_decision_vehicle_date", table_name="distance_quality_decisions"
    )
    op.drop_index(
        "ix_distance_decision_fact_created", table_name="distance_quality_decisions"
    )
    op.drop_table("distance_quality_decisions")
