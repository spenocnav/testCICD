"""campo vocacional en vehículos

Revision ID: h8e5f7g90008
Revises: g7d4e6f80007
Create Date: 2026-06-19

Navi Vehículos ahora expone `vocacional` en el snapshot (booleano, nunca null,
default false en origen). Réplica local de ese atributo del vehículo.
true = uso vocacional, false = transporte/comercial. server_default false para
poblar las filas existentes; el sync/seed lo sobreescribe con el valor real.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "h8e5f7g90008"
down_revision: str | None = "g7d4e6f80007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "vehicles",
        sa.Column(
            "vocacional",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # El default era solo para poblar filas existentes; el valor real viene del
    # snapshot en cada sync.
    op.alter_column("vehicles", "vocacional", server_default=None)


def downgrade() -> None:
    op.drop_column("vehicles", "vocacional")
