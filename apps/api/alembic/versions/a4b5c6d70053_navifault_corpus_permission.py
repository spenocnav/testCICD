"""Separate the Cummins corpus behind its own read permission.

El cliente ve sus fallas y la comunicación redactada para él; el manual Cummins
—documento original, resumen oficial y candidatas de resolución— es de
Navitrans. Hasta esta revisión bastaba `navifault.view`, que tienen los roles de
cliente.

Se crea un permiso propio en lugar de reusar `navifault.edit` —que hoy daría la
misma frontera y cero migración— porque sería un permiso de escritura
gobernando una lectura, y eso es lo que alguien "limpia" más adelante sin saber
por qué estaba así. Además deja lugar a un analista que consulte el manual sin
poder cerrar casos.

Sólo `view`: no hay nada que editar en el corpus, y sembrar un `edit` que ningún
endpoint exige es la clase de superficie muerta que este módulo ya pagó una vez.

Revision ID: a4b5c6d70053
Revises: z3a4b5c60052
Create Date: 2026-09-01
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
revision: str = "a4b5c6d70053"
down_revision: str | None = "z3a4b5c60052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MODULE_CODE = "navifault_corpus"
MODULE_NAME = "Documentación técnica Cummins"
MODULE_DESCRIPTION = (
    "Autoriza consultar el documento original, el resumen oficial y las "
    "candidatas de resolución del corpus Cummins"
)
PERMISSION_CODE = f"{MODULE_CODE}.view"
PERMISSION_DESCRIPTION = "Ver la documentación técnica Cummins de una falla"

# Roles internos de Navitrans. `admin` además tiene bypass, pero el grant
# explícito es lo que hace que el permiso aparezca en la matriz de roles.
GRANTED_ROLE_CODES = ("admin", "admin_flota_navitrans")


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
            name=MODULE_NAME,
            description=MODULE_DESCRIPTION,
            order=62,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=["code"],
            set_={
                "name": MODULE_NAME,
                "description": MODULE_DESCRIPTION,
                "order": 62,
                "is_active": True,
                "updated_at": now,
            },
        )
    )

    bind.execute(
        postgresql.insert(tables["permissions"])
        .values(
            id=uuid.uuid4(),
            code=PERMISSION_CODE,
            description=PERMISSION_DESCRIPTION,
            resource=MODULE_CODE,
            action="view",
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=["code"],
            set_={
                "description": PERMISSION_DESCRIPTION,
                "resource": MODULE_CODE,
                "action": "view",
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
            JOIN permissions ON permissions.code = :permission_code
            WHERE roles.code IN :role_codes
            ON CONFLICT DO NOTHING
            """
        ).bindparams(sa.bindparam("role_codes", expanding=True)),
        {"permission_code": PERMISSION_CODE, "role_codes": list(GRANTED_ROLE_CODES)},
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
