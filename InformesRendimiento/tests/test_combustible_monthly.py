"""El grano mensual debe ser la suma del diario, no un recálculo del contador.

`compute_monthly_ecm` tomaba `max(contador) - min(contador)` del mes sobre el
diagnóstico crudo de combustible, sin pasar por `fix_fuel_data`. Cada salto del
contador entre lecturas entraba íntegro al mes, así que el mensual reportaba más
galones que la suma de los días y el km/gal del gráfico salía por debajo del KPI
y de la tabla de detalle, que sí leen el diario.
"""

from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from transform.transform_combustible import aggregate_monthly_from_daily


def _daily() -> pd.DataFrame:
    """Dos meses de un vehículo, con días de operación muy dispar."""
    return pd.DataFrame(
        {
            "Fecha": [
                pd.Timestamp("2026-06-01").date(),
                pd.Timestamp("2026-06-15").date(),
                pd.Timestamp("2026-06-30").date(),
                pd.Timestamp("2026-07-02").date(),
            ],
            "Kms ECM": [300.0, 100.0, 20.0, 500.0],
            "Hrs ECM": [10.0, 5.0, 2.0, 12.0],
            "Comb": [50.0, 20.0, 5.0, 80.0],
            "Comb Ralentí": [3.0, 1.5, 0.5, 4.0],
            "Ralentí ECM": [2.0, 1.0, 0.5, 2.0],
        }
    )


def test_totales_son_la_suma_de_los_dias():
    monthly = aggregate_monthly_from_daily(_daily(), "ABC123")

    junio = monthly[monthly["Anio-Mes"] == "2026-06"].iloc[0]
    assert junio["Kms ECM"] == 420.0
    assert junio["Hrs ECM"] == 17.0
    assert junio["Comb"] == 75.0
    assert junio["Comb Ralentí"] == 5.0
    assert junio["Ralentí ECM"] == 3.5

    julio = monthly[monthly["Anio-Mes"] == "2026-07"].iloc[0]
    assert julio["Kms ECM"] == 500.0
    assert julio["Comb"] == 80.0


def test_razones_son_cociente_de_sumas_no_promedio_de_promedios():
    monthly = aggregate_monthly_from_daily(_daily(), "ABC123")
    junio = monthly[monthly["Anio-Mes"] == "2026-06"].iloc[0]

    assert junio["km/gal"] == 420.0 / 75.0
    assert junio["gal/hr"] == 75.0 / 17.0
    assert junio["gal/hr Ralentí"] == 5.0 / 3.5
    assert junio["Velocidad Promedio"] == 420.0 / 17.0

    # El promedio de los km/gal diarios (6.0, 5.0, 4.0) daría 5.0; el real es 5.6.
    # Un día de 20 km no puede pesar lo mismo que uno de 300.
    promedio_de_promedios = sum([300 / 50, 100 / 20, 20 / 5]) / 3
    assert junio["km/gal"] != promedio_de_promedios


def test_mes_sin_horas_no_revienta_ni_produce_infinito():
    daily = pd.DataFrame(
        {
            "Fecha": [pd.Timestamp("2026-06-01").date()],
            "Kms ECM": [10.0],
            "Hrs ECM": [0.0],
            "Comb": [0.0],
            "Comb Ralentí": [0.0],
            "Ralentí ECM": [0.0],
        }
    )
    junio = aggregate_monthly_from_daily(daily, "ABC123").iloc[0]

    assert junio["gal/hr"] == 0
    assert pd.isna(junio["gal/hr Ralentí"])
    assert junio["km/gal"] == 0
    assert junio["Velocidad Promedio"] == 0


def test_tasa_mensual_excluye_horas_sin_diagnostico_de_combustible_ralenti():
    daily = pd.DataFrame(
        {
            "Fecha": [
                pd.Timestamp("2026-06-01").date(),
                pd.Timestamp("2026-06-02").date(),
            ],
            "Kms ECM": [10.0, 10.0],
            "Hrs ECM": [2.0, 10.0],
            "Comb": [2.0, 10.0],
            "Comb Ralentí": [1.0, None],
            "Ralentí ECM": [1.0, 9.0],
        }
    )

    junio = aggregate_monthly_from_daily(daily, "ABC123").iloc[0]

    assert junio["Comb Ralentí"] == 1.0
    assert junio["Ralentí ECM"] == 1.0
    assert junio["gal/hr Ralentí"] == 1.0


def test_diario_vacio_devuelve_frame_vacio():
    assert aggregate_monthly_from_daily(pd.DataFrame(), "ABC123").empty


def test_gas_mensual_suma_m3_y_no_puebla_metricas_de_galones():
    daily = pd.DataFrame(
        {
            "Fecha": [
                pd.Timestamp("2026-07-01").date(),
                pd.Timestamp("2026-07-02").date(),
            ],
            "Kms ECM": [40.0, 60.0],
            "Hrs ECM": [2.0, 3.0],
            "Comb": [8.0, 12.0],
            "Comb Ralentí": [1.0, 2.0],
            "Ralentí ECM": [1.0, 1.0],
            "Fuel Kind": ["gas", "gas"],
            "Fuel Unit": ["m3", "m3"],
        }
    )

    julio = aggregate_monthly_from_daily(daily, "GAS001").iloc[0]

    assert julio["Comb"] == 20.0
    assert julio["Fuel Unit"] == "m3"
    assert julio["km/m3"] == 5.0
    assert julio["m3/hr"] == 4.0
    assert julio["m3/hr Ralentí"] == 1.5
    assert pd.isna(julio["km/gal"])
    assert pd.isna(julio["gal/hr"])


def test_gas_mensual_excluye_kms_y_horas_sin_medicion_de_combustible():
    daily = pd.DataFrame(
        {
            "Fecha": [
                pd.Timestamp("2026-07-01").date(),
                pd.Timestamp("2026-07-02").date(),
            ],
            "Kms ECM": [40.0, 600.0],
            "Hrs ECM": [2.0, 30.0],
            "Comb": [8.0, None],
            "Comb Ralentí": [1.0, None],
            "Ralentí ECM": [1.0, 10.0],
            "Fuel Kind": ["gas", "gas"],
            "Fuel Unit": ["m3", "m3"],
        }
    )

    julio = aggregate_monthly_from_daily(daily, "GAS001").iloc[0]

    # Los totales operativos conservan todo el mes, pero las razones usan solo
    # el día donde TotalFuelUsed permitió calcular un delta.
    assert julio["Kms ECM"] == 640.0
    assert julio["Hrs ECM"] == 32.0
    assert julio["Comb"] == 8.0
    assert julio["km/m3"] == 5.0
    assert julio["m3/hr"] == 4.0
