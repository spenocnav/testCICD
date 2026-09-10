"""Análisis de Ralentí por flota.

`fleets.ralenti_analysis_enabled` decide si el ETL extrae y el portal muestra el
módulo "Análisis Ralentí" de Reportes para esa flota. Nace apagado para todas:
la extracción cuesta llamadas a Geotab por vehículo y día, y sólo la paga quien
la pidió. Se activa desde /gestion/flotas.

Revision ID: f9a0b1c20058
Revises: e8f9a0b10057
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f9a0b1c20058"
down_revision: str | None = "e8f9a0b10057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fleets",
        sa.Column(
            "ralenti_analysis_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("fleets", "ralenti_analysis_enabled")
