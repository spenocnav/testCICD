"""Registra el módulo de calidad de datos y sus permisos RBAC.

Revision ID: c3d4e5f6a7b9
Revises: b2c3d4e5f6a8
Create Date: 2026-07-14
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "c3d4e5f6a7b9"
down_revision: str | None = "b2c3d4e5f6a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MODULE_CODE = "calidad_datos"
ROLE_GRANTS: dict[str, str] = {
    "admin": "edit",
    "admin_flota_navitrans": "edit",
    "admin_flota_cliente": "edit",
    "gerente_cuenta": "view",
    "viewer": "view",
}


def _actions(grant: str) -> tuple[str, ...]:
    return ("view", "edit") if grant == "edit" else ("view",)


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)

    bind.execute(
        sa.text(
            """
            INSERT INTO modules
                (id, code, name, description, "order", is_active, created_at, updated_at)
            VALUES
                (:id, :code, :name, :description, :order, true, :now, :now)
            ON CONFLICT (code) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                "order" = EXCLUDED."order",
                is_active = true,
                updated_at = EXCLUDED.updated_at
            """
        ),
        {
            "id": uuid.uuid4(),
            "code": MODULE_CODE,
            "name": "Calidad de datos",
            "description": "Salud de la fuente maestra, Geotab y extracción analítica",
            "order": 95,
            "now": now,
        },
    )

    for action, verb in (("view", "Ver"), ("edit", "Gestionar")):
        bind.execute(
            sa.text(
                """
                INSERT INTO permissions
                    (id, code, description, resource, action, created_at, updated_at)
                VALUES
                    (:id, :code, :description, :resource, :action, :now, :now)
                ON CONFLICT (code) DO UPDATE SET
                    description = EXCLUDED.description,
                    resource = EXCLUDED.resource,
                    action = EXCLUDED.action,
                    updated_at = EXCLUDED.updated_at
                """
            ),
            {
                "id": uuid.uuid4(),
                "code": f"{MODULE_CODE}.{action}",
                "description": f"{verb} calidad de datos",
                "resource": MODULE_CODE,
                "action": action,
                "now": now,
            },
        )

    for role_code, grant in ROLE_GRANTS.items():
        for action in _actions(grant):
            bind.execute(
                sa.text(
                    """
                    INSERT INTO role_permissions (role_id, permission_id)
                    SELECT roles.id, permissions.id
                    FROM roles
                    JOIN permissions ON permissions.code = :permission_code
                    WHERE roles.code = :role_code
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "role_code": role_code,
                    "permission_code": f"{MODULE_CODE}.{action}",
                },
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
        sa.text("DELETE FROM modules WHERE code = :code"),
        {"code": MODULE_CODE},
    )
