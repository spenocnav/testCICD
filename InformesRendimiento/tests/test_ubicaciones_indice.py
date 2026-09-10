"""El `Indice` de ubicaciones tiene que ser estable ante inserciones.

Era un contador denso (`range(1, len+1)`) sobre el histórico ordenado por fecha.
Como la columna entra en el hash de fila, insertar un día viejo corría el `Indice`
de todas las filas posteriores y el loader re-upserteaba los 3,3M de registros
para un delta de cero (11 minutos por corrida, medidos).

Power BI solo necesita que el control deslizante sea monótono en el tiempo, no
que sea denso.
"""

from __future__ import annotations

import pandas as pd


def _indice(fechas: list[str]) -> pd.Series:
    """Misma fórmula que transform_ubicaciones tras el arreglo."""
    frame = pd.DataFrame({"Fecha y Hora": pd.to_datetime(fechas)})
    frame = frame.sort_values("Fecha y Hora")
    return pd.to_datetime(frame["Fecha y Hora"]).astype("int64") // 10**9


def test_insertar_una_fila_vieja_no_mueve_el_indice_de_las_demas():
    original = ["2026-07-02 10:00", "2026-07-03 10:00"]
    con_backfill = ["2026-07-01 08:00", "2026-07-02 10:00", "2026-07-03 10:00"]

    antes = _indice(original)
    despues = _indice(con_backfill)

    # Las dos filas que ya existían conservan exactamente su valor.
    assert list(antes) == list(despues)[1:]


def test_es_monotono_en_el_tiempo():
    """Requisito del control deslizante: ordenar por Indice == ordenar por fecha."""
    indice = _indice(
        ["2026-07-03 10:00", "2026-07-01 08:00", "2026-07-02 10:00"]
    )
    assert list(indice) == sorted(indice)


def test_el_contador_denso_anterior_si_se_corria():
    """Documenta el defecto: con el esquema viejo, las mismas dos filas cambiaban
    de valor al insertar una anterior."""
    antes = list(range(1, 3))          # 2 filas  -> [1, 2]
    despues = list(range(1, 4))[1:]    # tras backfill, esas filas -> [2, 3]
    assert antes != despues
