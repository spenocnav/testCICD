"""cola de disparo del ETL de reportes (etl_trigger_request)

Revision ID: s9t0u1v20019
Revises: r8s9t0u10018
Create Date: 2026-07-23

Coordina el disparo manual del ETL InformesRendimiento desde el portal:
el endpoint inserta `pending`, el worker `reportes-etl-worker` reclama y
ejecuta. Ver app/models/etl_trigger.py.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "s9t0u1v20019"
down_revision: str | None = "r8s9t0u10018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "etl_trigger_request",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requested_by_email", sa.String(length=320), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_etl_trigger_request_status", "etl_trigger_request", ["status"])
    op.create_index(
        "ix_etl_trigger_request_status_created",
        "etl_trigger_request",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_etl_trigger_request_status_created", table_name="etl_trigger_request")
    op.drop_index("ix_etl_trigger_request_status", table_name="etl_trigger_request")
    op.drop_table("etl_trigger_request")
