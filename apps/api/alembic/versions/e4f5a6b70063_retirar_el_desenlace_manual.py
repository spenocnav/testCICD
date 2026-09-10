"""Se retira el desenlace manual: la gestión es confirmar lo del taller.

`fault_outcomes` existía para que una persona declarara en el portal qué se
hizo, con su tipo cerrado, su componente obligatorio y sus sugerencias entre
flotas. Ese modelo abría una segunda versión de los hechos que nada obligaba a
coincidir con la orden de CloudFleet. Desde ahora una falla se gestiona
confirmando el registro del taller, que es la única fuente de verdad de lo que
se hizo, así que la tabla y su endpoint dejan de tener sentido.

Se dropea y no se deja huérfana porque, retirado el modelo, una tabla sin
modelo produce deriva silenciosa entre esquema y código: un `--autogenerate`
futuro propondría el mismo DROP sin que nadie recordara por qué estaba ahí. La
tabla se verificó **vacía** antes de retirarla.

`actor_user_id` pasa a nullable porque aparece un autor que no es una persona:
cuando la orden deja de sostener una gestión —se anula, o le quitan el trabajo
con la referencia— el portal revierte solo y esa reversión tiene que constar en
la bitácora. Deshacer a mano se retira: dejaría el proceso de CloudFleet
huérfano, y la única forma de revertir es revertir allá.

Revision ID: e4f5a6b70063
Revises: d3e4f5a60062
Create Date: 2026-09-05
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4f5a6b70063"
down_revision: str | None = "d3e4f5a60062"
branch_labels = None
depends_on = None

SCHEMA = "navifault"


def upgrade() -> None:
    conn = op.get_bind()
    pendientes = conn.execute(
        sa.text(f"SELECT count(*) FROM {SCHEMA}.fault_outcomes")
    ).scalar_one()
    if pendientes:
        # Clasificar desenlaces existentes contra el registro del taller es una
        # decisión humana, no algo que una migración pueda resolver sola.
        raise RuntimeError(
            f"{SCHEMA}.fault_outcomes tiene {pendientes} filas: no se retira sin decidir "
            "qué hacer con ellas."
        )
    op.drop_table("fault_outcomes", schema=SCHEMA)
    op.alter_column(
        "managed_fault_actions", "actor_user_id", nullable=True, schema=SCHEMA
    )


def downgrade() -> None:
    conn = op.get_bind()
    sin_autor = conn.execute(
        sa.text(
            f"SELECT count(*) FROM {SCHEMA}.managed_fault_actions WHERE actor_user_id IS NULL"
        )
    ).scalar_one()
    if sin_autor:
        raise RuntimeError(
            f"{sin_autor} acciones sin autor: son reversiones automáticas y restaurar el "
            "NOT NULL las perdería."
        )
    op.alter_column(
        "managed_fault_actions", "actor_user_id", nullable=False, schema=SCHEMA
    )
    # La tabla se recrea con los nombres EXACTOS que la cadena espera después de
    # `d7e8f9a00056`: su bajada renombra estas constraints e índices y falla si
    # no existen. Con la tabla vacía no hay datos que restaurar, pero el
    # esqueleto tiene que coincidir o `downgrade base` se rompe.
    op.create_table(
        "fault_outcomes",
        sa.Column("outcome_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("managed_action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fault_page_id", sa.String(length=96), nullable=True),
        sa.Column("outcome_type", sa.String(length=32), nullable=False),
        sa.Column("outcome_action", sa.String(length=240), nullable=False),
        sa.Column("outcome_key", sa.String(length=256), nullable=False),
        sa.Column("component_name", sa.String(length=240), nullable=True),
        sa.Column("outcome_detail", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            [f"{SCHEMA}.managed_fault_cases.case_id"],
            name="fault_outcomes_case_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["managed_action_id"],
            [f"{SCHEMA}.managed_fault_actions.action_id"],
            name="fault_outcomes_managed_action_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fault_page_id"],
            [f"{SCHEMA}.fault_pages.fault_page_id"],
            name="fault_outcomes_fault_page_id_fkey",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fault_outcomes_created_by_user_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("outcome_id", name="fault_outcomes_pkey"),
        sa.CheckConstraint(
            "outcome_type IN ('cambio_componente', 'reparacion_componente', "
            "'procedimiento', 'sin_hallazgo', 'dispositivo_telematico')",
            name="ck_navifault_outcome_type",
        ),
        sa.CheckConstraint(
            "(outcome_type NOT IN ('cambio_componente', 'reparacion_componente')) "
            "OR (component_name IS NOT NULL AND btrim(component_name) <> '')",
            name="ck_navifault_outcome_component_required",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_navifault_outcome_case_at",
        "fault_outcomes",
        ["case_id", "resolved_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_navifault_outcome_page_key",
        "fault_outcomes",
        ["fault_page_id", "outcome_key"],
        schema=SCHEMA,
    )
