"""canonicaliza reportes.* en roles sembrados.

Revision ID: z6a7b8c90026
Revises: y5z6a7b80025
Create Date: 2026-08-05

La matriz inicial usó ``reports.*`` y las rutas actuales usan ``reportes.*``.
Tras crear el módulo nuevo, solo admin recibió los permisos canónicos. Esta
migración conserva la acción de cada rol, retira los grants legacy y deja un
único catálogo consumible por API y frontend. También elimina el módulo
legacy ``reports``.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
import uuid

import sqlalchemy as sa

from alembic import op

revision: str = "z6a7b8c90026"
down_revision: str | None = "y5z6a7b80025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    bind.execute(
        sa.text(
            """
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT rp.role_id, canonical.id
            FROM role_permissions rp
            JOIN permissions legacy ON legacy.id = rp.permission_id
            JOIN permissions canonical
              ON canonical.code = REPLACE(legacy.code, 'reports.', 'reportes.')
            WHERE legacy.code IN ('reports.view', 'reports.edit')
            ON CONFLICT DO NOTHING
            """
        )
    )
    bind.execute(
        sa.text(
            "DELETE FROM permissions "
            "WHERE code IN ('reports.view', 'reports.edit')"
        )
    )
    bind.execute(sa.text("DELETE FROM modules WHERE code = 'reports'"))
    bind.execute(
        sa.text(
            """
            DELETE FROM role_permissions
            WHERE permission_id IN (
                SELECT id FROM permissions
                WHERE code IN ('reports.view', 'reports.edit')
            )
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()

    now = datetime.now(timezone.utc)
    bind.execute(
        sa.text(
            """
            INSERT INTO modules (id, code, name, description, "order", is_active,
                                 created_at, updated_at)
            VALUES (:id, 'reports', 'Reportes', 'Reportes e indicadores', 40, true,
                    :created_at, :updated_at)
            ON CONFLICT (code) DO NOTHING
            """
        ),
        {"id": uuid.uuid4(), "created_at": now, "updated_at": now},
    )
    for action in ("view", "edit"):
        bind.execute(
            sa.text(
                """
                INSERT INTO permissions (id, code, description, resource, action,
                                         created_at, updated_at)
                VALUES (:id, :code, :description, 'reports', :action,
                        :created_at, :updated_at)
                ON CONFLICT (code) DO NOTHING
                """
            ),
            {
                "id": uuid.uuid4(),
                "code": f"reports.{action}",
                "description": f"{'Ver' if action == 'view' else 'Editar'} reportes",
                "action": action,
                "created_at": now,
                "updated_at": now,
            },
        )

    bind.execute(
        sa.text(
            """
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT rp.role_id, legacy.id
            FROM role_permissions rp
            JOIN permissions canonical ON canonical.id = rp.permission_id
            JOIN permissions legacy
              ON legacy.code = REPLACE(canonical.code, 'reportes.', 'reports.')
            WHERE canonical.code IN ('reportes.view', 'reportes.edit')
            ON CONFLICT DO NOTHING
            """
        )
    )
    bind.execute(
        sa.text(
            """
            DELETE FROM role_permissions
            WHERE permission_id IN (
                SELECT id FROM permissions
                WHERE code IN ('reportes.view', 'reportes.edit')
            )
            """
        )
    )
