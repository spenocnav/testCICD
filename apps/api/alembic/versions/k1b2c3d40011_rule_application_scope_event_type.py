"""alcance por motor y tipo semantico en aplicaciones de regla

Revision ID: k1b2c3d40011
Revises: j0a1b2c30010
Create Date: 2026-06-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "k1b2c3d40011"
down_revision: str | None = "j0a1b2c30010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("geotab_rule_applications", sa.Column("event_type", sa.String(), nullable=True))
    op.create_index(
        "ix_geotab_rule_applications_event_type",
        "geotab_rule_applications",
        ["event_type"],
    )
    op.drop_constraint(
        "ck_geotab_rule_app_motor_by_category",
        "geotab_rule_applications",
        type_="check",
    )
    op.create_check_constraint(
        "ck_geotab_rule_app_motor_by_category",
        "geotab_rule_applications",
        "(category = 'operacion' AND motor_type IS NOT NULL) OR category = 'habito_seguro'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_geotab_rule_app_motor_by_category",
        "geotab_rule_applications",
        type_="check",
    )
    op.create_check_constraint(
        "ck_geotab_rule_app_motor_by_category",
        "geotab_rule_applications",
        "(category = 'operacion' AND motor_type IS NOT NULL) "
        "OR (category = 'habito_seguro' AND motor_type IS NULL)",
    )
    op.drop_index("ix_geotab_rule_applications_event_type", table_name="geotab_rule_applications")
    op.drop_column("geotab_rule_applications", "event_type")
