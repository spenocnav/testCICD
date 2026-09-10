"""Calibración de la calificación por flota, append-only.

Revision ID: p2q3r4s50042
Revises: o1p2q3r40041
Create Date: 2026-08-27

La fórmula de la calificación vivía en constantes de módulo. Cada flota puede
recalibrar ahora los parámetros que dependen de SU operación. Se guarda una fila
por cada guardado —la última por flota gana— porque el dato cambia el puntaje de
toda la flota y la auditoría de quién lo cambió tiene que ser gratis.

Todas las columnas de parámetros son nullable y sin `server_default`: una fila
escrita antes de añadir un parámetro nuevo debe seguir siendo legible, y el
lector cae al default del código. Un `server_default` congelaría la calibración
vigente dentro de cada fila.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "p2q3r4s50042"
down_revision: str | None = "o1p2q3r40041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "calificacion_config_versions"
FLEET_INDEX = "ix_calificacion_config_fleet_created"

_UNIT_INTERVAL_FIELDS = (
    "peso_qhs",
    "peso_qho",
    "peso_eficiente",
    "peso_ralenti",
    "peso_exceso_rpm",
    "ralenti_target",
    "ralenti_max",
)

_POSITIVE_FIELDS = (
    "eventos_cap",
    "rpm_cap_comercial_1000km",
    "rpm_cap_vocacional_100h",
)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fleet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("peso_qhs", sa.Float(), nullable=True),
        sa.Column("peso_qho", sa.Float(), nullable=True),
        sa.Column("peso_eficiente", sa.Float(), nullable=True),
        sa.Column("peso_ralenti", sa.Float(), nullable=True),
        sa.Column("peso_exceso_rpm", sa.Float(), nullable=True),
        sa.Column("efic_target", sa.Float(), nullable=True),
        sa.Column("ralenti_target", sa.Float(), nullable=True),
        sa.Column("ralenti_max", sa.Float(), nullable=True),
        sa.Column("eventos_cap", sa.Float(), nullable=True),
        sa.Column("rpm_cap_comercial_1000km", sa.Float(), nullable=True),
        sa.Column("rpm_cap_vocacional_100h", sa.Float(), nullable=True),
        sa.Column("rpm_high_weight", sa.Float(), nullable=True),
        sa.Column("umbral_en_riesgo", sa.Float(), nullable=True),
        sa.Column("umbral_cumple", sa.Float(), nullable=True),
        sa.Column("qhs_event_weights", postgresql.JSONB(), nullable=True),
        sa.Column("qhs_default_weight", sa.Float(), nullable=True),
        sa.Column(
            "is_reset",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_email", sa.String(length=320), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *(
            sa.CheckConstraint(
                f"{name} IS NULL OR ({name} >= 0 AND {name} <= 1)",
                name=f"ck_calificacion_config_{name}_unit",
            )
            for name in _UNIT_INTERVAL_FIELDS
        ),
        *(
            sa.CheckConstraint(
                f"{name} IS NULL OR {name} > 0",
                name=f"ck_calificacion_config_{name}_positive",
            )
            for name in _POSITIVE_FIELDS
        ),
        sa.CheckConstraint(
            "efic_target IS NULL OR (efic_target > 0 AND efic_target <= 1)",
            name="ck_calificacion_config_efic_target",
        ),
        sa.CheckConstraint(
            "ralenti_target IS NULL OR ralenti_max IS NULL OR ralenti_target < ralenti_max",
            name="ck_calificacion_config_ralenti_orden",
        ),
        sa.CheckConstraint(
            "rpm_high_weight IS NULL OR rpm_high_weight >= 1",
            name="ck_calificacion_config_rpm_high_weight",
        ),
        sa.CheckConstraint(
            "umbral_en_riesgo IS NULL OR (umbral_en_riesgo >= 0 AND umbral_en_riesgo <= 100)",
            name="ck_calificacion_config_umbral_en_riesgo",
        ),
        sa.CheckConstraint(
            "umbral_cumple IS NULL OR (umbral_cumple > 0 AND umbral_cumple <= 100)",
            name="ck_calificacion_config_umbral_cumple",
        ),
        sa.CheckConstraint(
            "umbral_en_riesgo IS NULL OR umbral_cumple IS NULL OR umbral_en_riesgo < umbral_cumple",
            name="ck_calificacion_config_umbral_orden",
        ),
        sa.CheckConstraint(
            "qhs_default_weight IS NULL OR qhs_default_weight >= 0",
            name="ck_calificacion_config_qhs_default_weight",
        ),
        sa.ForeignKeyConstraint(["fleet_id"], ["fleets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    # "La última por flota" se resuelve sin ordenar; cubre también el filtro
    # por `fleet_id` solo, así que la columna no lleva índice propio.
    op.create_index(
        FLEET_INDEX,
        TABLE,
        ["fleet_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(FLEET_INDEX, table_name=TABLE)
    op.drop_table(TABLE)
