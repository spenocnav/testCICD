"""asegura módulo mantenimiento y permisos en RBAC

Revision ID: o5p6q7r80015
Revises: n4o5p6q70014
Create Date: 2026-06-30

Esta migración corrige bases donde `m3n4o5p60013` ya había corrido antes de
agregar grants de mantenimiento para roles operativos.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "o5p6q7r80015"
down_revision: str | None = "n4o5p6q70014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MODULE = {
    "code": "mantenimiento",
    "name": "Mantenimiento",
    "description": "Gestión de mantenimiento de vehículos",
    "order": 80,
}

ROLE_GRANTS: dict[str, str] = {
    "admin": "edit",
    "admin_flota_navitrans": "edit",
    "admin_flota_cliente": "edit",
    "gerente_cuenta": "view",
    "viewer": "view",
}


def _expand(action: str) -> list[str]:
    return ["view", "edit"] if action == "edit" else ["view"]


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)

    bind.execute(
        sa.text(
            """
            INSERT INTO modules (id, code, name, description, "order", is_active, created_at, updated_at)
            VALUES (:id, :code, :name, :description, :order, true, :now, :now)
            ON CONFLICT (code) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                "order" = EXCLUDED."order",
                is_active = true,
                updated_at = EXCLUDED.updated_at
            """
        ),
        {**MODULE, "id": uuid.uuid4(), "now": now},
    )

    for action in ("view", "edit"):
        verb = "Ver" if action == "view" else "Editar"
        bind.execute(
            sa.text(
                """
                INSERT INTO permissions (id, code, description, resource, action, created_at, updated_at)
                VALUES (:id, :code, :description, :resource, :action, :now, :now)
                ON CONFLICT (code) DO UPDATE SET
                    description = EXCLUDED.description,
                    resource = EXCLUDED.resource,
                    action = EXCLUDED.action,
                    updated_at = EXCLUDED.updated_at
                """
            ),
            {
                "id": uuid.uuid4(),
                "code": f"{MODULE['code']}.{action}",
                "description": f"{verb} {str(MODULE['name']).lower()}",
                "resource": MODULE["code"],
                "action": action,
                "now": now,
            },
        )

    for role_code, grant in ROLE_GRANTS.items():
        for action in _expand(grant):
            bind.execute(
                sa.text(
                    """
                    INSERT INTO role_permissions (role_id, permission_id)
                    SELECT r.id, p.id
                    FROM roles r
                    JOIN permissions p ON p.code = :permission_code
                    WHERE r.code = :role_code
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "role_code": role_code,
                    "permission_code": f"{MODULE['code']}.{action}",
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
        {"resource": MODULE["code"]},
    )
    bind.execute(
        sa.text("DELETE FROM permissions WHERE resource = :resource"),
        {"resource": MODULE["code"]},
    )
    bind.execute(
        sa.text("DELETE FROM modules WHERE code = :code"),
        {"code": MODULE["code"]},
    )
