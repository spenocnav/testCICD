"""add módulo reportes con permisos view/edit

Revision ID: d4f8h2c60004
Revises: c3e5g7b90003
Create Date: 2026-06-03

Crea el módulo `reportes` + permisos view/edit y los otorga al rol admin.
NO crea tablas analytics.* — esas las gestiona el loader de InformesRendimiento.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "d4f8h2c60004"
down_revision: str | None = "c3e5g7b90003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIONS = ["view", "edit"]


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
        "role_permissions": sa.table(
            "role_permissions",
            sa.column("role_id", UUID(as_uuid=True)),
            sa.column("permission_id", UUID(as_uuid=True)),
        ),
    }


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    t = _tables()

    bind.execute(
        sa.insert(t["modules"]).values(
            id=uuid.uuid4(),
            code="reportes",
            name="Reportes",
            description="Reportes e indicadores analíticos",
            order=50,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
    )

    admin_id = bind.execute(
        sa.text("SELECT id FROM roles WHERE code = 'admin'")
    ).scalar_one_or_none()

    for action in ACTIONS:
        perm_id = uuid.uuid4()
        verbo = "Ver" if action == "view" else "Editar"
        bind.execute(
            sa.insert(t["permissions"]).values(
                id=perm_id,
                code=f"reportes.{action}",
                description=f"{verbo} reportes",
                resource="reportes",
                action=action,
                created_at=now,
                updated_at=now,
            )
        )
        if admin_id is not None:
            bind.execute(
                sa.insert(t["role_permissions"]).values(
                    role_id=admin_id, permission_id=perm_id
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id IN "
            "(SELECT id FROM permissions WHERE resource = 'reportes')"
        )
    )
    bind.execute(sa.text("DELETE FROM permissions WHERE resource = 'reportes'"))
    bind.execute(sa.text("DELETE FROM modules WHERE code = 'reportes'"))
