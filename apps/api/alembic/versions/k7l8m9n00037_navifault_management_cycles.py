"""Persist management cycles and response-time baseline for Navifault.

Revision ID: k7l8m9n00037
Revises: j6k7l8m90036
Create Date: 2026-08-25

Una misma firma puede gestionarse, revertirse por corrección y volver a
gestionarse sobre el mismo evento. La bitácora conserva esas decisiones; por
eso el checkpoint deja de ser único. Cada cierre gestionado almacena el inicio
de su ciclo para medir el tiempo de respuesta sin reinterpretar el histórico
de Geotab en cada consulta.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "k7l8m9n00037"
down_revision: str | None = "j6k7l8m90036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "navifault"
CASE_TABLE = "managed_fault_cases"
ACTION_TABLE = "managed_fault_actions"
CONFIGURATION_TABLE = "management_configuration"
CHECKPOINT_CONSTRAINT = "uq_navifault_managed_action_checkpoint"


def upgrade() -> None:
    op.drop_constraint(CHECKPOINT_CONSTRAINT, ACTION_TABLE, schema=SCHEMA, type_="unique")
    op.add_column(
        CASE_TABLE,
        sa.Column("current_cycle_started_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        ACTION_TABLE,
        sa.Column("cycle_started_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_table(
        CONFIGURATION_TABLE,
        sa.Column("singleton", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("tracking_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "singleton IS TRUE", name="ck_navifault_management_configuration_singleton"
        ),
        sa.PrimaryKeyConstraint("singleton"),
        schema=SCHEMA,
    )
    op.execute(
        sa.text(
            "INSERT INTO navifault.management_configuration "
            "(singleton, tracking_started_at) VALUES (TRUE, CURRENT_TIMESTAMP)"
        )
    )


def downgrade() -> None:
    op.drop_table(CONFIGURATION_TABLE, schema=SCHEMA)
    op.drop_column(ACTION_TABLE, "cycle_started_at", schema=SCHEMA)
    op.drop_column(CASE_TABLE, "current_cycle_started_at", schema=SCHEMA)
    # Si se gestionó de nuevo un checkpoint después de una reversión, Postgres
    # rechazará restaurar esta restricción: no se elimina evidencia para forzar
    # un downgrade destructivo.
    op.create_unique_constraint(
        CHECKPOINT_CONSTRAINT,
        ACTION_TABLE,
        ["case_id", "managed_through_row_id"],
        schema=SCHEMA,
    )
