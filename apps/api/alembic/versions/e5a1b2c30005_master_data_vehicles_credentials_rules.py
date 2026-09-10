"""data maestra: bases geotab, credenciales, vehículos y reglas

Revision ID: e5a1b2c30005
Revises: d4f8h2c60004
Create Date: 2026-06-04

Crea las tablas de data maestra del pipeline InformesRendimiento (input del
ETL), editables desde el portal: bases geotab por flota, credenciales cifradas,
catálogo de motores, reglas por motor/base/clase RPM y vehículos. Solo DDL; el
seed con los valores actuales va en un script aparte (idempotente).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "e5a1b2c30005"
down_revision: str | None = "d4f8h2c60004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Catálogo de motores (global).
    op.create_table(
        "motor_catalog",
        sa.Column("motor_type", sa.String(), primary_key=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # Bases geotab por flota (cliente 1:N bases).
    op.create_table(
        "geotab_databases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "fleet_id",
            UUID(as_uuid=True),
            sa.ForeignKey("fleets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_geotab_databases_fleet_id", "geotab_databases", ["fleet_id"])
    op.create_index("ix_geotab_databases_name", "geotab_databases", ["name"], unique=True)

    # Credenciales geotab (clave cifrada Fernet).
    op.create_table(
        "geotab_credentials",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "geotab_database_id",
            UUID(as_uuid=True),
            sa.ForeignKey("geotab_databases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("password_enc", sa.LargeBinary(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("geotab_database_id", "ordinal", name="uq_credential_db_ordinal"),
    )
    op.create_index(
        "ix_geotab_credentials_geotab_database_id",
        "geotab_credentials",
        ["geotab_database_id"],
    )

    # Reglas de combustible por motor (= RULES_BY_MOTOR).
    op.create_table(
        "motor_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "motor_type",
            sa.String(),
            sa.ForeignKey("motor_catalog.motor_type", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rule_name", sa.String(), nullable=False),
        sa.Column("rule_id", sa.String(), nullable=False),
        sa.Column("categoria", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("motor_type", "rule_name", name="uq_motor_rule_name"),
    )
    op.create_index("ix_motor_rules_motor_type", "motor_rules", ["motor_type"])

    # Reglas de evento (hábitos seguros) por base geotab (= EVENT_RULES).
    op.create_table(
        "db_event_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "geotab_database_id",
            UUID(as_uuid=True),
            sa.ForeignKey("geotab_databases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rule_name", sa.String(), nullable=False),
        sa.Column("rule_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("geotab_database_id", "rule_name", name="uq_event_rule_name"),
    )
    op.create_index(
        "ix_db_event_rules_geotab_database_id", "db_event_rules", ["geotab_database_id"]
    )

    # Reglas de exceso RPM por clase RPM (= RPM_RULES).
    op.create_table(
        "rpm_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("rpm_class", sa.String(), nullable=False),
        sa.Column("rule_id", sa.String(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("rpm_class", "ordinal", name="uq_rpm_rule_class_ordinal"),
    )
    op.create_index("ix_rpm_rules_rpm_class", "rpm_rules", ["rpm_class"])

    # Vehículos por base geotab (= VEHICLE_CATALOG + volumen de tanque).
    op.create_table(
        "vehicles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "geotab_database_id",
            UUID(as_uuid=True),
            sa.ForeignKey("geotab_databases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("placa", sa.String(), nullable=False),
        sa.Column(
            "motor_type",
            sa.String(),
            sa.ForeignKey("motor_catalog.motor_type", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("group_key", sa.String(), nullable=True),
        sa.Column("rpm_class", sa.String(), nullable=True),
        sa.Column("tank_volume", sa.Float(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("geotab_database_id", "device_id", name="uq_vehicle_db_device"),
    )
    op.create_index("ix_vehicles_geotab_database_id", "vehicles", ["geotab_database_id"])
    op.create_index("ix_vehicles_motor_type", "vehicles", ["motor_type"])


def downgrade() -> None:
    op.drop_table("vehicles")
    op.drop_table("rpm_rules")
    op.drop_table("db_event_rules")
    op.drop_table("motor_rules")
    op.drop_table("geotab_credentials")
    op.drop_table("geotab_databases")
    op.drop_table("motor_catalog")
