"""La clave de un viaje no puede depender del `id` de Geotab.

Un Trip es un dato CALCULADO: mientras está en curso, el servidor reemplaza el
registro por uno NUEVO con OTRO `id` cada vez que llega telemetría, y también lo
regenera cuando reprocesa el histórico. Geotab documenta que la clave estable es
`deviceId` + inicio del viaje.

Con el `id` dentro de `trip_row_id`, reextraer una ventana ya cargada insertaba
el mismo viaje una segunda vez: 2.815 viajes duplicados en el silver y, para
WPK489 el 2026-08-06, 452,65 km de `Kms GPS` donde el historial de viajes
reportaba 226,32.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import make_trip_row_id

START = datetime(2026, 8, 6, 4, 58, 5, tzinfo=timezone.utc)


def test_mismo_viaje_recalculado_colapsa_en_una_clave():
    """Mismo device y mismo inicio ⇒ misma fila, aunque Geotab cambie el id."""
    primera = make_trip_row_id('navitrans', 'b356', START)
    recalculada = make_trip_row_id('navitrans', 'b356', START)
    assert primera == recalculada


def test_viajes_distintos_no_colisionan():
    otro_inicio = make_trip_row_id('navitrans', 'b356', START + timedelta(seconds=1))
    otro_device = make_trip_row_id('navitrans', 'b999', START)
    otra_base = make_trip_row_id('otra_base', 'b356', START)
    base = make_trip_row_id('navitrans', 'b356', START)
    assert len({base, otro_inicio, otro_device, otra_base}) == 4


def test_clave_estable_entre_datetime_y_timestamp():
    """El silver se relee como `pandas.Timestamp`; la extracción entrega
    `datetime`. Ambos tienen que hashear igual o una migración reescribiría
    claves que no cambiaron."""
    assert make_trip_row_id('navitrans', 'b356', START) == make_trip_row_id(
        'navitrans', 'b356', pd.Timestamp(START)
    )


def test_clave_estable_ante_representacion_del_instante():
    """Naive-UTC, con offset y con microsegundos explícitos son el mismo instante."""
    naive = make_trip_row_id('navitrans', 'b356', START.replace(tzinfo=None))
    con_offset = make_trip_row_id('navitrans', 'b356', START)
    texto = make_trip_row_id('navitrans', 'b356', '2026-08-06T04:58:05.000000+00:00')
    otra_zona = make_trip_row_id(
        'navitrans', 'b356', START.astimezone(timezone(timedelta(hours=-5)))
    )
    assert naive == con_offset == texto == otra_zona


def test_start_ausente_no_revienta():
    assert make_trip_row_id('navitrans', 'b356', None)
    assert make_trip_row_id('navitrans', 'b356', pd.NaT)
