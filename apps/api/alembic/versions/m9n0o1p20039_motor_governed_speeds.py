"""velocidades de placa por motor

Navi Vehículos ahora publica dos datos de la hoja técnica del fabricante en cada
entrada de `motors[]` del snapshot (contrato §2.6):

- `governed_speed_rpm`: velocidad nominal gobernada sin carga (X13E6 = 2100).
- `max_overspeed_rpm`: capacidad máxima de sobrevelocidad (X13E6 = 2250).

Ambas son nullable: NULL significa "aún no capturado en la fuente", nunca 0, y no
dependen de `range_mode` — viajan para todos los motores.

Revision ID: m9n0o1p20039
Revises: l8m9n0o10038
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "m9n0o1p20039"
down_revision: str | None = "l8m9n0o10038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "motor_catalog",
        sa.Column("governed_speed_rpm", sa.Integer(), nullable=True),
    )
    op.add_column(
        "motor_catalog",
        sa.Column("max_overspeed_rpm", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_motor_catalog_governed_speeds",
        "motor_catalog",
        "(governed_speed_rpm IS NULL OR governed_speed_rpm > 0) "
        "AND (max_overspeed_rpm IS NULL OR max_overspeed_rpm > 0) "
        "AND (governed_speed_rpm IS NULL OR max_overspeed_rpm IS NULL "
        "OR max_overspeed_rpm >= governed_speed_rpm)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_motor_catalog_governed_speeds", "motor_catalog", type_="check")
    op.drop_column("motor_catalog", "max_overspeed_rpm")
    op.drop_column("motor_catalog", "governed_speed_rpm")
