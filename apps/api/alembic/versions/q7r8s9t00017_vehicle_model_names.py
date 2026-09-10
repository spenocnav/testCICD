"""nombres de modelo comercial y servicio en vehículos

Revision ID: q7r8s9t00017
Revises: p6q7r8s90016
Create Date: 2026-07-08

Navi Vehículos expone `marketing_model_name` y `service_model_name` en el
snapshot desde vehicle_motor_assignments. Portal Clientes los replica como
atributos de vehículo, sobreescritos por cada sync.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "q7r8s9t00017"
down_revision: str | None = "p6q7r8s90016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("vehicles", sa.Column("marketing_model_name", sa.Text(), nullable=True))
    op.add_column("vehicles", sa.Column("service_model_name", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("vehicles", "service_model_name")
    op.drop_column("vehicles", "marketing_model_name")
