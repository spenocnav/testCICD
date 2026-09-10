"""El identificador de la falla cambia con el ciclo.

La referencia `NF-…` que el taller copia en los trabajos se derivaba de vehículo
+ firma, así que era la misma durante toda la vida de la falla. Eso abría dos
huecos simétricos al confirmar una gestión contra una orden: una orden vieja
lleva la misma marca, de modo que un ciclo nuevo podía cerrarse con trabajo de
hace meses; y un ciclo abierto desde hace mucho aceptaba cualquier trabajo por
tardío que fuera.

Con el ciclo dentro del identificador los dos desaparecen por construcción: un
trabajo marcado con la referencia del ciclo 2 sólo puede pertenecer al ciclo 2.
No hacen falta ventanas de fecha ni un registro de trabajos ya usados.

**El contador NO avanza cuando el ciclo nace, sino cuando alguien actúa sobre
él.** Un ciclo nuevo lo abre el paso del tiempo —una reaparición fuera de la
ventana de 30 días— y nadie escribe una fila en ese instante. Si la referencia
cambiara ahí, quien la copió el día 29 se encontraría con que la que pegó ya no
es la de la falla. Actuando como disparo, nadie puede tener pegada la referencia
de un ciclo en el que aún no ha trabajado nadie.

Una repetición dentro de los 30 días **no** avanza el contador: es el mismo
ciclo, el que la gestión no logró cerrar.

Revision ID: d3e4f5a60062
Revises: c2d3e4f50061
Create Date: 2026-09-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3e4f5a60062"
down_revision: str | None = "c2d3e4f50061"
branch_labels = None
depends_on = None

SCHEMA = "navifault"


def upgrade() -> None:
    # `server_default` para que los casos existentes queden en el ciclo 1, que
    # es lo que son: ninguno ha completado una ventana de 30 días en silencio.
    op.add_column(
        "managed_fault_cases",
        sa.Column("cycle_number", sa.Integer(), nullable=False, server_default="1"),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_navifault_case_cycle_number_positive",
        "managed_fault_cases",
        "cycle_number >= 1",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_navifault_case_cycle_number_positive",
        "managed_fault_cases",
        type_="check",
        schema=SCHEMA,
    )
    op.drop_column("managed_fault_cases", "cycle_number", schema=SCHEMA)
