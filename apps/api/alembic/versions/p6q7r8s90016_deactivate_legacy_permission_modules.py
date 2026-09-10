"""desactiva módulos legacy fuera del sidebar actual

Revision ID: p6q7r8s90016
Revises: o5p6q7r80015
Create Date: 2026-06-30
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p6q7r8s90016"
down_revision: str | None = "o5p6q7r80015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_MODULES = ("reports", "documents")


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE modules SET is_active = false, updated_at = :now "
            "WHERE code = ANY(:codes)"
        ),
        {"codes": list(LEGACY_MODULES), "now": datetime.now(UTC)},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE modules SET is_active = true, updated_at = :now "
            "WHERE code = ANY(:codes)"
        ),
        {"codes": list(LEGACY_MODULES), "now": datetime.now(UTC)},
    )
