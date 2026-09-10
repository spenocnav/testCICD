"""Allow auditable reversions of Navifault fault management.

Revision ID: i5j6k7l80035
Revises: h4i5j6k70034
Create Date: 2026-08-25
"""

from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic.
revision = "i5j6k7l80035"
down_revision = "h4i5j6k70034"
branch_labels = None
depends_on = None

SCHEMA = "navifault"
TABLE = "managed_fault_actions"
CONSTRAINT = "ck_navifault_managed_action_type"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, TABLE, schema=SCHEMA, type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        TABLE,
        "action_type IN ('managed', 'unmanaged')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, TABLE, schema=SCHEMA, type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        TABLE,
        "action_type IN ('managed')",
        schema=SCHEMA,
    )
