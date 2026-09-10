"""Store verifiable repair records for Navifault fault cases.

Revision ID: l8m9n0o10038
Revises: k7l8m9n00037
Create Date: 2026-08-26

Los registros de reparación son evidencia humana privada. La efectividad se
calcula contra Geotab durante 30 días al consultar; no se reescribe el hecho
original ni se afirma una causa Cummins cuando el match es ambiguo.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "l8m9n0o10038"
down_revision: str | None = "k7l8m9n00037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "navifault"
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "repair_records",
        sa.Column("repair_id", UUID, nullable=False),
        sa.Column("case_id", UUID, nullable=False),
        sa.Column("managed_action_id", UUID, nullable=False),
        sa.Column("fault_page_id", sa.String(length=96), nullable=True),
        sa.Column("repair_action", sa.String(length=240), nullable=False),
        sa.Column("repair_key", sa.String(length=256), nullable=False),
        sa.Column("component_name", sa.String(length=240), nullable=True),
        sa.Column("repair_detail", sa.Text(), nullable=True),
        sa.Column("repaired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], [f"{SCHEMA}.managed_fault_cases.case_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["managed_action_id"],
            [f"{SCHEMA}.managed_fault_actions.action_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fault_page_id"], [f"{SCHEMA}.fault_pages.fault_page_id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("repair_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_navifault_repair_case_at",
        "repair_records",
        ["case_id", "repaired_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_navifault_repair_page_key",
        "repair_records",
        ["fault_page_id", "repair_key"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_navifault_repair_page_key", table_name="repair_records", schema=SCHEMA)
    op.drop_index("ix_navifault_repair_case_at", table_name="repair_records", schema=SCHEMA)
    op.drop_table("repair_records", schema=SCHEMA)
