"""banda de rpm explicita en aplicaciones de regla

Revision ID: u1v2w3x40021
Revises: t0u1v2w30020
Create Date: 2026-07-24

Navi Vehículos pasa a declarar la banda de RPM de cada aplicación de regla
(`band`) y si mide el tramo en descenso (`is_descenso`), en vez de que el ETL la
infiera por palabras clave del nombre. `band` es nullable porque un snapshot
viejo no la trae y porque las aplicaciones `habito_seguro` no tienen banda.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "u1v2w3x40021"
down_revision: str | None = "t0u1v2w30020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BAND_VALUES = (
    "rango_bajo",
    "rango_economico",
    "rango_balanceado",
    "rango_potencia",
    "rango_potencia_ineficiente",
    "exceso_rpm",
    "ralenti",
)
_BAND_IN = ", ".join(f"'{value}'" for value in _BAND_VALUES)


def upgrade() -> None:
    op.add_column("geotab_rule_applications", sa.Column("band", sa.String(), nullable=True))
    op.add_column(
        "geotab_rule_applications",
        sa.Column("is_descenso", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_check_constraint(
        "ck_geotab_rule_app_band",
        "geotab_rule_applications",
        f"band IS NULL OR band IN ({_BAND_IN})",
    )
    # Ralentí no se mide en descenso (el transform no arma 'Ralentí Descenso').
    op.create_check_constraint(
        "ck_geotab_rule_app_ralenti_no_descenso",
        "geotab_rule_applications",
        "NOT (band = 'ralenti' AND is_descenso)",
    )
    # Un descenso sin banda no es interpretable: sería un sufijo sin dimensión.
    op.create_check_constraint(
        "ck_geotab_rule_app_descenso_needs_band",
        "geotab_rule_applications",
        "NOT (is_descenso AND band IS NULL)",
    )
    op.create_index(
        "ix_geotab_rule_applications_band",
        "geotab_rule_applications",
        ["band"],
    )


def downgrade() -> None:
    op.drop_index("ix_geotab_rule_applications_band", table_name="geotab_rule_applications")
    for name in (
        "ck_geotab_rule_app_descenso_needs_band",
        "ck_geotab_rule_app_ralenti_no_descenso",
        "ck_geotab_rule_app_band",
    ):
        op.drop_constraint(name, "geotab_rule_applications", type_="check")
    op.drop_column("geotab_rule_applications", "is_descenso")
    op.drop_column("geotab_rule_applications", "band")
