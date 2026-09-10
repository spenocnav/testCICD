"""penalizaciones calibrables en la calificación por flota

Revision ID: r4s5t6u70044
Revises: q3r4s5t60043
Create Date: 2026-08-28

La anulación del puntaje por superar la sobrevelocidad máxima del motor era una
regla fija. Pasa a ser calibrable por flota, y no como un booleano suelto sino
como un REGISTRO de penalizaciones (`app.services.calificacion_config.
PENALIZACIONES`): se esperan más, y cada una nueva debe poder añadirse sin
migración.

Por eso la columna es un `JSONB` con la forma `{code: activa}` y no una columna
booleana por penalización. Es el mismo razonamiento de `qhs_event_weights`.

Nullable y sin `server_default`, como el resto de los parámetros de esta tabla:

- una fila escrita antes de que existiera esta columna —o antes de que existiera
  una penalización concreta— tiene que seguir siendo legible, y el lector cae al
  default del registro cuando la clave falta;
- un `server_default` congelaría dentro de cada fila la decisión vigente hoy: el
  día que una penalización nueva nazca activa por defecto, las filas viejas
  seguirían arrastrando el mapa antiguo sin que nadie lo haya decidido.

**Esta migración no cambia ningún puntaje.** Todas las filas existentes quedan
con `penalizaciones = NULL`, que se resuelve al default del registro, y ese
default es `activa=True` para la sobrevelocidad: exactamente el comportamiento
que la calificación ya tenía.

El único check es de forma y tolerante con NULL. Los códigos válidos los valida
`validate_config`, que es la autoridad; SQL sólo atajaría una escritura hecha por
fuera del servicio, y lo que ahí importa es que un escalar o un arreglo no
lleguen a la lectura: `_resolve_row` sólo atrapa `CalificacionConfigError`, así
que un `TypeError` al leerlos tumbaría el reporte en vez de caer a los defaults.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "r4s5t6u70044"
down_revision: str | None = "q3r4s5t60043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "calificacion_config_versions"
COLUMN = "penalizaciones"
CHECK = "ck_calificacion_config_penalizaciones_objeto"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(COLUMN, postgresql.JSONB(), nullable=True),
    )
    op.create_check_constraint(
        CHECK,
        TABLE,
        f"{COLUMN} IS NULL OR jsonb_typeof({COLUMN}) = 'object'",
    )


def downgrade() -> None:
    op.drop_constraint(CHECK, TABLE, type_="check")
    op.drop_column(TABLE, COLUMN)
