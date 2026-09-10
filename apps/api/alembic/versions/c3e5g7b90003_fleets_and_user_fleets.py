"""tabla fleets + asignación N:N user_fleets + seed de flotas ejemplo

Revision ID: c3e5g7b90003
Revises: b2d4f6a80002
Create Date: 2026-05-29

Introduce la entidad Flota y la asignación usuario<->flota. Los datos de la
app se scopean por flota (header `X-Fleet-Id`); `admin` ve todas.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "c3e5g7b90003"
down_revision: str | None = "b2d4f6a80002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Flotas de ejemplo para arrancar.
SEED_FLEETS: list[dict[str, str]] = [
    {"code": "FLOTA-NAVITRANS", "name": "Flota Navitrans"},
    {"code": "FLOTA-CLIENTE-A", "name": "Flota Cliente A"},
    {"code": "FLOTA-CLIENTE-B", "name": "Flota Cliente B"},
]


def upgrade() -> None:
    now = datetime.now(timezone.utc)

    op.create_table(
        "fleets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_fleets_code", "fleets", ["code"], unique=True)

    op.create_table(
        "user_fleets",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "fleet_id",
            UUID(as_uuid=True),
            sa.ForeignKey("fleets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    fleets = sa.table(
        "fleets",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    bind = op.get_bind()
    for f in SEED_FLEETS:
        bind.execute(
            sa.insert(fleets).values(
                id=uuid.uuid4(),
                code=f["code"],
                name=f["name"],
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )


def downgrade() -> None:
    op.drop_table("user_fleets")
    op.drop_index("ix_fleets_code", table_name="fleets")
    op.drop_table("fleets")
