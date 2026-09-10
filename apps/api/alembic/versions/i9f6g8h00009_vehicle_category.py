"""categoría efectiva del cliente en vehículos

Revision ID: i9f6g8h00009
Revises: h8e5f7g90008
Create Date: 2026-06-22

Navi Vehículos ahora expone la categoría efectiva del vehículo en el snapshot
(override propio > cliente > 'Ninguna'). Réplica local de ese atributo. El sync
deriva is_active de aquí: 'Ninguna' = inactivo, las gestionadas (Flota
Administrada / Experiencia Superior) = activo. server_default 'Ninguna' para
poblar filas existentes; el sync/seed lo sobreescribe con el valor real.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "i9f6g8h00009"
down_revision: str | None = "h8e5f7g90008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "vehicles",
        sa.Column(
            "category",
            sa.Text(),
            nullable=False,
            server_default="Ninguna",
        ),
    )
    # El default era solo para poblar filas existentes; el valor real viene del
    # snapshot en cada sync.
    op.alter_column("vehicles", "category", server_default=None)


def downgrade() -> None:
    op.drop_column("vehicles", "category")
