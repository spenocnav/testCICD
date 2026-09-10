"""estado de extraccion por vehiculo y dataset

Revision ID: l2c3d4e50012
Revises: k1b2c3d40011
Create Date: 2026-06-24

Mueve el watermark de extraccion (antes `estado_ejecucion.json` global por
dataset en InformesRendimiento) a la DB del Portal, por `(vehiculo, dataset)`,
para poder backfillear un vehiculo nuevo sin re-procesar a toda la flota.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "l2c3d4e50012"
down_revision: str | None = "k1b2c3d40011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vehicle_extraction_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vehicle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset", sa.String(), nullable=False),
        sa.Column("watermark", sa.DateTime(timezone=True), nullable=True),
        sa.Column("backfill_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "vehicle_id", "dataset", name="uq_vehicle_extraction_vehicle_dataset"
        ),
    )
    op.create_index(
        "ix_vehicle_extraction_state_vehicle_id",
        "vehicle_extraction_state",
        ["vehicle_id"],
    )
    op.create_index(
        "ix_vehicle_extraction_state_dataset",
        "vehicle_extraction_state",
        ["dataset"],
    )


def downgrade() -> None:
    op.drop_index("ix_vehicle_extraction_state_dataset", table_name="vehicle_extraction_state")
    op.drop_index("ix_vehicle_extraction_state_vehicle_id", table_name="vehicle_extraction_state")
    op.drop_table("vehicle_extraction_state")
