"""Prioridad de cola para las comunicaciones cliente de Navifault.

Revision ID: o1p2q3r40041
Revises: n0o1p2q30040
Create Date: 2026-08-26

El worker es serial por diseño: el modelo local comparte GPU. Desde que un
barrido pregenera las fallas recientes, la cola deja de ser corta y una
petición nacida de un usuario que abre una ficha quedaría detrás del lote.
`priority` separa ambos orígenes sin duplicar la cola: lo interactivo se
atiende primero y el barrido ocupa el tiempo restante.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "o1p2q3r40041"
down_revision: str | None = "n0o1p2q30040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "navifault"
TABLE = "generated_client_descriptions"
QUEUE_INDEX = "ix_navifault_client_description_queue"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(
            "priority",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        schema=SCHEMA,
    )
    # El claim filtra por estado y consume en orden de prioridad y antigüedad.
    op.create_index(
        QUEUE_INDEX,
        TABLE,
        [sa.text("status"), sa.text("priority DESC"), sa.text("created_at")],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(QUEUE_INDEX, table_name=TABLE, schema=SCHEMA)
    op.drop_column(TABLE, "priority", schema=SCHEMA)
