"""Retirar la tabla de descripciones técnicas, que quedó sin dueño.

La "descripción técnica" no era una función del producto: su endpoint
`POST /navifault/technical-description` no tenía **un solo consumidor** en
`apps/web`, su tabla estaba en **0 filas** —contra 366 de las descripciones de
cliente— y aun así el worker `portal-cliente-navifault-llm-worker` seguía
corriendo. Lo que no se usa no se protege: se borra.

Al retirarse el modelo `NavifaultGeneratedTechnicalDescription`, dejar la tabla
produciría deriva silenciosa entre el esquema y los modelos, y un
`alembic revision --autogenerate` futuro propondría este mismo DROP sin que
nadie recuerde por qué.

Se dropea con la tabla vacía y verificada vacía: no hay dato que perder. El
`downgrade` la reconstruye idéntica —columnas, CHECK, FK, unicidad e índice—
pero vacía, porque no había nada que restaurar.

La resolución Cummins **no se toca**: vive en `navifault_llm_service` y de ella
dependen el visor del documento y la comunicación al cliente.

Revision ID: b5c6d7e80054
Revises: a4b5c6d70053
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5c6d7e80054"
down_revision: str | None = "a4b5c6d70053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "navifault"
TABLE = "generated_technical_descriptions"


def upgrade() -> None:
    op.drop_index("ix_navifault_description_status", table_name=TABLE, schema=SCHEMA)
    op.drop_table(TABLE, schema=SCHEMA)


def downgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("description_id", sa.String(length=96), nullable=False),
        sa.Column("fault_page_id", sa.String(length=96), nullable=False),
        sa.Column("context_sha256", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=96), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "generation_metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed')",
            name="ck_navifault_description_status",
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
            name="uq_navifault_description_cache",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_navifault_description_status",
        TABLE,
        ["status", "updated_at"],
        schema=SCHEMA,
    )
