"""exposición mínima y modo de promedio, calibrables por flota

Revision ID: z3a4b5c60052
Revises: y2z3a4b50051
Create Date: 2026-09-01

QHS y QHO son tasas: eventos por 1000 km y tiempo sobre tiempo. Con un
denominador minúsculo dejan de medir conducción y pasan a medir que el vehículo
no se movió —es imposible registrar un evento de seguridad en 200 metros, así
que el QHS sale 100—, y ese puntaje entraba en la tabla, en el donut y en el
promedio como si fuera una medición.

Estas dos columnas guardan la exposición mínima POR DÍA del periodo consultado.
Por día y no absoluta a propósito: la pantalla consulta tres meses por defecto,
un mes al hacer clic en la serie mensual y lo que el usuario elija en el filtro;
un mínimo fijo en kilómetros sería indulgente en un rango largo y absurdo en uno
corto.

Nullable y sin `server_default`, como el resto de los parámetros de la tabla:
una fila escrita antes de que existieran tiene que seguir siendo legible, y
`config_from_mapping` cae al default del código cuando la clave falta. Un
`server_default` congelaría dentro de cada fila el número vigente hoy.

**Esta migración no cambia ningún puntaje por sí sola**: sólo añade columnas en
NULL. El cambio de comportamiento lo introduce el default del código
(5 km/día · 0,5 h ECM/día), que sí deja sin calificar a los vehículos-mes por
debajo de esa marca. Una flota que prefiera el comportamiento anterior guarda 0
en ambos campos.

El check acepta 0 —es el apagado explícito— y es tolerante con NULL, como los
demás de esta tabla: la autoridad de validación es
`app.services.calificacion_config.validate_config`.

La tercera columna, `promedio_ponderado`, decide cómo se agrega el puntaje de
los vehículos en la cifra de la flota: ponderado por exposición (el
comportamiento histórico, y el default) o promedio simple, un vehículo un voto.
Ninguno de los dos es "el correcto" —son dos preguntas distintas— y por eso la
elección es del cliente. NULL cae al default, así que las filas existentes
conservan el ponderado. Es nullable como los demás parámetros, a diferencia de
`is_reset`, que es un booleano de la fila y no un parámetro.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "z3a4b5c60052"
down_revision: str | None = "y2z3a4b50051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "calificacion_config_versions"
COLUMNS = ("exposicion_minima_km_dia", "exposicion_minima_horas_dia")
BOOL_COLUMN = "promedio_ponderado"


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column(TABLE, sa.Column(column, sa.Float(), nullable=True))
        op.create_check_constraint(
            f"ck_calificacion_config_{column}",
            TABLE,
            f"{column} IS NULL OR {column} >= 0",
        )
    # Sin check: un booleano nullable ya sólo admite tres valores.
    op.add_column(TABLE, sa.Column(BOOL_COLUMN, sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column(TABLE, BOOL_COLUMN)
    for column in COLUMNS:
        op.drop_constraint(f"ck_calificacion_config_{column}", TABLE, type_="check")
        op.drop_column(TABLE, column)
