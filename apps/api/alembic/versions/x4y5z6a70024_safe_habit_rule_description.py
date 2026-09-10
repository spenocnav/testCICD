"""descripción semántica de reglas de hábitos seguros

Revision ID: x4y5z6a70024
Revises: w3x4y5z60023
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "x4y5z6a70024"
down_revision: str | None = "w3x4y5z60023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DESCRIPTIONS = (
    "Excesos de velocidad",
    "Giros bruscos",
    "Excesos de RPM",
    "Frenadas bruscas",
    "Baches o Resaltos fuertes",
    "Aceleraciones bruscas",
)
_DESCRIPTION_IN = ", ".join(f"'{value}'" for value in _DESCRIPTIONS)


def upgrade() -> None:
    op.add_column(
        "geotab_rule_applications",
        sa.Column("description", sa.String(), nullable=True),
    )
    op.create_check_constraint(
        "ck_geotab_rule_app_description_habito_only",
        "geotab_rule_applications",
        "description IS NULL OR category = 'habito_seguro'",
    )
    op.create_check_constraint(
        "ck_geotab_rule_app_description",
        "geotab_rule_applications",
        f"description IS NULL OR description IN ({_DESCRIPTION_IN})",
    )
    # Clasifica las reglas ya replicadas. El nombre físico se usa únicamente
    # para el backfill inicial; los siguientes syncs reciben `description`.
    op.execute(
        sa.text(
            """
            UPDATE geotab_rule_applications AS app
            SET description = CASE
                WHEN rule.name ILIKE '%exceso%velocidad%' THEN 'Excesos de velocidad'
                WHEN rule.name ILIKE '%giro%' THEN 'Giros bruscos'
                WHEN app.event_type = 'exceso_rpm' THEN 'Excesos de RPM'
                WHEN rule.name ILIKE '%frenada%' THEN 'Frenadas bruscas'
                WHEN rule.name ILIKE '%bache%' OR rule.name ILIKE '%resalto%'
                    THEN 'Baches o Resaltos fuertes'
                WHEN rule.name ILIKE '%aceleracion%'
                    OR rule.name ILIKE '%aceleración%'
                    THEN 'Aceleraciones bruscas'
                ELSE app.description
            END
            FROM geotab_rules AS rule
            WHERE rule.id = app.geotab_rule_id
              AND app.category = 'habito_seguro'
              AND app.description IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_geotab_rule_app_description",
        "geotab_rule_applications",
        type_="check",
    )
    op.drop_constraint(
        "ck_geotab_rule_app_description_habito_only",
        "geotab_rule_applications",
        type_="check",
    )
    op.drop_column("geotab_rule_applications", "description")
