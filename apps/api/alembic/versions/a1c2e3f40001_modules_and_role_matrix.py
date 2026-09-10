"""modules table + view/edit permission matrix + 6 business roles

Revision ID: a1c2e3f40001
Revises: bace3438506c
Create Date: 2026-05-28

Reemplaza el esquema de permisos por uno basado en módulos con acciones
`view`/`edit`, introduce la tabla `modules` y siembra los 6 roles de negocio.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "a1c2e3f40001"
down_revision: str | None = "bace3438506c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --- Catálogo nuevo -------------------------------------------------------

MODULES: list[dict[str, object]] = [
    {"code": "users", "name": "Usuarios", "description": "Gestión de usuarios", "order": 10},
    {"code": "roles", "name": "Roles y permisos", "description": "Roles y matriz de permisos", "order": 20},
    {"code": "fleet", "name": "Flota", "description": "Gestión de flota", "order": 30},
    {"code": "reports", "name": "Reportes", "description": "Reportes e indicadores", "order": 40},
    {"code": "documents", "name": "Documentos", "description": "Documentos y cargas de información", "order": 50},
]

ACTIONS = ["view", "edit"]

# Matriz por rol: módulo -> acción máxima. 'edit' implica 'view'.
ROLES: list[dict[str, object]] = [
    {
        "code": "admin",
        "name": "Administrador",
        "description": "Acceso completo a la plataforma",
        "is_system": True,
        "grants": {m["code"]: "edit" for m in MODULES},
    },
    {
        "code": "admin_flota_navitrans",
        "name": "Administrador Flota Navitrans",
        "description": "Administra flota, reportes y documentos a nivel Navitrans",
        "is_system": True,
        "grants": {"users": "view", "fleet": "edit", "reports": "edit", "documents": "edit"},
    },
    {
        "code": "admin_flota_cliente",
        "name": "Administrador Flota Cliente",
        "description": "Administra la flota y documentos de su cliente",
        "is_system": True,
        "grants": {"fleet": "edit", "reports": "view", "documents": "edit"},
    },
    {
        "code": "gerente_cuenta",
        "name": "Gerente de cuenta",
        "description": "Gestiona reportes de la cuenta y consulta flota/documentos",
        "is_system": True,
        "grants": {"fleet": "view", "reports": "edit", "documents": "view"},
    },
    {
        "code": "conductor",
        "name": "Conductor",
        "description": "Consulta flota y documentos asignados",
        "is_system": True,
        "grants": {"fleet": "view", "documents": "view"},
    },
    {
        "code": "viewer",
        "name": "Visor",
        "description": "Solo lectura en todos los módulos operativos",
        "is_system": True,
        "grants": {"users": "view", "fleet": "view", "reports": "view", "documents": "view"},
    },
]


def _expand(action: str) -> list[str]:
    """edit -> [view, edit]; view -> [view]."""
    return ["view", "edit"] if action == "edit" else ["view"]


# --- Tablas (sa.table para data ops) --------------------------------------


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
        "roles": sa.table(
            "roles",
            sa.column("id", UUID(as_uuid=True)),
            sa.column("code", sa.String),
            sa.column("name", sa.String),
            sa.column("description", sa.String),
            sa.column("is_system", sa.Boolean),
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

    # 1. Tabla modules.
    op.create_table(
        "modules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_modules_code", "modules", ["code"], unique=True)

    t = _tables()

    # 2. Seed modules.
    for m in MODULES:
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

    # 3. Limpiar esquema viejo de permisos.
    bind.execute(sa.text("DELETE FROM role_permissions"))
    bind.execute(sa.text("DELETE FROM permissions"))

    # 4. Permisos view/edit por módulo.
    perm_ids: dict[str, uuid.UUID] = {}
    for m in MODULES:
        for action in ACTIONS:
            code = f"{m['code']}.{action}"
            new_id = uuid.uuid4()
            perm_ids[code] = new_id
            verbo = "Ver" if action == "view" else "Editar"
            bind.execute(
                sa.insert(t["permissions"]).values(
                    id=new_id,
                    code=code,
                    description=f"{verbo} {str(m['name']).lower()}",
                    resource=m["code"],
                    action=action,
                    created_at=now,
                    updated_at=now,
                )
            )

    # 5. Eliminar rol obsoleto 'gestor' (no presente en el nuevo catálogo).
    bind.execute(sa.text("DELETE FROM roles WHERE code = 'gestor'"))

    # 6. Upsert de los 6 roles + cableado de la matriz.
    for role in ROLES:
        existing = bind.execute(
            sa.text("SELECT id FROM roles WHERE code = :c"), {"c": role["code"]}
        ).first()
        if existing:
            role_id = existing[0]
            bind.execute(
                sa.text(
                    "UPDATE roles SET name = :n, description = :d, is_system = :s, "
                    "updated_at = :u WHERE id = :id"
                ),
                {
                    "n": role["name"],
                    "d": role["description"],
                    "s": role["is_system"],
                    "u": now,
                    "id": role_id,
                },
            )
        else:
            role_id = uuid.uuid4()
            bind.execute(
                sa.insert(t["roles"]).values(
                    id=role_id,
                    code=role["code"],
                    name=role["name"],
                    description=role["description"],
                    is_system=role["is_system"],
                    created_at=now,
                    updated_at=now,
                )
            )

        granted: set[str] = set()
        for module_code, action in role["grants"].items():  # type: ignore[union-attr]
            for act in _expand(action):
                granted.add(f"{module_code}.{act}")
        for code in granted:
            bind.execute(
                sa.insert(t["role_permissions"]).values(
                    role_id=role_id,
                    permission_id=perm_ids[code],
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    t = _tables()

    # Revertir a permisos viejos.
    bind.execute(sa.text("DELETE FROM role_permissions"))
    bind.execute(sa.text("DELETE FROM permissions"))
    bind.execute(
        sa.text(
            "DELETE FROM roles WHERE code IN "
            "('admin_flota_navitrans','admin_flota_cliente','gerente_cuenta','conductor')"
        )
    )

    old_perms = [
        ("users.read", "users", "read", "Listar y ver usuarios"),
        ("users.create", "users", "create", "Crear nuevos usuarios"),
        ("users.update", "users", "update", "Editar usuarios existentes"),
        ("users.delete", "users", "delete", "Desactivar/eliminar usuarios"),
        ("roles.read", "roles", "read", "Listar roles y permisos"),
        ("roles.manage", "roles", "manage", "Gestionar roles y permisos"),
    ]
    perm_ids: dict[str, uuid.UUID] = {}
    for code, resource, action, desc in old_perms:
        new_id = uuid.uuid4()
        perm_ids[code] = new_id
        bind.execute(
            sa.insert(t["permissions"]).values(
                id=new_id, code=code, description=desc, resource=resource,
                action=action, created_at=now, updated_at=now,
            )
        )

    old_roles = {
        "admin": list(perm_ids.keys()),
        "gestor": ["users.read", "users.create", "users.update", "roles.read"],
        "viewer": ["users.read"],
    }
    for code, perm_codes in old_roles.items():
        row = bind.execute(sa.text("SELECT id FROM roles WHERE code = :c"), {"c": code}).first()
        if row:
            role_id = row[0]
        else:
            role_id = uuid.uuid4()
            bind.execute(
                sa.insert(t["roles"]).values(
                    id=role_id, code=code, name=code.capitalize(),
                    description=None, is_system=True, created_at=now, updated_at=now,
                )
            )
        for pc in perm_codes:
            bind.execute(
                sa.insert(t["role_permissions"]).values(
                    role_id=role_id, permission_id=perm_ids[pc]
                )
            )

    op.drop_index("ix_modules_code", table_name="modules")
    op.drop_table("modules")
