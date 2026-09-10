"""Escalar una falla a novedades de CloudFleet.

Escalar no es "pedir un favor" ni un paso previo a gestionar: es **cambiar el
régimen de seguimiento** cuando la falla no se puede resolver ya. El seguimiento
estricto del portal se hace en CloudFleet, que es donde el equipo mira cuando va
a intervenir el vehículo.

Por eso escalar **no** marca la falla como gestionada: la reparación no ocurrió,
y arrancar sobre eso la verificación de 30 días falsearía la efectividad de los
desenlaces y el tiempo promedio de gestión.

`ON DELETE SET NULL` en la novedad y no `RESTRICT`: `DELETE /novedades/{id}`
existe y es de admin. Borrar la copia local de una novedad no debe impedirse por
un enlace de Navifault, pero tampoco debe borrar el caso — el caso es el estado
de la falla, no el del ticket.

**El cambio delicado es la nulabilidad.** Un caso creado SÓLO por un
escalamiento no tiene gestión, así que `last_managed_at` y
`last_managed_by_user_id` dejan de ser obligatorias. Las dos alternativas se
descartaron:

- rellenar `last_managed_at` con la hora del escalamiento **mentiría**: esa
  columna ancla la ventana de reincidencia y el filtro de reaparición, así que
  un escalamiento se contaría como gestión y la bandeja mostraría a quien escaló
  en la columna "Gestionada por";
- no crear la fila hasta que alguien gestione dejaría el escalamiento sin dónde
  vivir, y además sacaría a las fallas escaladas del camino acotado de la
  bandeja: entrarían por el que barre el hecho, que cuesta 23 s.

Que un `last_managed_at` NULL no caiga en la rama `else -> managed` hay que
**verificarlo con una prueba**, no por inspección: esa rama es el modo de fallo
peligroso, una falla escalada apareciendo como gestionada.

El CHECK de la bitácora se amplía a `escalated`. Precedente exacto:
`u7v8w90047` tuvo que ampliar `ck_sync_run_kind` porque la fila de auditoría se
perdía **en silencio** — la auditoría es best-effort y no levanta el error.

Revision ID: e8f9a0b10057
Revises: d7e8f9a00056
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8f9a0b10057"
down_revision: str | None = "d7e8f9a00056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "navifault"
CASES = "managed_fault_cases"
ACTIONS = "managed_fault_actions"
ACTION_CHECK = "ck_navifault_managed_action_type"
ESCALATION_INDEX = "ix_navifault_managed_case_escalated"


def upgrade() -> None:
    op.add_column(
        CASES,
        sa.Column("escalated_novedad_id", UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        CASES,
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        CASES,
        sa.Column("escalated_by_user_id", UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "managed_fault_cases_escalated_novedad_id_fkey",
        CASES,
        "novedades",
        ["escalated_novedad_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema="public",
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "managed_fault_cases_escalated_by_user_id_fkey",
        CASES,
        "users",
        ["escalated_by_user_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema="public",
        ondelete="RESTRICT",
    )
    # Parcial: la inmensa mayoría de los casos no está escalada, y el índice
    # sirve para encontrar los que sí.
    op.create_index(
        ESCALATION_INDEX,
        CASES,
        ["escalated_novedad_id"],
        schema=SCHEMA,
        postgresql_where=sa.text("escalated_novedad_id IS NOT NULL"),
    )

    op.alter_column(CASES, "last_managed_at", nullable=True, schema=SCHEMA)
    op.alter_column(CASES, "last_managed_by_user_id", nullable=True, schema=SCHEMA)

    op.drop_constraint(ACTION_CHECK, ACTIONS, schema=SCHEMA, type_="check")
    op.create_check_constraint(
        ACTION_CHECK,
        ACTIONS,
        "action_type IN ('managed', 'unmanaged', 'escalated')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    bind = op.get_bind()
    huerfanos = bind.execute(
        sa.text(
            f'SELECT count(*) FROM {SCHEMA}."{CASES}" '
            "WHERE last_managed_at IS NULL OR last_managed_by_user_id IS NULL"
        )
    ).scalar_one()
    if huerfanos:
        raise RuntimeError(
            f"{huerfanos} caso(s) existen sólo por un escalamiento y no tienen gestión. "
            "Restaurar el NOT NULL exigiría inventarles una fecha y un responsable; "
            "resuélvelos o bórralos antes de revertir."
        )

    bind.execute(
        sa.text(
            f"DELETE FROM {SCHEMA}.\"{ACTIONS}\" WHERE action_type = 'escalated'"
        )
    )
    op.drop_constraint(ACTION_CHECK, ACTIONS, schema=SCHEMA, type_="check")
    op.create_check_constraint(
        ACTION_CHECK,
        ACTIONS,
        "action_type IN ('managed', 'unmanaged')",
        schema=SCHEMA,
    )

    op.alter_column(CASES, "last_managed_by_user_id", nullable=False, schema=SCHEMA)
    op.alter_column(CASES, "last_managed_at", nullable=False, schema=SCHEMA)

    op.drop_index(ESCALATION_INDEX, table_name=CASES, schema=SCHEMA)
    op.drop_constraint(
        "managed_fault_cases_escalated_by_user_id_fkey", CASES, schema=SCHEMA, type_="foreignkey"
    )
    op.drop_constraint(
        "managed_fault_cases_escalated_novedad_id_fkey", CASES, schema=SCHEMA, type_="foreignkey"
    )
    op.drop_column(CASES, "escalated_by_user_id", schema=SCHEMA)
    op.drop_column(CASES, "escalated_at", schema=SCHEMA)
    op.drop_column(CASES, "escalated_novedad_id", schema=SCHEMA)
