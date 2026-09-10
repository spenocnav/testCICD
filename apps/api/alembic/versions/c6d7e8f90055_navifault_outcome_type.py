"""Gestionar una falla exige declarar QUÉ se hizo: el tipo de desenlace.

Hasta aquí `mark-managed` sólo pedía una nota libre, y `create_repair_record`
**exigía que la falla ya estuviera gestionada** —o sea, la reparación era un
adjunto posterior y opcional—. El equipo definió lo contrario: una falla se
gestiona si y sólo si alguien declara el desenlace. Esta revisión prepara el
esquema; la inversión de la precondición va en la capa de servicio.

El tipo es una **lista cerrada** a propósito. `repair_action` es texto libre y
su `repair_key` sólo normaliza mayúsculas y acentos, así que "Cambio de sensor
NOx" y "cambio sensor de NOx" ya son dos grupos distintos en la estadística de
efectividad entre equipos. El tipo es el único eje que no se fragmenta, y es lo
que permite preguntar "para este código de falla, ¿funciona mejor cambiar la
pieza o hacer el procedimiento?".

Por qué cinco y no dos:

- separar **cambio** de **reparación** de componente no es burocracia: para el
  mismo código de falla, cambiar el sensor de NOx y reparar el arnés de ese
  sensor son dos historias de efectividad distintas y mezclarlas es justo el
  ruido que se quiere evitar;
- **sin hallazgo** es un desenlace legítimo y frecuente en diagnóstico. Sin él
  la única salida sería inventar una reparación o dejar la falla eternamente sin
  gestionar;
- **dispositivo telemático** existe porque el 15,9 % de los eventos del hecho
  (165.030 filas) vienen de `SourceGeotabGoId`, o sea el equipo Geotab hablando
  de sí mismo. Los reportes ya los excluyen; Navifault no, a propósito. De paso
  da la medida de cuánto ruido del equipo consume tiempo de quien administra.

El componente es obligatorio en los dos tipos de reparación y sólo en ellos: es
lo que hace comparable el historial entre equipos, y exigirlo en un
procedimiento o en un "sin hallazgo" obligaría a inventarlo.

**La columna nace NOT NULL sin `server_default`.** La tabla está vacía
—verificado— y un default silenciaría exactamente la decisión que se quiere
forzar: clasificaría solo cualquier inserción futura que se olvide del tipo.

Nota de despliegue: entre esta revisión y la capa de servicio,
`POST /management/repairs` no puede insertar. Es inalcanzable en la práctica
—exige `navifault.edit` (4 usuarios) sobre un caso ya gestionado, y no existe
ninguno— pero conviene aplicar las dos seguidas.

El nombre de la tabla se conserva. `repair_records` ya no describe bien lo que
guarda —tres de los cinco desenlaces no son reparaciones— pero renombrarla
arrastra modelo, servicio, endpoints y frontend, y ese es un cambio aparte. Con
la tabla vacía sigue siendo barato hacerlo después.

Revision ID: c6d7e8f90055
Revises: b5c6d7e80054
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c6d7e8f90055"
down_revision: str | None = "b5c6d7e80054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "navifault"
TABLE = "repair_records"

OUTCOME_TYPES = (
    "cambio_componente",
    "reparacion_componente",
    "procedimiento",
    "sin_hallazgo",
    "dispositivo_telematico",
)
COMPONENT_REQUIRED = ("cambio_componente", "reparacion_componente")

CK_TYPE = "ck_navifault_repair_outcome_type"
CK_COMPONENT = "ck_navifault_repair_component_required"


def _quoted(values: Sequence[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    bind = op.get_bind()
    existentes = bind.execute(sa.text(f'SELECT count(*) FROM {SCHEMA}."{TABLE}"')).scalar_one()
    if existentes:
        raise RuntimeError(
            f"{SCHEMA}.{TABLE} tiene {existentes} filas y la columna nace NOT NULL sin default. "
            "Clasificar desenlaces existentes es una decisión humana, no de una migración."
        )

    op.add_column(
        TABLE,
        sa.Column("outcome_type", sa.String(length=32), nullable=False),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        CK_TYPE,
        TABLE,
        f"outcome_type IN ({_quoted(OUTCOME_TYPES)})",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        CK_COMPONENT,
        TABLE,
        (
            f"outcome_type NOT IN ({_quoted(COMPONENT_REQUIRED)}) "
            "OR (component_name IS NOT NULL AND btrim(component_name) <> '')"
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(CK_COMPONENT, TABLE, schema=SCHEMA, type_="check")
    op.drop_constraint(CK_TYPE, TABLE, schema=SCHEMA, type_="check")
    op.drop_column(TABLE, "outcome_type", schema=SCHEMA)
