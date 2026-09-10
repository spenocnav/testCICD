"""Añade archivo lógico a usuarios."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "x1y2z3a40050"
down_revision: str | None = "w9x0y1z20049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column("users", "is_archived", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "is_archived")
