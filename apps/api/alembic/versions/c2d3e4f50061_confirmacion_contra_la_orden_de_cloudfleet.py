"""Gestionar una falla pasa a ser confirmar lo que el taller registró.

Navifault deja de ser donde se declara un desenlace y pasa a ser donde se
confirma el de CloudFleet. El motivo es tener una sola fuente de verdad: el
registro del taller. Declarar el desenlace a mano en el portal abría una segunda
versión de los hechos que nada obligaba a coincidir con la orden.

`confirmed_work_order_number` en el caso es la orden que **sostiene** la gestión
vigente. No es informativa: es lo que se vuelve a mirar para saber si la gestión
sigue en pie. Si esa orden se anula, o le quitan el trabajo con la referencia, la
falla vuelve sola a sin gestionar —y a escalada si su novedad sigue viva—, sin
que nadie deshaga nada desde el portal. Deshacer a mano dejaría el proceso de
CloudFleet huérfano, que es justo lo que este modelo impide.

En la bitácora, `work_order_number` y `details` guardan la COPIA de lo
confirmado: el trabajo, su sistema, su tipo de mantenimiento y sus repuestos.
Copia y no identificadores porque anular una orden borra sus trabajos y
repuestos —comprobado el 2026-09-05 sobre la orden 5733—, y entonces la
declaración quedaría apuntando a nada.

Revision ID: c2d3e4f50061
Revises: b1c2d3e40060
Create Date: 2026-09-05
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2d3e4f50061"
down_revision: str | None = "b1c2d3e40060"
branch_labels = None
depends_on = None

SCHEMA = "navifault"


def upgrade() -> None:
    op.add_column(
        "managed_fault_cases",
        sa.Column("confirmed_work_order_number", sa.BigInteger(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "managed_fault_actions",
        sa.Column("work_order_number", sa.BigInteger(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "managed_fault_actions",
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema=SCHEMA,
    )
    # El vigilante recorre las gestiones vivas para comprobar que su orden
    # sigue sosteniéndolas; son pocas y el índice parcial las aísla.
    op.create_index(
        "ix_navifault_case_confirmed_order",
        "managed_fault_cases",
        ["confirmed_work_order_number"],
        schema=SCHEMA,
        postgresql_where=sa.text("confirmed_work_order_number IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_navifault_case_confirmed_order", table_name="managed_fault_cases", schema=SCHEMA
    )
    op.drop_column("managed_fault_actions", "details", schema=SCHEMA)
    op.drop_column("managed_fault_actions", "work_order_number", schema=SCHEMA)
    op.drop_column("managed_fault_cases", "confirmed_work_order_number", schema=SCHEMA)
