"""`repair_records` pasa a llamarse `fault_outcomes`: no todo desenlace es una reparación.

Desde `c6d7e8f90055` la tabla guarda cinco tipos de desenlace y **tres no son
reparaciones**: un procedimiento, una revisión sin hallazgo y una falla del
propio equipo telemático. El nombre describía el caso particular con el que
nació y habría mentido para siempre.

Se renombra ahora porque la tabla está **vacía** y el módulo todavía no tiene
frontend escrito contra estos nombres. Es la misma disciplina con la que la
columna "Modelo" de Vehículos pasó a "Motor" cuando encabezado, contenido y
orden decían tres cosas distintas.

Sólo cambian nombres: ni una fila, ni un tipo, ni una restricción de contenido.

Revision ID: d7e8f9a00056
Revises: c6d7e8f90055
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7e8f9a00056"
down_revision: str | None = "c6d7e8f90055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "navifault"
OLD_TABLE = "repair_records"
NEW_TABLE = "fault_outcomes"

COLUMNS = {
    "repair_id": "outcome_id",
    "repair_action": "outcome_action",
    "repair_key": "outcome_key",
    "repair_detail": "outcome_detail",
    # El instante del desenlace es el que arranca la ventana de verificación de
    # 30 días. `repaired_at` sugería que siempre hubo una reparación.
    "repaired_at": "resolved_at",
}

CONSTRAINTS = {
    "ck_navifault_repair_outcome_type": "ck_navifault_outcome_type",
    "ck_navifault_repair_component_required": "ck_navifault_outcome_component_required",
    "repair_records_pkey": "fault_outcomes_pkey",
    "repair_records_case_id_fkey": "fault_outcomes_case_id_fkey",
    "repair_records_managed_action_id_fkey": "fault_outcomes_managed_action_id_fkey",
    "repair_records_fault_page_id_fkey": "fault_outcomes_fault_page_id_fkey",
    "repair_records_created_by_user_id_fkey": "fault_outcomes_created_by_user_id_fkey",
}

INDEXES = {
    "ix_navifault_repair_case_at": "ix_navifault_outcome_case_at",
    "ix_navifault_repair_page_key": "ix_navifault_outcome_page_key",
}


def _rename(*, table_from: str, table_to: str, columns: dict[str, str],
            constraints: dict[str, str], indexes: dict[str, str]) -> None:
    op.execute(f'ALTER TABLE {SCHEMA}."{table_from}" RENAME TO "{table_to}"')
    for old, new in columns.items():
        op.execute(f'ALTER TABLE {SCHEMA}."{table_to}" RENAME COLUMN "{old}" TO "{new}"')
    # Renombrar la constraint de la PK renombra también su índice.
    for old, new in constraints.items():
        op.execute(f'ALTER TABLE {SCHEMA}."{table_to}" RENAME CONSTRAINT "{old}" TO "{new}"')
    for old, new in indexes.items():
        op.execute(f'ALTER INDEX {SCHEMA}."{old}" RENAME TO "{new}"')


def upgrade() -> None:
    _rename(
        table_from=OLD_TABLE,
        table_to=NEW_TABLE,
        columns=COLUMNS,
        constraints=CONSTRAINTS,
        indexes=INDEXES,
    )


def downgrade() -> None:
    _rename(
        table_from=NEW_TABLE,
        table_to=OLD_TABLE,
        columns={v: k for k, v in COLUMNS.items()},
        constraints={v: k for k, v in CONSTRAINTS.items()},
        indexes={v: k for k, v in INDEXES.items()},
    )
