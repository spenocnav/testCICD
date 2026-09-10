"""rol de sistema `reportante_novedades`: sólo registra y consulta novedades

Revision ID: v8w9x0y10048
Revises: u7v8w90047
Create Date: 2026-08-28

Se le entregan a un cliente credenciales que sólo sirven para reportar
novedades, probablemente usadas por conductores desde el celular. Hasta esta
migración `novedades.view`/`novedades.edit` estaban concedidos únicamente a
`admin` (b2d4f6a80002), así que no existía ningún rol que diera acceso al
módulo sin dar acceso a todo lo demás.

Qué habilita: `/novedades` (listar y ver detalle de las novedades de SUS
flotas asignadas), `/novedades/nuevo` (crear, con adjuntos) y el catálogo de
placas de esas flotas (`GET /vehicles` acepta `novedades.view`). El alcance
sigue siendo por flota, como el resto del portal: el rol no ve novedades de
flotas que no tenga asignadas.

Qué NO habilita: reportes, mantenimiento, Navifault, gestión de usuarios,
roles o flotas, ni borrar novedades (eso exige rol admin).

`novedades.view` se concede explícito aunque `edit` lo implique en tiempo de
ejecución, igual que hacen las demás migraciones de roles: la matriz de la
pantalla de roles lee `role_permissions`, no la inferencia.

Es rol de sistema (`is_system=true`) para que no se pueda borrar desde la UI
y exista igual en todos los entornos. El downgrade sólo lo retira si ningún
usuario lo tiene: quitarle el rol a alguien es una decisión operativa, no
algo que deba hacer un `alembic downgrade`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "v8w9x0y10048"
down_revision: str | None = "u7v8w90047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE = {
    "code": "reportante_novedades",
    "name": "Reportante de novedades",
    "description": (
        "Registra y consulta novedades de sus flotas asignadas. "
        "Sin acceso a reportes, mantenimiento ni gestión."
    ),
}
PERMISSION_CODES = ("novedades.view", "novedades.edit")


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)
    bind.execute(
        sa.text(
            """
            INSERT INTO roles (id, code, name, description, is_system, created_at, updated_at)
            VALUES (:id, :code, :name, :description, true, :now, :now)
            ON CONFLICT (code) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                is_system = true,
                updated_at = EXCLUDED.updated_at
            """
        ),
        {**ROLE, "id": uuid.uuid4(), "now": now},
    )
    for permission_code in PERMISSION_CODES:
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
            {"role_code": ROLE["code"], "permission_code": permission_code},
        )


def downgrade() -> None:
    bind = op.get_bind()
    assigned = bind.execute(
        sa.text(
            """
            SELECT count(*) FROM user_roles ur
            JOIN roles r ON r.id = ur.role_id
            WHERE r.code = :role_code
            """
        ),
        {"role_code": ROLE["code"]},
    ).scalar_one()
    if assigned:
        # Hay usuarios con el rol: se conserva. Reasignarlos es decisión humana.
        return
    bind.execute(
        sa.text("DELETE FROM roles WHERE code = :role_code AND is_system = true"),
        {"role_code": ROLE["code"]},
    )
