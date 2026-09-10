"""outbox transaccional para novedades + idempotencia HTTP

Revision ID: 1c2d3e4f5a60
Revises: q7r8s9t00017, 364f6337bf44
Create Date: 2026-07-14

Consolida los dos heads existentes (q7r8s9t00017 y 364f6337bf44) y agrega:

- Tabla `novedad_outbox` one-to-one con `novedades` (FK CASCADE) con estados
  internos pending/processing/sent/failed, `attempts`, `available_at`,
  `locked_at` y `last_error`. Esta tabla es interna: NO se expone en
  `NovedadRead`. Su propósito es desacoplar el commit local del envío a
  Cloudfleet y permitir reintentos seguros.

- Columnas `idempotency_key` y `request_fingerprint` en `novedades` (ambas
  nullable). El índice único parcial por (created_by_id, idempotency_key)
  asegura que la misma clave + usuario sólo produzca una novedad, ignorando
  filas con clave NULL.

- Backfill del outbox para todas las novedades existentes que aún no tengan
  fila. Las que ya están `sent` se marcan como `sent`; el resto queda
  `pending` con `available_at = now()`.

- Downgrade completo: elimina primero el backfill, luego columnas, índices y
  tabla.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1c2d3e4f5a60"
# Consolidamos los dos heads previos: este migration depende de AMBOS vía la
# tupla `down_revision`. Alembic trata una tupla como un merge implícito: los
# dos heads quedan como ramas y este revision es el nuevo head único.
down_revision: tuple[str, str] = ("q7r8s9t00017", "364f6337bf44")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Novedades: idempotencia HTTP.
    # -------------------------------------------------------------------------
    op.add_column(
        "novedades",
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "novedades",
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "uq_novedades_idem_per_user",
        "novedades",
        ["created_by_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    # -------------------------------------------------------------------------
    # 2. Novedad outbox.
    # -------------------------------------------------------------------------
    op.create_table(
        "novedad_outbox",
        sa.Column("novedad_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'sent', 'failed')",
            name="ck_novedad_outbox_status",
        ),
        sa.ForeignKeyConstraint(
            ["novedad_id"], ["novedades.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("novedad_id", name="uq_novedad_outbox_novedad_id"),
    )
    op.create_index(
        "ix_novedad_outbox_due",
        "novedad_outbox",
        ["status", "available_at", "id"],
    )

    # -------------------------------------------------------------------------
    # 3. Backfill: crear outbox para cada novedad existente.
    # -------------------------------------------------------------------------
    op.execute(
        sa.text(
            """
            INSERT INTO novedad_outbox
                (id, novedad_id, status, attempts, available_at, locked_at,
                 last_error, created_at, updated_at)
            SELECT
                gen_random_uuid(),
                n.id,
                CASE
                    WHEN n.cloudfleet_status = 'sent' THEN 'sent'
                    ELSE 'pending'
                END,
                0,
                now(),
                NULL,
                NULL,
                now(),
                now()
            FROM novedades n
            WHERE NOT EXISTS (
                SELECT 1 FROM novedad_outbox o WHERE o.novedad_id = n.id
            )
            """
        )
    )


def downgrade() -> None:
    # Backfill es seguro de revertir: no se eliminan filas, sólo la tabla.
    op.drop_index("ix_novedad_outbox_due", table_name="novedad_outbox")
    op.drop_table("novedad_outbox")

    op.drop_index("uq_novedades_idem_per_user", table_name="novedades")
    op.drop_column("novedades", "request_fingerprint")
    op.drop_column("novedades", "idempotency_key")
