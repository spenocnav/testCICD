"""seed permissions roles admin bootstrap

Revision ID: bace3438506c
Revises: 1bb9aa7e294d
Create Date: 2026-05-27 11:10:17.826286

"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from app.core.config import settings
from app.core.security import hash_password

# revision identifiers, used by Alembic.
revision: str = "bace3438506c"
down_revision: str | None = "1bb9aa7e294d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PERMISSIONS: list[dict[str, str]] = [
    {"code": "users.read", "resource": "users", "action": "read", "description": "Listar y ver usuarios"},
    {"code": "users.create", "resource": "users", "action": "create", "description": "Crear nuevos usuarios"},
    {"code": "users.update", "resource": "users", "action": "update", "description": "Editar usuarios existentes"},
    {"code": "users.delete", "resource": "users", "action": "delete", "description": "Desactivar/eliminar usuarios"},
    {"code": "roles.read", "resource": "roles", "action": "read", "description": "Listar roles y permisos"},
    {"code": "roles.manage", "resource": "roles", "action": "manage", "description": "Gestionar roles y permisos"},
]

ROLES: list[dict[str, object]] = [
    {
        "code": "admin",
        "name": "Administrador",
        "description": "Acceso completo a la plataforma",
        "permissions": [
            "users.read",
            "users.create",
            "users.update",
            "users.delete",
            "roles.read",
            "roles.manage",
        ],
    },
    {
        "code": "gestor",
        "name": "Gestor",
        "description": "Gestiona usuarios sin eliminar ni administrar roles",
        "permissions": ["users.read", "users.create", "users.update", "roles.read"],
    },
    {
        "code": "viewer",
        "name": "Visor",
        "description": "Solo lectura de usuarios",
        "permissions": ["users.read"],
    },
]


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc)

    permissions_table = sa.table(
        "permissions",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("code", sa.String),
        sa.column("description", sa.String),
        sa.column("resource", sa.String),
        sa.column("action", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    roles_table = sa.table(
        "roles",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("is_system", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    role_perm_table = sa.table(
        "role_permissions",
        sa.column("role_id", UUID(as_uuid=True)),
        sa.column("permission_id", UUID(as_uuid=True)),
    )
    users_table = sa.table(
        "users",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("email", sa.String),
        sa.column("password_hash", sa.String),
        sa.column("full_name", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    user_roles_table = sa.table(
        "user_roles",
        sa.column("user_id", UUID(as_uuid=True)),
        sa.column("role_id", UUID(as_uuid=True)),
    )

    perm_ids: dict[str, uuid.UUID] = {}
    for perm in PERMISSIONS:
        new_id = uuid.uuid4()
        perm_ids[perm["code"]] = new_id
        bind.execute(
            sa.insert(permissions_table).values(
                id=new_id,
                code=perm["code"],
                description=perm["description"],
                resource=perm["resource"],
                action=perm["action"],
                created_at=now,
                updated_at=now,
            )
        )

    role_ids: dict[str, uuid.UUID] = {}
    for role in ROLES:
        new_id = uuid.uuid4()
        role_ids[str(role["code"])] = new_id
        bind.execute(
            sa.insert(roles_table).values(
                id=new_id,
                code=role["code"],
                name=role["name"],
                description=role["description"],
                is_system=True,
                created_at=now,
                updated_at=now,
            )
        )
        for perm_code in role["permissions"]:  # type: ignore[union-attr]
            bind.execute(
                sa.insert(role_perm_table).values(
                    role_id=new_id,
                    permission_id=perm_ids[perm_code],
                )
            )

    admin_id = uuid.uuid4()
    bind.execute(
        sa.insert(users_table).values(
            id=admin_id,
            email=settings.bootstrap_admin_email,
            password_hash=hash_password(settings.bootstrap_admin_password),
            full_name=settings.bootstrap_admin_name,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
    )
    bind.execute(
        sa.insert(user_roles_table).values(
            user_id=admin_id,
            role_id=role_ids["admin"],
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM user_roles"))
    bind.execute(
        sa.text("DELETE FROM users WHERE email = :email"),
        {"email": settings.bootstrap_admin_email},
    )
    bind.execute(sa.text("DELETE FROM role_permissions"))
    bind.execute(
        sa.text("DELETE FROM roles WHERE code IN ('admin', 'gestor', 'viewer')")
    )
    bind.execute(
        sa.text(
            "DELETE FROM permissions WHERE code IN "
            "('users.read','users.create','users.update','users.delete',"
            "'roles.read','roles.manage')"
        )
    )
