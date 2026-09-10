"""réplica de los grupos internos de vehículos por flota (categorías/subcategorías)

Revision ID: y2z3a4b50051
Revises: x1y2z3a40050
Create Date: 2026-08-31

Navi Vehículos permite que cada cliente organice su flota en grupos internos
(ej. Bavaria: Regional -> CEDI). El árbol viaja en el snapshot maestro como
`customers[].groups` (plano, con `parent_id`) y cada vehículo trae su
`customer_group_id`. Esta tabla es la réplica local; `vehicles` gana la FK.

Reglas del diseño:
- `source_id` es la identidad del upsert, igual que en el resto de réplicas.
- `parent_id` es autorreferente y nullable: NULL = categoría raíz.
- Un grupo que desaparece del origen se DESACTIVA, no se borra: los vehículos
  y filtros guardados no deben romperse por una baja en el maestro.
- `vehicles.vehicle_group_id` es nullable y con ON DELETE SET NULL: la réplica
  nunca debe impedir borrar un grupo si alguna vez se decide hacerlo a mano.

Migración aditiva: no toca ninguna consulta existente.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "y2z3a4b50051"
down_revision: str | None = "x1y2z3a40050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "fleet_vehicle_groups"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("fleet_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["fleet_id"], ["fleets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], [f"{TABLE}.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("source_id", name="uq_fleet_vehicle_group_source_id"),
    )
    op.create_index(f"ix_{TABLE}_fleet", TABLE, ["fleet_id"])
    op.create_index(f"ix_{TABLE}_fleet_active", TABLE, ["fleet_id", "is_active"])

    op.add_column(
        "vehicles",
        sa.Column("vehicle_group_id", sa.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_vehicles_vehicle_group_id",
        "vehicles",
        TABLE,
        ["vehicle_group_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_vehicles_vehicle_group_id",
        "vehicles",
        ["vehicle_group_id"],
        postgresql_where=sa.text("vehicle_group_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_vehicles_vehicle_group_id", table_name="vehicles")
    op.drop_constraint("fk_vehicles_vehicle_group_id", "vehicles", type_="foreignkey")
    op.drop_column("vehicles", "vehicle_group_id")
    op.drop_index(f"ix_{TABLE}_fleet_active", table_name=TABLE)
    op.drop_index(f"ix_{TABLE}_fleet", table_name=TABLE)
    op.drop_table(TABLE)
