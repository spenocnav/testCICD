"""separar regla geotab fisica de sus aplicaciones

Revision ID: j0a1b2c30010
Revises: i9f6g8h00009
Create Date: 2026-06-23

Una regla fisica de Geotab puede clasificar simultaneamente como `operacion`
por motor y como `habito_seguro`. Se crea `geotab_rule_applications` para
guardar esas clasificaciones sin duplicar `geotab_rules.rule_id`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "j0a1b2c30010"
down_revision: str | None = "i9f6g8h00009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "geotab_rule_applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=True),
        sa.Column("geotab_rule_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("motor_type", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "category IN ('operacion', 'habito_seguro')",
            name="ck_geotab_rule_app_category",
        ),
        sa.CheckConstraint(
            "(category = 'operacion' AND motor_type IS NOT NULL) "
            "OR (category = 'habito_seguro' AND motor_type IS NULL)",
            name="ck_geotab_rule_app_motor_by_category",
        ),
        sa.ForeignKeyConstraint(["geotab_rule_id"], ["geotab_rules.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["motor_type"], ["motor_catalog.motor_type"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", name="uq_geotab_rule_applications_source_id"),
    )
    op.create_index(
        "ix_geotab_rule_applications_geotab_rule_id",
        "geotab_rule_applications",
        ["geotab_rule_id"],
    )
    op.create_index(
        "ix_geotab_rule_applications_category",
        "geotab_rule_applications",
        ["category"],
    )
    op.create_index(
        "ix_geotab_rule_applications_motor_type",
        "geotab_rule_applications",
        ["motor_type"],
    )
    op.create_index(
        "uq_geotab_rule_app_non_null_motor",
        "geotab_rule_applications",
        ["geotab_rule_id", "category", "motor_type"],
        unique=True,
        postgresql_where=sa.text("motor_type IS NOT NULL"),
    )
    op.create_index(
        "uq_geotab_rule_app_null_motor",
        "geotab_rule_applications",
        ["geotab_rule_id", "category"],
        unique=True,
        postgresql_where=sa.text("motor_type IS NULL"),
    )

    # source_id de la aplicacion = NULL: el id viejo (geotab_rules.source_id) es
    # el id de la regla fisica en Navi, NO el de su aplicacion. Las aplicaciones
    # adoptan su source_id (geotab_rule_applications.id de Navi) en el primer sync
    # por match de identidad logica. Copiarlo aqui colisionaba con ese id-space.
    op.execute(
        """
        INSERT INTO geotab_rule_applications (
            id, source_id, geotab_rule_id, category, motor_type, is_active,
            synced_at, created_at, updated_at
        )
        SELECT
            gen_random_uuid(), NULL, id, category, motor_type, is_active,
            synced_at, created_at, updated_at
        FROM geotab_rules
        """
    )

    # Si ya existian duplicados fisicos del mismo rule_id bajo la misma base,
    # todas sus aplicaciones pasan a la fila canonica y se elimina el duplicado.
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                FIRST_VALUE(id) OVER (
                    PARTITION BY geotab_database_id, rule_id ORDER BY created_at, id
                ) AS keeper_id,
                ROW_NUMBER() OVER (
                    PARTITION BY geotab_database_id, rule_id ORDER BY created_at, id
                ) AS rn
            FROM geotab_rules
        )
        UPDATE geotab_rule_applications app
        SET geotab_rule_id = ranked.keeper_id
        FROM ranked
        WHERE app.geotab_rule_id = ranked.id AND ranked.rn > 1
        """
    )
    op.execute(
        """
        DELETE FROM geotab_rules r
        USING (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY geotab_database_id, rule_id ORDER BY created_at, id
                ) AS rn
            FROM geotab_rules
        ) ranked
        WHERE r.id = ranked.id AND ranked.rn > 1
        """
    )

    op.drop_constraint("ck_geotab_rule_motor_by_category", "geotab_rules", type_="check")
    op.drop_constraint("ck_geotab_rule_category", "geotab_rules", type_="check")
    op.drop_constraint("uq_geotab_rule_db_rule_cat", "geotab_rules", type_="unique")
    op.create_unique_constraint(
        "uq_geotab_rule_db_rule",
        "geotab_rules",
        ["geotab_database_id", "rule_id"],
    )
    op.drop_constraint("geotab_rules_motor_type_fkey", "geotab_rules", type_="foreignkey")
    op.drop_index("ix_geotab_rules_motor_type", table_name="geotab_rules")
    op.drop_index("ix_geotab_rules_category", table_name="geotab_rules")
    op.drop_column("geotab_rules", "motor_type")
    op.drop_column("geotab_rules", "category")


def downgrade() -> None:
    op.add_column("geotab_rules", sa.Column("category", sa.String(), nullable=True))
    op.add_column("geotab_rules", sa.Column("motor_type", sa.String(), nullable=True))
    op.create_index("ix_geotab_rules_category", "geotab_rules", ["category"])
    op.create_index("ix_geotab_rules_motor_type", "geotab_rules", ["motor_type"])
    op.create_foreign_key(
        "geotab_rules_motor_type_fkey",
        "geotab_rules",
        "motor_catalog",
        ["motor_type"],
        ["motor_type"],
        ondelete="RESTRICT",
    )

    op.execute(
        """
        UPDATE geotab_rules r
        SET category = app.category,
            motor_type = app.motor_type
        FROM (
            SELECT DISTINCT ON (geotab_rule_id)
                geotab_rule_id, category, motor_type
            FROM geotab_rule_applications
            ORDER BY geotab_rule_id, created_at, id
        ) app
        WHERE r.id = app.geotab_rule_id
        """
    )
    op.execute("DELETE FROM geotab_rules WHERE category IS NULL")
    op.alter_column("geotab_rules", "category", nullable=False)

    op.drop_constraint("uq_geotab_rule_db_rule", "geotab_rules", type_="unique")
    op.create_unique_constraint(
        "uq_geotab_rule_db_rule_cat",
        "geotab_rules",
        ["geotab_database_id", "rule_id", "category", "motor_type"],
    )
    op.create_check_constraint(
        "ck_geotab_rule_category",
        "geotab_rules",
        "category IN ('operacion', 'habito_seguro')",
    )
    op.create_check_constraint(
        "ck_geotab_rule_motor_by_category",
        "geotab_rules",
        "(category = 'operacion' AND motor_type IS NOT NULL) "
        "OR (category = 'habito_seguro' AND motor_type IS NULL)",
    )

    op.drop_index("uq_geotab_rule_app_null_motor", table_name="geotab_rule_applications")
    op.drop_index("uq_geotab_rule_app_non_null_motor", table_name="geotab_rule_applications")
    op.drop_index(
        "ix_geotab_rule_applications_motor_type",
        table_name="geotab_rule_applications",
    )
    op.drop_index(
        "ix_geotab_rule_applications_category",
        table_name="geotab_rule_applications",
    )
    op.drop_index(
        "ix_geotab_rule_applications_geotab_rule_id",
        table_name="geotab_rule_applications",
    )
    op.drop_table("geotab_rule_applications")
