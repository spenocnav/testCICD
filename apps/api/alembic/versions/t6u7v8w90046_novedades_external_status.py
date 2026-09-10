"""estado real de la issue en CloudFleet sobre `novedades`

Revision ID: t6u7v8w90046
Revises: s5t6u7v80045
Create Date: 2026-08-28

`novedades.cloudfleet_status` (pending/sent/failed) dice si el POST llegó a
CloudFleet; no dice nada de lo que pasó después. CloudFleet publica en cada
issue `isDone`, `doneAt` y `workOrderDoneNumber`, y el portal necesita
mostrarlo para que quien reportó sepa si su novedad ya se atendió.

Las columnas nuevas llevan el prefijo `external_` a propósito: son la réplica
del estado remoto y NO redefinen `cloudfleet_status`, que sigue siendo el
estado del envío. Todas nullable: NULL significa "nunca sincronizado".

El índice parcial cubre exactamente lo que el sync diario busca —novedades
enviadas cuya issue no consta como resuelta— para que el barrido no recorra
las ya cerradas.

Migración aditiva: no cambia ninguna consulta existente.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "t6u7v8w90046"
down_revision: str | None = "s5t6u7v80045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "novedades"
INDEX = "ix_novedades_external_open"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("external_is_done", sa.Boolean(), nullable=True))
    op.add_column(
        TABLE, sa.Column("external_done_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(TABLE, sa.Column("external_work_order_number", sa.Integer(), nullable=True))
    op.add_column(
        TABLE, sa.Column("external_synced_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        INDEX,
        TABLE,
        ["cloudfleet_issue_number"],
        unique=False,
        postgresql_where=sa.text(
            "cloudfleet_status = 'sent' AND external_is_done IS NOT TRUE"
        ),
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_column(TABLE, "external_synced_at")
    op.drop_column(TABLE, "external_work_order_number")
    op.drop_column(TABLE, "external_done_at")
    op.drop_column(TABLE, "external_is_done")
