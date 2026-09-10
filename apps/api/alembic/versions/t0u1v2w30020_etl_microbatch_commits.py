"""checkpoints transaccionales para micro-lotes del ETL

Revision ID: t0u1v2w30020
Revises: s9t0u1v20019
Create Date: 2026-07-23

Añade control optimista al estado de extracción y un ledger append-only para
que el ETL pueda confirmar hechos analíticos y watermarks en una sola
transacción idempotente.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "t0u1v2w30020"
down_revision: str | None = "s9t0u1v20019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "vehicle_extraction_state",
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
    )

    op.create_table(
        "etl_microbatch_commit",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vehicle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset", sa.String(length=64), nullable=False),
        sa.Column("analytics_vehicle_id", sa.String(length=64), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pipeline_version", sa.String(length=64), nullable=False),
        sa.Column("output_sha256", sa.String(length=64), nullable=False),
        sa.Column("raw_row_count", sa.BigInteger(), nullable=False),
        sa.Column("fact_row_count", sa.BigInteger(), nullable=False),
        sa.Column("comparison", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "committed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_etl_microbatch_vehicle_dataset_window",
        "etl_microbatch_commit",
        ["vehicle_id", "dataset", "window_start", "window_end"],
    )
    op.create_index(
        "ix_etl_microbatch_committed_at",
        "etl_microbatch_commit",
        ["committed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_etl_microbatch_committed_at", table_name="etl_microbatch_commit")
    op.drop_index(
        "ix_etl_microbatch_vehicle_dataset_window",
        table_name="etl_microbatch_commit",
    )
    op.drop_table("etl_microbatch_commit")
    op.drop_column("vehicle_extraction_state", "version")
