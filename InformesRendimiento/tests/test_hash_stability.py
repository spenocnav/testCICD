"""El hash de fila no puede depender de la composición del lote.

`row_hashes` decide qué filas se re-upsertean. El dtype que infiere pandas
depende de qué filas entraron: la misma columna con los mismos valores sale
`int64` si todas son enteras y `float64` si una trae NaN o decimal. Al reducir el
catálogo de 250 a 16 vehículos, `Tiempo Total en Rango de Descenso` cambió de
float64 a int64 con valores idénticos y el loader re-upserteó la tabla entera.

En PostgreSQL el tipo de columna lo fijó `create_table` y no cambia, así que dos
dtypes de pandas con el mismo valor se guardan igual: hashearlos distinto es un
falso positivo.
"""

from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from load.db import _MAX_EXACT_FLOAT_INT, row_hashes


def test_int_y_float_con_el_mismo_valor_hashean_igual():
    """El caso exacto que disparó el re-upsert masivo."""
    como_int = pd.DataFrame({"pk": ["a", "b"], "total": [0, 5]})
    como_float = pd.DataFrame({"pk": ["a", "b"], "total": [0.0, 5.0]})

    assert como_int["total"].dtype != como_float["total"].dtype
    assert list(row_hashes(como_int)) == list(row_hashes(como_float))


def test_un_cambio_real_de_valor_si_cambia_el_hash():
    """La estabilidad no puede volverse ceguera: si el dato cambia, se upsertea."""
    antes = pd.DataFrame({"pk": ["a"], "total": [5]})
    despues = pd.DataFrame({"pk": ["a"], "total": [6]})

    assert list(row_hashes(antes)) != list(row_hashes(despues))


def test_enteros_fuera_del_rango_exacto_no_se_castean():
    """Sobre 2^53 el cast a float64 pierde precisión y colapsaría dos valores
    distintos en el mismo hash. Ahí se prefiere el dtype original."""
    a = pd.DataFrame({"pk": ["x"], "big": [_MAX_EXACT_FLOAT_INT + 1]})
    b = pd.DataFrame({"pk": ["x"], "big": [_MAX_EXACT_FLOAT_INT + 3]})

    assert list(row_hashes(a)) != list(row_hashes(b))


def test_columnas_de_texto_no_se_tocan():
    iguales = pd.DataFrame({"pk": ["a"], "placa": ["ABC123"]})
    distintas = pd.DataFrame({"pk": ["a"], "placa": ["ABC124"]})

    assert list(row_hashes(iguales)) == list(row_hashes(iguales.copy()))
    assert list(row_hashes(iguales)) != list(row_hashes(distintas))


def test_frame_vacio():
    assert len(row_hashes(pd.DataFrame())) == 0
