"""`partition_by_vehicle` debe ser equivalente al filtro que reemplazó.

El transform de combustible recorría `dim_vehiculos` y, por cada vehículo,
volvía a filtrar los DataFrames globales (`df[df['vehicle_id'] == vid]`): cinco
barridos completos por vehículo sobre ~10,6M filas de status. Ahora se particiona
una sola vez con `groupby`.

El refactor solo vale si el sub-frame es EXACTAMENTE el mismo: mismas filas, mismo
orden, mismo índice y mismos dtypes. Cualquier diferencia cambia los cálculos
aguas abajo en silencio.
"""

from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from transform.transform_combustible import _slice_for, partition_by_vehicle


def _frame() -> pd.DataFrame:
    """Orden entrelazado a propósito: si `groupby` reordenara, se nota."""
    return pd.DataFrame(
        {
            "vehicle_id": ["v1", "v2", "v1", "v3", "v2", "v1"],
            "diagnostic_id": ["d1", "d2", "d2", "d1", "d1", "d1"],
            "data": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        }
    )


def test_particion_equivale_a_la_mascara_booleana():
    df = _frame()
    index = partition_by_vehicle(df)

    for vid in ("v1", "v2", "v3"):
        esperado = df[df["vehicle_id"] == vid]
        obtenido = _slice_for(index, df, vid)
        # check_like=False: el ORDEN de filas y el índice también deben coincidir.
        pd.testing.assert_frame_equal(obtenido, esperado)


def test_vehiculo_sin_filas_conserva_el_schema():
    """Aguas abajo se consultan columnas aunque no haya filas; un DataFrame
    vacío sin columnas rompería `veh_status['diagnostic_id']`."""
    df = _frame()
    index = partition_by_vehicle(df)

    vacio = _slice_for(index, df, "no-existe")
    assert vacio.empty
    assert list(vacio.columns) == list(df.columns)
    assert vacio.dtypes.to_dict() == df.dtypes.to_dict()
    # La operación que hace compute_revision_flag no debe explotar.
    assert vacio[vacio["diagnostic_id"] == "d1"].empty


def test_frame_vacio_o_sin_columna_no_rompe():
    """`fact_trips` puede no existir todavía: el transform pasa un DataFrame
    vacío y el particionador tiene que devolver un índice vacío, no explotar."""
    assert partition_by_vehicle(pd.DataFrame()) == {}
    assert partition_by_vehicle(pd.DataFrame({"otra": [1, 2]})) == {}


def test_todas_las_filas_quedan_en_alguna_particion():
    df = _frame()
    index = partition_by_vehicle(df)
    assert sum(len(g) for g in index.values()) == len(df)
    assert set(index) == set(df["vehicle_id"])
