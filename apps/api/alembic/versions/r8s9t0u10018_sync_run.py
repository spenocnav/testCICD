"""auditoría de corridas de sync (tabla sync_run)

Revision ID: r8s9t0u10018
Revises: c3d4e5f6a7b9
Create Date: 2026-07-23

Historial append-only de pasadas de sincronización (réplica CloudFleet y
snapshot maestro). Ver app/models/sync_run.py.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "r8s9t0u10018"
down_revision: str | None = "c3d4e5f6a7b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sync_run",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_email", sa.String(length=320), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_run_kind", "sync_run", ["kind"])
    op.create_index("ix_sync_run_status", "sync_run", ["status"])
    op.create_index(
        "ix_sync_run_started_at", "sync_run", [sa.text("started_at DESC")]
    )
    op.create_index(
        "ix_sync_run_kind_started_at",
        "sync_run",
        ["kind", sa.text("started_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_sync_run_kind_started_at", table_name="sync_run")
    op.drop_index("ix_sync_run_started_at", table_name="sync_run")
    op.drop_index("ix_sync_run_status", table_name="sync_run")
    op.drop_index("ix_sync_run_kind", table_name="sync_run")
    op.drop_table("sync_run")
