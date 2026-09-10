"""La referencia que ata los trabajos, y el estado real de la orden.

Dos columnas sobre `novedades`, aditivas y nullable.

`navifault_reference` es un identificador corto que viaja dentro del comentario
de la novedad. CloudFleet **no publica** el enlace entre una novedad y el trabajo
que la resuelve —lo pinta en su interfaz y no lo expone por API, en ninguna de
las dos direcciones— y además sólo admite un trabajo por novedad. La referencia
resuelve las dos cosas: el taller la copia en cada trabajo que corresponda a esa
novedad, y esa copia es una declaración explícita, no una coincidencia de texto
que hubiera que interpretar.

`external_work_order_status` guarda el estado de la orden enlazada, que es lo
único que distingue "el taller la tiene" de "el taller terminó".
`external_is_done` NO sirve para eso: se activa en el momento de asignar la
novedad a una orden, con la orden todavía abierta y sin intervenir el vehículo.

Revision ID: b1c2d3e40060
Revises: a0b1c2d30059
Create Date: 2026-09-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e40060"
down_revision: str | None = "a0b1c2d30059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "novedades",
        sa.Column("navifault_reference", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "novedades",
        sa.Column("external_work_order_status", sa.String(length=64), nullable=True),
    )
    # Única: la referencia se busca dentro del texto de los trabajos y dos
    # novedades con la misma marca harían ambiguo justo lo que vino a desambiguar.
    op.create_index(
        "ux_novedades_navifault_reference",
        "novedades",
        ["navifault_reference"],
        unique=True,
        postgresql_where=sa.text("navifault_reference IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_novedades_navifault_reference", table_name="novedades")
    op.drop_column("novedades", "external_work_order_status")
    op.drop_column("novedades", "navifault_reference")
