"""El trabajo asociado a la novedad, y el borrado de la issue en CloudFleet.

Dos columnas aditivas y nullables sobre `novedades`:

- `associated_labor_name` acompaña a `associated_labor_id`, que existía en el
  modelo desde el principio y se escribía con `None` fijo. CloudFleet publica el
  enlace como `associatedLabor: {id, name}` en el detalle de la issue: el `id`
  es el `labors[].id` de la orden de trabajo, y por `parts[].laborId` identifica
  también los repuestos de ESE trabajo. Es lo que permite atribuir un desenlace
  al trabajo correcto en vez de a la orden completa, que puede tener varios.
- `external_deleted_at` registra que CloudFleet ya no conoce la issue. Es
  observación, no decisión: el sync la vuelve a NULL si la issue reaparece, así
  que un 404 transitorio del proveedor se corrige solo en la corrida siguiente.

Sin la segunda, una issue borrada desde la UI de CloudFleet dejaba la falla
marcada "Escalada" para siempre y sin poder volver a escalarse, porque el sync
de estado deja intacta la fila de lo que no encuentra.

Revision ID: a0b1c2d30059
Revises: f9a0b1c20058
Create Date: 2026-09-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a0b1c2d30059"
down_revision: str | None = "f9a0b1c20058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "novedades",
        sa.Column("associated_labor_name", sa.String(), nullable=True),
    )
    op.add_column(
        "novedades",
        sa.Column("external_deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Las borradas son pocas y se consultan para liberar la falla escalada.
    op.create_index(
        "ix_novedades_external_deleted",
        "novedades",
        ["external_deleted_at"],
        postgresql_where=sa.text("external_deleted_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_novedades_external_deleted", table_name="novedades")
    op.drop_column("novedades", "external_deleted_at")
    op.drop_column("novedades", "associated_labor_name")
