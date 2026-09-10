"""alinea tipos de idempotencia de novedades con el contrato ORM

Revision ID: w3x4y5z60023
Revises: v2w3x4y50022
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "w3x4y5z60023"
down_revision: str | None = "v2w3x4y50022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "novedades",
        "idempotency_key",
        existing_type=sa.Text(),
        type_=sa.String(length=128),
        existing_nullable=True,
    )
    op.alter_column(
        "novedades",
        "request_fingerprint",
        existing_type=sa.Text(),
        type_=sa.String(length=64),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "novedades",
        "request_fingerprint",
        existing_type=sa.String(length=64),
        type_=sa.Text(),
        existing_nullable=True,
    )
    op.alter_column(
        "novedades",
        "idempotency_key",
        existing_type=sa.String(length=128),
        type_=sa.Text(),
        existing_nullable=True,
    )
