"""Seed the explicit permission to revert a Navifault fault management.

Revision ID: j6k7l8m90036
Revises: i5j6k7l80035
Create Date: 2026-08-25
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = "j6k7l8m90036"
down_revision: str | None = "i5j6k7l80035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MODULE_CODE = "navifault_management"
PERMISSION_CODES = (f"{MODULE_CODE}.view", f"{MODULE_CODE}.edit")


def _tables() -> dict[str, sa.Table]:
    return {
        "modules": sa.table(
            "modules",
            sa.column("id", UUID(as_uuid=True)),
            sa.column("code", sa.String),
            sa.column("name", sa.String),
            sa.column("description", sa.String),
            sa.column("order", sa.Integer),
            sa.column("is_active", sa.Boolean),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        ),
        "permissions": sa.table(
            "permissions",
            sa.column("id", UUID(as_uuid=True)),
            sa.column("code", sa.String),
            sa.column("description", sa.String),
            sa.column("resource", sa.String),
            sa.column("action", sa.String),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        ),
    }


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    tables = _tables()

    bind.execute(
        postgresql.insert(tables["modules"])
        .values(
            id=uuid.uuid4(),
            code=MODULE_CODE,
            name="Revertir gestión de fallas",
            description="Autoriza deshacer una falla marcada como gestionada en Navifault",
            order=61,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=["code"],
            set_={
                "name": "Revertir gestión de fallas",
                "description": "Autoriza deshacer una falla marcada como gestionada en Navifault",
                "order": 61,
                "is_active": True,
                "updated_at": now,
            },
        )
    )

    for action, description in (
        ("view", "Ver el permiso para revertir gestión de fallas Navifault"),
        ("edit", "Revertir una falla gestionada en Navifault"),
    ):
        bind.execute(
            postgresql.insert(tables["permissions"])
            .values(
                id=uuid.uuid4(),
                code=f"{MODULE_CODE}.{action}",
                description=description,
                resource=MODULE_CODE,
                action=action,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["code"],
                set_={
                    "description": description,
                    "resource": MODULE_CODE,
                    "action": action,
                    "updated_at": now,
                },
            )
        )

    bind.execute(
        sa.text(
            """
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT roles.id, permissions.id
            FROM roles
            JOIN permissions ON permissions.code IN :permission_codes
            WHERE roles.code = 'admin'
            ON CONFLICT DO NOTHING
            """
        ).bindparams(sa.bindparam("permission_codes", expanding=True)),
        {"permission_codes": PERMISSION_CODES},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            DELETE FROM role_permissions
            WHERE permission_id IN (
                SELECT id FROM permissions WHERE resource = :resource
            )
            """
        ),
        {"resource": MODULE_CODE},
    )
    bind.execute(
        sa.text("DELETE FROM permissions WHERE resource = :resource"),
        {"resource": MODULE_CODE},
    )
    bind.execute(
        sa.text("DELETE FROM modules WHERE code = :module_code"),
        {"module_code": MODULE_CODE},
    )
