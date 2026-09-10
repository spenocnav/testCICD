"""concede reportes.edit al rol admin_flota_cliente

Revision ID: q3r4s5t60043
Revises: p2q3r4s50042
Create Date: 2026-08-27

`reportes.edit` existía en el catálogo desde la canonicalización
(`z6a7b8c90026`) pero ningún endpoint lo exigía, así que su reparto entre roles
nunca se revisó: lo tenían `admin`, `admin_flota_navitrans` y `gerente_cuenta`,
y NO lo tenía `admin_flota_cliente`.

`p2q3r4s50042` le dio un uso: la calibración de la calificación por flota
(`PUT/DELETE /reportes/calificacion/config` y el historial). Con el reparto
anterior el cliente no podía recalibrar su propia flota, que es justo para quien
se construyó la función. Esta migración concede el permiso a
`admin_flota_cliente`.

Alcance de lo que habilita, para que quede escrito: quien tenga `reportes.edit`
puede cambiar los pesos y umbrales con los que se califica **su** flota, y eso
recalcula el puntaje histórico de esa flota. NO habilita tocar lo que depende del
motor —velocidad gobernada, sobrevelocidad máxima, rangos de RPM—, que viene de
Navi Vehículos, ni desactivar la anulación del puntaje por sobrevelocidad. El
alcance de flota se verifica aparte en cada endpoint: el permiso no da acceso a
la calibración de una flota ajena.

`reportes.view` ya lo tenía el rol, así que la inferencia "edit implica view" del
modelo RBAC no requiere nada extra aquí; se concede igual con `ON CONFLICT DO
NOTHING` para que la migración sea válida en una base donde falte.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "q3r4s5t60043"
down_revision: str | None = "p2q3r4s50042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_CODE = "admin_flota_cliente"
PERMISSION_CODES = ("reportes.view", "reportes.edit")


def upgrade() -> None:
    bind = op.get_bind()
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
            {"role_code": ROLE_CODE, "permission_code": permission_code},
        )


def downgrade() -> None:
    """Retira sólo `reportes.edit` de este rol.

    `reportes.view` NO se retira: el rol ya lo tenía antes de esta migración y
    quitarlo dejaría al cliente sin ver sus propios reportes, que es un daño
    mayor y ajeno a lo que esta migración introdujo.
    """
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            DELETE FROM role_permissions
            WHERE role_id IN (SELECT id FROM roles WHERE code = :role_code)
              AND permission_id IN (SELECT id FROM permissions WHERE code = 'reportes.edit')
            """
        ),
        {"role_code": ROLE_CODE},
    )
