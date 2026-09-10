"""add a bounded detail payload to sync_state

`sync_state` ya guarda el último watermark por recurso. Un heartbeat operativo
necesita además un poco de contexto (estado, razones, contadores) para que la
UI distinga "sano y sin novedades" de "detenido". Se agrega una columna JSONB
genérica en vez de una tabla por worker: el concepto de `sync_state` es
justamente "último estado conocido de cada recurso".

Revision ID: c9d0e1f20029
Revises: b8c9d0e10028
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c9d0e1f20029"
down_revision: str | None = "b8c9d0e10028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sync_state",
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sync_state", "detail")
