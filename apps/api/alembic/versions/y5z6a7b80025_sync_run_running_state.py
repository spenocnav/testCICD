"""allow durable running state in sync_run

Revision ID: y5z6a7b80025
Revises: x4y5z6a70024
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "y5z6a7b80025"
down_revision: str | None = "x4y5z6a70024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_sync_run_status", "sync_run", type_="check")
    op.drop_constraint("ck_sync_run_duration_nonnegative", "sync_run", type_="check")
    op.drop_constraint("ck_sync_run_time_order", "sync_run", type_="check")

    op.alter_column(
        "sync_run",
        "finished_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
    op.alter_column(
        "sync_run",
        "duration_ms",
        existing_type=sa.Integer(),
        nullable=True,
    )

    op.create_check_constraint(
        "ck_sync_run_status",
        "sync_run",
        "status IN ('running', 'success', 'partial', 'error')",
    )
    op.create_check_constraint(
        "ck_sync_run_duration_nonnegative",
        "sync_run",
        "duration_ms IS NULL OR duration_ms >= 0",
    )
    op.create_check_constraint(
        "ck_sync_run_time_order",
        "sync_run",
        "finished_at IS NULL OR finished_at >= started_at",
    )
    op.create_check_constraint(
        "ck_sync_run_lifecycle",
        "sync_run",
        "(status = 'running' AND finished_at IS NULL AND duration_ms IS NULL) "
        "OR (status <> 'running' AND finished_at IS NOT NULL "
        "AND duration_ms IS NOT NULL)",
    )


def downgrade() -> None:
    # Una fila todavía en curso no cabe en el contrato anterior.
    op.execute("DELETE FROM sync_run WHERE status = 'running'")

    op.drop_constraint("ck_sync_run_lifecycle", "sync_run", type_="check")
    op.drop_constraint("ck_sync_run_status", "sync_run", type_="check")
    op.drop_constraint("ck_sync_run_duration_nonnegative", "sync_run", type_="check")
    op.drop_constraint("ck_sync_run_time_order", "sync_run", type_="check")

    op.alter_column(
        "sync_run",
        "finished_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    op.alter_column(
        "sync_run",
        "duration_ms",
        existing_type=sa.Integer(),
        nullable=False,
    )

    op.create_check_constraint(
        "ck_sync_run_status",
        "sync_run",
        "status IN ('success', 'partial', 'error')",
    )
    op.create_check_constraint(
        "ck_sync_run_duration_nonnegative",
        "sync_run",
        "duration_ms >= 0",
    )
    op.create_check_constraint(
        "ck_sync_run_time_order",
        "sync_run",
        "finished_at >= started_at",
    )
