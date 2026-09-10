"""reglas de operación por tipo de motor

Revision ID: g7d4e6f80007
Revises: f6c3d5e70006
Create Date: 2026-06-16

Una db física puede tener vehículos de varios tipos de motor, y las reglas de
`operacion` aplican a UN motor. Se agrega `geotab_rules.motor_type` (FK al
catálogo controlado `motor_catalog`, mismo vocabulario que `vehicles.motor_type`)
para que la regla aplicable a un vehículo cruce por su motor. `habito_seguro`
mantiene `motor_type` NULL (aplica a toda la db).

Cambios:
- `geotab_rules.motor_type` nullable, FK motor_catalog(motor_type) ON DELETE RESTRICT.
- unique pasa de (db, rule_id, category) a (db, rule_id, category, motor_type).
- check `ck_geotab_rule_motor_by_category`: operacion => motor NOT NULL,
  habito_seguro => motor NULL.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "g7d4e6f80007"
down_revision: str | None = "f6c3d5e70006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("geotab_rules", sa.Column("motor_type", sa.String(), nullable=True))
    op.create_index(
        "ix_geotab_rules_motor_type", "geotab_rules", ["motor_type"]
    )
    op.create_foreign_key(
        "geotab_rules_motor_type_fkey",
        "geotab_rules",
        "motor_catalog",
        ["motor_type"],
        ["motor_type"],
        ondelete="RESTRICT",
    )
    # El unique ahora incluye motor_type (operacion del mismo rule_id por motor).
    op.drop_constraint("uq_geotab_rule_db_rule_cat", "geotab_rules", type_="unique")
    op.create_unique_constraint(
        "uq_geotab_rule_db_rule_cat",
        "geotab_rules",
        ["geotab_database_id", "rule_id", "category", "motor_type"],
    )
    # Las reglas `operacion` previas a esta migración no tienen motor (el concepto
    # no existía) y son inválidas bajo el nuevo modelo: se eliminan para que el
    # sync desde Navi Vehículos las reponga ya con `motor_type`. Datos demo/réplica,
    # reproducibles vía seed/sync; no hay verdad que perder aquí.
    op.execute(
        "DELETE FROM geotab_rules WHERE category = 'operacion' AND motor_type IS NULL"
    )
    op.create_check_constraint(
        "ck_geotab_rule_motor_by_category",
        "geotab_rules",
        "(category = 'operacion' AND motor_type IS NOT NULL) "
        "OR (category = 'habito_seguro' AND motor_type IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_geotab_rule_motor_by_category", "geotab_rules", type_="check"
    )
    op.drop_constraint("uq_geotab_rule_db_rule_cat", "geotab_rules", type_="unique")
    op.create_unique_constraint(
        "uq_geotab_rule_db_rule_cat",
        "geotab_rules",
        ["geotab_database_id", "rule_id", "category"],
    )
    op.drop_constraint(
        "geotab_rules_motor_type_fkey", "geotab_rules", type_="foreignkey"
    )
    op.drop_index("ix_geotab_rules_motor_type", table_name="geotab_rules")
    op.drop_column("geotab_rules", "motor_type")
