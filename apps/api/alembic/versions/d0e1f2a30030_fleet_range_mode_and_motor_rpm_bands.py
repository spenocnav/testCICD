"""modo de rangos por flota y rangos de RPM por motor

Navi Vehículos ahora decide, por cliente, de dónde salen las bandas de RPM:
`reglas` (las reglas Geotab de siempre) o `rpm` (cortes del eje de revoluciones
definidos por motor). Esta migración replica ambas piezas del contrato:

- `fleets.range_mode`: el modo, con default `reglas` para que las flotas
  existentes conserven exactamente el comportamiento actual.
- `motor_rpm_bands`: la partición del eje de RPM por motor. `rpm_min` inclusivo,
  `rpm_max` exclusivo y NULL solo en la banda más alta. Un motor sin filas queda
  "sin configurar": el ETL debe saltarse esos vehículos, no inventar cortes.

Revision ID: d0e1f2a30030
Revises: c9d0e1f20029
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d0e1f2a30030"
down_revision: str | None = "c9d0e1f20029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RPM_RANGE_BANDS = (
    "rango_bajo",
    "rango_economico",
    "rango_balanceado",
    "rango_potencia",
    "rango_potencia_ineficiente",
    "exceso_rpm",
)


def upgrade() -> None:
    op.add_column(
        "fleets",
        sa.Column(
            "range_mode",
            sa.String(),
            nullable=False,
            server_default="reglas",
        ),
    )
    op.create_check_constraint(
        "ck_fleets_range_mode",
        "fleets",
        "range_mode IN ('reglas', 'rpm')",
    )

    op.create_table(
        "motor_rpm_bands",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "motor_type",
            sa.String(),
            sa.ForeignKey("motor_catalog.motor_type", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("band", sa.String(), nullable=False),
        sa.Column("rpm_min", sa.Integer(), nullable=False),
        sa.Column("rpm_max", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("motor_type", "band", name="uq_motor_rpm_band"),
        sa.CheckConstraint(
            "band IN (" + ", ".join(f"'{band}'" for band in _RPM_RANGE_BANDS) + ")",
            name="ck_motor_rpm_bands_band",
        ),
        sa.CheckConstraint("rpm_min >= 0", name="ck_motor_rpm_bands_min"),
        sa.CheckConstraint(
            "rpm_max IS NULL OR rpm_max > rpm_min", name="ck_motor_rpm_bands_max"
        ),
    )
    op.create_index(
        "ix_motor_rpm_bands_motor_type", "motor_rpm_bands", ["motor_type"]
    )


def downgrade() -> None:
    op.drop_index("ix_motor_rpm_bands_motor_type", table_name="motor_rpm_bands")
    op.drop_table("motor_rpm_bands")
    op.drop_constraint("ck_fleets_range_mode", "fleets", type_="check")
    op.drop_column("fleets", "range_mode")
