"""réplica del contrato de integración con Navi Vehículos

Revision ID: f6c3d5e70006
Revises: e5a1b2c30005
Create Date: 2026-06-12

Adapta la data maestra al contrato (Docs/contrato-intrgracion-portal-clientes.md):
`source_id`/`synced_at` para upserts idempotentes del sync, `database_key` (db
física compartible entre clientes; cae el unique global de nombre), credenciales
con pool LRU (`label`/`last_used_at`, muere `ordinal`), `geotab_rules` con
categoría operacion/habito_seguro (absorbe `db_event_rules`), vehículos con
`plate` como clave natural + campos del snapshot, y tabla `sync_state` para el
watermark. Preserva los datos ya sembrados.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "f6c3d5e70006"
down_revision: str | None = "e5a1b2c30005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- fleets = réplica de customers -------------------------------------
    op.add_column("fleets", sa.Column("source_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "fleets", sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_unique_constraint("uq_fleets_source_id", "fleets", ["source_id"])

    # --- geotab_databases ----------------------------------------------------
    op.alter_column("geotab_databases", "name", new_column_name="database_name")
    op.drop_index("ix_geotab_databases_name", table_name="geotab_databases")
    op.add_column(
        "geotab_databases", sa.Column("database_key", sa.String(), nullable=True)
    )
    op.execute("UPDATE geotab_databases SET database_key = LOWER(database_name)")
    op.alter_column("geotab_databases", "database_key", nullable=False)
    op.create_index("ix_geotab_databases_key", "geotab_databases", ["database_key"])
    op.add_column(
        "geotab_databases",
        sa.Column(
            "connection_type", sa.String(), nullable=False, server_default="geotab"
        ),
    )
    op.add_column(
        "geotab_databases", sa.Column("plate_prefix", sa.String(), nullable=True)
    )
    op.add_column(
        "geotab_databases", sa.Column("source_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "geotab_databases",
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint(
        "uq_geotab_databases_source_id", "geotab_databases", ["source_id"]
    )
    op.create_unique_constraint(
        "uq_geotab_db_fleet_name", "geotab_databases", ["fleet_id", "database_name"]
    )

    # --- geotab_credentials --------------------------------------------------
    op.add_column(
        "geotab_credentials", sa.Column("source_id", sa.BigInteger(), nullable=True)
    )
    op.add_column("geotab_credentials", sa.Column("label", sa.String(), nullable=True))
    op.add_column(
        "geotab_credentials",
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "geotab_credentials",
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint(
        "uq_geotab_credentials_source_id", "geotab_credentials", ["source_id"]
    )
    op.drop_constraint(
        "uq_credential_db_ordinal", "geotab_credentials", type_="unique"
    )
    op.drop_column("geotab_credentials", "ordinal")
    op.create_unique_constraint(
        "uq_credential_db_username",
        "geotab_credentials",
        ["geotab_database_id", "username"],
    )

    # --- geotab_rules (absorbe db_event_rules) -------------------------------
    op.create_table(
        "geotab_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_id", sa.BigInteger(), nullable=True, unique=True),
        sa.Column(
            "geotab_database_id",
            UUID(as_uuid=True),
            sa.ForeignKey("geotab_databases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rule_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "geotab_database_id", "rule_id", "category", name="uq_geotab_rule_db_rule_cat"
        ),
        sa.CheckConstraint(
            "category IN ('operacion', 'habito_seguro')", name="ck_geotab_rule_category"
        ),
    )
    op.create_index(
        "ix_geotab_rules_geotab_database_id", "geotab_rules", ["geotab_database_id"]
    )
    op.create_index("ix_geotab_rules_category", "geotab_rules", ["category"])
    op.execute(
        "INSERT INTO geotab_rules (id, geotab_database_id, rule_id, name, category, "
        "is_active, created_at, updated_at) "
        "SELECT id, geotab_database_id, rule_id, rule_name, 'habito_seguro', true, "
        "created_at, updated_at FROM db_event_rules"
    )
    op.drop_table("db_event_rules")

    # --- vehicles --------------------------------------------------------------
    op.alter_column("vehicles", "placa", new_column_name="plate")
    op.alter_column("vehicles", "device_id", new_column_name="geotab_device_id")
    op.drop_constraint("uq_vehicle_db_device", "vehicles", type_="unique")
    op.alter_column("vehicles", "geotab_device_id", nullable=True)
    op.alter_column("vehicles", "geotab_database_id", nullable=True)
    # FK de database pasa de CASCADE a SET NULL: el vehículo sobrevive a su base.
    op.drop_constraint(
        "vehicles_geotab_database_id_fkey", "vehicles", type_="foreignkey"
    )
    op.create_foreign_key(
        "vehicles_geotab_database_id_fkey",
        "vehicles",
        "geotab_databases",
        ["geotab_database_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("vehicles", sa.Column("vin", sa.String(), nullable=True))
    op.add_column(
        "vehicles",
        sa.Column("geotab_device_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "vehicles",
        sa.Column(
            "fleet_id",
            UUID(as_uuid=True),
            sa.ForeignKey("fleets.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "vehicles",
        sa.Column(
            "geotab_customer_status",
            sa.String(),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column("vehicles", sa.Column("engine_number", sa.String(), nullable=True))
    op.add_column("vehicles", sa.Column("technical_number", sa.String(), nullable=True))
    op.add_column("vehicles", sa.Column("cpl", sa.String(), nullable=True))
    op.add_column("vehicles", sa.Column("marca", sa.String(), nullable=True))
    op.add_column("vehicles", sa.Column("linea", sa.String(), nullable=True))
    op.add_column("vehicles", sa.Column("ano_modelo", sa.String(), nullable=True))
    op.add_column("vehicles", sa.Column("tipo_combustible", sa.String(), nullable=True))
    op.add_column("vehicles", sa.Column("nombre_vehiculo", sa.String(), nullable=True))
    op.add_column(
        "vehicles", sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Backfill: flota del vehículo = flota de su base geotab.
    op.execute(
        "UPDATE vehicles v SET fleet_id = gd.fleet_id "
        "FROM geotab_databases gd WHERE gd.id = v.geotab_database_id"
    )
    op.create_index("ix_vehicles_fleet_id", "vehicles", ["fleet_id"])
    op.create_unique_constraint("uq_vehicles_plate", "vehicles", ["plate"])

    # --- sync_state -------------------------------------------------------------
    op.create_table(
        "sync_state",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("watermark", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("sync_state")

    # vehicles
    op.drop_constraint("uq_vehicles_plate", "vehicles", type_="unique")
    op.drop_index("ix_vehicles_fleet_id", table_name="vehicles")
    for col in (
        "synced_at",
        "nombre_vehiculo",
        "tipo_combustible",
        "ano_modelo",
        "linea",
        "marca",
        "cpl",
        "technical_number",
        "engine_number",
        "geotab_customer_status",
        "fleet_id",
        "geotab_device_synced_at",
        "vin",
    ):
        op.drop_column("vehicles", col)
    op.execute("DELETE FROM vehicles WHERE geotab_database_id IS NULL")
    op.drop_constraint(
        "vehicles_geotab_database_id_fkey", "vehicles", type_="foreignkey"
    )
    op.create_foreign_key(
        "vehicles_geotab_database_id_fkey",
        "vehicles",
        "geotab_databases",
        ["geotab_database_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column("vehicles", "geotab_database_id", nullable=False)
    op.execute("DELETE FROM vehicles WHERE geotab_device_id IS NULL")
    op.alter_column("vehicles", "geotab_device_id", nullable=False)
    op.alter_column("vehicles", "geotab_device_id", new_column_name="device_id")
    op.alter_column("vehicles", "plate", new_column_name="placa")
    op.create_unique_constraint(
        "uq_vehicle_db_device", "vehicles", ["geotab_database_id", "device_id"]
    )

    # geotab_rules -> db_event_rules
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
    op.execute(
        "INSERT INTO db_event_rules (id, geotab_database_id, rule_name, rule_id, "
        "created_at, updated_at) "
        "SELECT id, geotab_database_id, name, rule_id, created_at, updated_at "
        "FROM geotab_rules WHERE category = 'habito_seguro'"
    )
    op.drop_table("geotab_rules")

    # geotab_credentials
    op.drop_constraint("uq_credential_db_username", "geotab_credentials", type_="unique")
    op.add_column(
        "geotab_credentials",
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="1"),
    )
    op.execute(
        "UPDATE geotab_credentials gc SET ordinal = sub.rn FROM ("
        "SELECT id, ROW_NUMBER() OVER (PARTITION BY geotab_database_id ORDER BY created_at, id) rn "
        "FROM geotab_credentials) sub WHERE sub.id = gc.id"
    )
    op.create_unique_constraint(
        "uq_credential_db_ordinal", "geotab_credentials", ["geotab_database_id", "ordinal"]
    )
    op.drop_constraint(
        "uq_geotab_credentials_source_id", "geotab_credentials", type_="unique"
    )
    for col in ("synced_at", "last_used_at", "label", "source_id"):
        op.drop_column("geotab_credentials", col)

    # geotab_databases
    op.drop_constraint("uq_geotab_db_fleet_name", "geotab_databases", type_="unique")
    op.drop_constraint(
        "uq_geotab_databases_source_id", "geotab_databases", type_="unique"
    )
    op.drop_index("ix_geotab_databases_key", table_name="geotab_databases")
    for col in ("synced_at", "source_id", "plate_prefix", "connection_type", "database_key"):
        op.drop_column("geotab_databases", col)
    op.alter_column("geotab_databases", "database_name", new_column_name="name")
    op.create_index(
        "ix_geotab_databases_name", "geotab_databases", ["name"], unique=True
    )

    # fleets
    op.drop_constraint("uq_fleets_source_id", "fleets", type_="unique")
    op.drop_column("fleets", "synced_at")
    op.drop_column("fleets", "source_id")
