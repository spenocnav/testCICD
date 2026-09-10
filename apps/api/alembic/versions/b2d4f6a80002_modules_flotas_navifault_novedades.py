"""rename fleet->flotas + nuevos módulos navifault y novedades

Revision ID: b2d4f6a80002
Revises: a1c2e3f40001
Create Date: 2026-05-29

Renombra el módulo `fleet` a `flotas` (code/nombre y sus permisos) y siembra
dos módulos nuevos `navifault` y `novedades` con permisos view/edit. El rol
`admin` recibe `edit` en los módulos nuevos.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "b2d4f6a80002"
down_revision: str | None = "a1c2e3f40001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Nuevos módulos a sembrar (reports y documents ya existen).
NEW_MODULES: list[dict[str, object]] = [
    {"code": "navifault", "name": "Navifault", "description": "Eventos y alertas Navifault", "order": 60},
    {"code": "novedades", "name": "Novedades", "description": "Novedades y notificaciones", "order": 70},
]

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

    # 1. fleet -> flotas (módulo + permisos + resource).
    bind.execute(
        sa.text(
            "UPDATE modules SET code = 'flotas', name = 'Flotas', "
            "description = 'Gestión de flotas', updated_at = :u WHERE code = 'fleet'"
        ),
        {"u": now},
    )
    bind.execute(
        sa.text(
            "UPDATE permissions SET code = 'flotas.view', resource = 'flotas', "
            "description = 'Ver flotas', updated_at = :u WHERE code = 'fleet.view'"
        ),
        {"u": now},
    )
    bind.execute(
        sa.text(
            "UPDATE permissions SET code = 'flotas.edit', resource = 'flotas', "
            "description = 'Editar flotas', updated_at = :u WHERE code = 'fleet.edit'"
        ),
        {"u": now},
    )

    # 2. Sembrar módulos nuevos + permisos view/edit.
    admin_id = bind.execute(
        sa.text("SELECT id FROM roles WHERE code = 'admin'")
    ).scalar_one_or_none()

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
            bind.execute(
                sa.insert(t["permissions"]).values(
                    id=perm_id,
                    code=f"{m['code']}.{action}",
                    description=f"{verbo} {str(m['name']).lower()}",
                    resource=m["code"],
                    action=action,
                    created_at=now,
                    updated_at=now,
                )
            )
            # admin recibe edit (y view) en los módulos nuevos.
            if admin_id is not None:
                bind.execute(
                    sa.insert(t["role_permissions"]).values(
                        role_id=admin_id, permission_id=perm_id
                    )
                )


def downgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc)

    codes = [m["code"] for m in NEW_MODULES]
    placeholders = ",".join(f"'{c}'" for c in codes)

    # Quitar grants + permisos + módulos nuevos.
    bind.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id IN "
            f"(SELECT id FROM permissions WHERE resource IN ({placeholders}))"
        )
    )
    bind.execute(sa.text(f"DELETE FROM permissions WHERE resource IN ({placeholders})"))
    bind.execute(sa.text(f"DELETE FROM modules WHERE code IN ({placeholders})"))

    # flotas -> fleet.
    bind.execute(
        sa.text(
            "UPDATE modules SET code = 'fleet', name = 'Flota', "
            "description = 'Gestión de flota', updated_at = :u WHERE code = 'flotas'"
        ),
        {"u": now},
    )
    bind.execute(
        sa.text(
            "UPDATE permissions SET code = 'fleet.view', resource = 'fleet', "
            "description = 'Ver flota', updated_at = :u WHERE code = 'flotas.view'"
        ),
        {"u": now},
    )
    bind.execute(
        sa.text(
            "UPDATE permissions SET code = 'fleet.edit', resource = 'fleet', "
            "description = 'Editar flota', updated_at = :u WHERE code = 'flotas.edit'"
        ),
        {"u": now},
    )
