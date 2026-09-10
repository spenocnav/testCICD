"""Cache durable de comunicaciones cliente generadas para Navifault.

Revision ID: n0o1p2q30040
Revises: m9n0o1p20039
Create Date: 2026-08-26

Cada fila conserva los dos textos producidos por una misma ejecución del
prompt de cliente. La identidad incluye página, contexto, prompt y modelo para
que una mejora no sobrescriba ni reutilice una salida histórica incompatible.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "n0o1p2q30040"
down_revision: str | None = "m9n0o1p20039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "navifault"


def upgrade() -> None:
    op.create_table(
        "generated_client_descriptions",
        sa.Column("description_id", sa.String(length=96), nullable=False),
        sa.Column("fault_page_id", sa.String(length=96), nullable=False),
        sa.Column("context_sha256", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=96), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("descripcion_correo_cliente", sa.Text(), nullable=True),
        sa.Column("descripcion_plataforma_cliente", sa.Text(), nullable=True),
        sa.Column("generation_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed')",
            name="ck_navifault_client_description_status",
        ),
        sa.ForeignKeyConstraint(
            ["fault_page_id"], [f"{SCHEMA}.fault_pages.fault_page_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("description_id"),
        sa.UniqueConstraint(
            "fault_page_id",
            "context_sha256",
            "prompt_version",
            "model_version",
            name="uq_navifault_client_description_cache",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_navifault_client_description_status",
        "generated_client_descriptions",
        ["status", "updated_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_navifault_client_description_status",
        table_name="generated_client_descriptions",
        schema=SCHEMA,
    )
    op.drop_table("generated_client_descriptions", schema=SCHEMA)
