"""add private Navifault fault-management lifecycle

Revision ID: h4i5j6k70034
Revises: g3h4i5j60033
Create Date: 2026-08-25

Los hechos Geotab permanecen en ``analytics``. Estas tablas privadas guardan
solamente la decisión humana y su marca de agua para que una ocurrencia posterior
del mismo vehículo y firma se presente como repetida.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "h4i5j6k70034"
down_revision: str | None = "g3h4i5j60033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "navifault"
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "managed_fault_cases",
        sa.Column("case_id", UUID, nullable=False),
        sa.Column("vehicle_id", UUID, nullable=False),
        sa.Column("signature_sha256", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("diagnostic_code", sa.Integer(), nullable=True),
        sa.Column("failure_mode", sa.Float(), nullable=True),
        sa.Column("stop_amber", sa.Boolean(), nullable=True),
        sa.Column("stop_red", sa.Boolean(), nullable=True),
        sa.Column("malfunction", sa.Boolean(), nullable=True),
        sa.Column("warning", sa.Boolean(), nullable=True),
        sa.Column("managed_through_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("managed_through_row_id", sa.String(length=96), nullable=True),
        sa.Column("last_managed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_managed_by_user_id", UUID, nullable=False),
        sa.Column("last_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["last_managed_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("case_id"),
        sa.UniqueConstraint("vehicle_id", "signature_sha256", name="uq_navifault_managed_case"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_navifault_managed_case_vehicle",
        "managed_fault_cases",
        ["vehicle_id", "last_managed_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "managed_fault_actions",
        sa.Column("action_id", UUID, nullable=False),
        sa.Column("case_id", UUID, nullable=False),
        sa.Column("action_type", sa.String(length=24), nullable=False, server_default="managed"),
        sa.Column("managed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("managed_through_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("managed_through_row_id", sa.String(length=96), nullable=True),
        sa.Column("actor_user_id", UUID, nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint("action_type IN ('managed')", name="ck_navifault_managed_action_type"),
        sa.ForeignKeyConstraint(
            ["case_id"], [f"{SCHEMA}.managed_fault_cases.case_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("action_id"),
        sa.UniqueConstraint(
            "case_id", "managed_through_row_id", name="uq_navifault_managed_action_checkpoint"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_navifault_managed_action_case_at",
        "managed_fault_actions",
        ["case_id", "managed_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_navifault_managed_action_case_at",
        table_name="managed_fault_actions",
        schema=SCHEMA,
    )
    op.drop_table("managed_fault_actions", schema=SCHEMA)
    op.drop_index(
        "ix_navifault_managed_case_vehicle",
        table_name="managed_fault_cases",
        schema=SCHEMA,
    )
    op.drop_table("managed_fault_cases", schema=SCHEMA)
