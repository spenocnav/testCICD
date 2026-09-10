"""nuevo módulo mantenimiento + permisos view/edit

Revision ID: m3n4o5p60013
Revises: l2c3d4e50012
Create Date: 2026-06-25

Siembra el módulo `mantenimiento` con permisos view/edit y grants iniciales para
roles operativos.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "m3n4o5p60013"
down_revision: str | None = "l2c3d4e50012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_MODULES: list[dict[str, object]] = [
    {
        "code": "mantenimiento",
        "name": "Mantenimiento",
        "description": "Gestión de mantenimiento de vehículos",
        "order": 80,
    },
]

ACTIONS = ["view", "edit"]

ROLE_GRANTS: dict[str, str] = {
    "admin": "edit",
    "admin_flota_navitrans": "edit",
    "admin_flota_cliente": "edit",
    "gerente_cuenta": "view",
    "viewer": "view",
}


def _expand(action: str) -> list[str]:
    return ["view", "edit"] if action == "edit" else ["view"]


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
    now = datetime.now(UTC)
    t = _tables()

    for m in NEW_MODULES:
        bind.execute(
            sa.insert(t["modules"]).values(
                id=uuid.uuid4(),
                code=m["code"],
                name=m["name"],
                description=m["description"],
                order=m["order"],
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
        for action in ACTIONS:
            perm_id = uuid.uuid4()
            verbo = "Ver" if action == "view" else "Editar"
            code = f"{m['code']}.{action}"
            bind.execute(
                sa.insert(t["permissions"]).values(
                    id=perm_id,
                    code=code,
                    description=f"{verbo} {str(m['name']).lower()}",
                    resource=m["code"],
                    action=action,
                    created_at=now,
                    updated_at=now,
                )
            )
        for role_code, grant in ROLE_GRANTS.items():
            role_id = bind.execute(
                sa.text("SELECT id FROM roles WHERE code = :code"),
                {"code": role_code},
            ).scalar_one_or_none()
            if role_id is None:
                continue
            for action in _expand(grant):
                permission_id = bind.execute(
                    sa.text("SELECT id FROM permissions WHERE code = :code"),
                    {"code": f"{m['code']}.{action}"},
                ).scalar_one()
                bind.execute(
                    sa.insert(t["role_permissions"]).values(
                        role_id=role_id,
                        permission_id=permission_id,
                    )
                )


def downgrade() -> None:
    bind = op.get_bind()

    codes = [m["code"] for m in NEW_MODULES]
    placeholders = ",".join(f"'{c}'" for c in codes)

    bind.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id IN "
            f"(SELECT id FROM permissions WHERE resource IN ({placeholders}))"
        )
    )
    bind.execute(sa.text(f"DELETE FROM permissions WHERE resource IN ({placeholders})"))
    bind.execute(sa.text(f"DELETE FROM modules WHERE code IN ({placeholders})"))
