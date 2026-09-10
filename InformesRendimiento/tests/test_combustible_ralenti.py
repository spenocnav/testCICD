from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import ECM_COUNTER_DIAGNOSTICS
from fuel import classify_fuel
from transform.transform_combustible import calculate_metrics, process_ecm_counters


def test_combustible_ralenti_convierte_delta_litros_a_galones_y_tasa():
    fecha = pd.Timestamp("2026-07-20T05:00:00Z")
    status = pd.DataFrame(
        [
            {
                "dateTime": fecha,
                "diagnostic_id": ECM_COUNTER_DIAGNOSTICS["combustible_ralenti"],
                "data": 100.0,
            },
            {
                "dateTime": fecha + pd.Timedelta(hours=12),
                "diagnostic_id": ECM_COUNTER_DIAGNOSTICS["combustible_ralenti"],
                "data": 107.5708236,
            },
            {
                "dateTime": fecha,
                "diagnostic_id": ECM_COUNTER_DIAGNOSTICS["horas_ralenti_ecm"],
                "data": 3600.0,
            },
            {
                "dateTime": fecha + pd.Timedelta(hours=12),
                "diagnostic_id": ECM_COUNTER_DIAGNOSTICS["horas_ralenti_ecm"],
                "data": 10800.0,
            },
        ]
    )

    # El pipeline rellena los diagnósticos no disponibles antes de calcular.
    counters = process_ecm_counters(status).fillna(0)
    metrics = calculate_metrics(counters, "ABC123").iloc[0]

    assert metrics["Comb Ralentí"] == pytest.approx(2.0)
    assert metrics["Ralentí ECM"] == pytest.approx(2.0)
    assert metrics["gal/hr Ralentí"] == pytest.approx(1.0)


def test_tasa_ralenti_sin_horas_no_produce_infinito():
    metrics = calculate_metrics(
        pd.DataFrame(
            {
                "Fecha": [pd.Timestamp("2026-07-20").date()],
                "Combustible Ralentí Inicio": [10.0],
                "Combustible Ralentí Fin": [20.0],
                "Combustible Ralentí Disponible": [True],
            }
        ),
        "ABC123",
    ).iloc[0]

    assert pd.isna(metrics["gal/hr Ralentí"])


def test_una_sola_lectura_del_contador_no_se_reporta_como_cero_real():
    status = pd.DataFrame(
        [
            {
                "dateTime": pd.Timestamp("2026-07-20T05:00:00Z"),
                "diagnostic_id": ECM_COUNTER_DIAGNOSTICS["combustible_ralenti"],
                "data": 100.0,
            }
        ]
    )

    counters = process_ecm_counters(status).fillna(0)
    metrics = calculate_metrics(counters, "ABC123").iloc[0]

    assert pd.isna(metrics["Comb Ralentí"])
    assert pd.isna(metrics["gal/hr Ralentí"])


def test_n15_es_gas_como_fallback_sin_confundir_gasolina():
    assert classify_fuel(None, "n15")[:3] == ("gas", "m3", "motor_n15_fallback")
    assert classify_fuel("GNV", "otro")[:2] == ("gas", "m3")
    assert classify_fuel("Gasolina", "otro")[:2] == ("liquid", "gal")
    assert classify_fuel("Diesel", "N15") == (
        "liquid",
        "gal",
        "vehicle_master",
        True,
    )


def test_n15_calcula_m3_desde_total_fuel_y_idle_device_sin_conversion():
    fecha = pd.Timestamp("2026-07-20T05:00:00Z")
    readings = [
        ("combustible_usado", 10.0, 14.0),
        ("combustible_ralenti_dispositivo", 5.0, 6.0),
        ("odometro", 100_000.0, 120_000.0),
        ("horas_motor", 3_600.0, 10_800.0),
        ("horas_ralenti_ecm", 3_600.0, 7_200.0),
    ]
    status = pd.DataFrame(
        [
            {
                "dateTime": fecha + offset,
                "diagnostic_id": ECM_COUNTER_DIAGNOSTICS[key],
                "data": value,
            }
            for key, start, end in readings
            for offset, value in ((pd.Timedelta(0), start), (pd.Timedelta(hours=12), end))
        ]
    )

    counters = process_ecm_counters(status)
    metrics = calculate_metrics(counters, "GAS001", fuel_kind="gas").iloc[0]

    assert metrics["Fuel Unit"] == "m3"
    assert metrics["Comb"] == pytest.approx(4.0)
    assert metrics["Comb Ralentí"] == pytest.approx(1.0)
    assert metrics["km/m3"] == pytest.approx(5.0)
    assert metrics["m3/hr"] == pytest.approx(2.0)
    assert metrics["m3/hr Ralentí"] == pytest.approx(1.0)
    assert pd.isna(metrics["km/gal"])
    assert pd.isna(metrics["gal/hr"])


def test_gas_con_una_sola_lectura_total_queda_sin_consumo_ni_rendimiento():
    status = pd.DataFrame(
        [
            {
                "dateTime": pd.Timestamp("2026-07-20T05:00:00Z"),
                "diagnostic_id": ECM_COUNTER_DIAGNOSTICS["combustible_usado"],
                "data": 10.0,
            }
        ]
    )

    metrics = calculate_metrics(
        process_ecm_counters(status),
        "GAS001",
        fuel_kind="gas",
    ).iloc[0]

    assert pd.isna(metrics["Comb"])
    assert pd.isna(metrics["km/m3"])
    assert pd.isna(metrics["m3/hr"])


# ---------------------------------------------------------------------------
# `% Ralentí` publicado: una sola definición, en cascada y acotada
# ---------------------------------------------------------------------------
def _metrics(**columns) -> pd.Series:
    base = {"Fecha": pd.Timestamp("2026-08-17").date()}
    base.update({name: [value] for name, value in columns.items()})
    return calculate_metrics(pd.DataFrame(base), "ABC123").iloc[0]


def test_ralenti_usa_el_contador_ecm_cuando_existe():
    """Numerador y denominador de la misma fuente: 2 h de 8 h de motor."""
    metrics = _metrics(
        **{
            "Horas Inicio": 0.0,
            "Horas Fin": 8 * 3600.0,
            "Ralentí ECM Inicio": 0.0,
            "Ralentí ECM Fin": 2 * 3600.0,
            "Hrs GPS": 10.0,
            "Ralentí": 1.0,
        }
    )
    assert metrics["% Ralentí"] == pytest.approx(0.25)
    assert metrics["Fuente Ralentí"] == "ecm"
    assert metrics["Horas Ralentí Base"] == pytest.approx(8.0)


def test_ralenti_cae_a_la_banda_sin_contador_ecm():
    """Flotas cuyo motor no publica el contador de ralentí del ECU."""
    metrics = _metrics(**{"Hrs GPS": 10.0, "Ralentí": 3.0})
    assert metrics["% Ralentí"] == pytest.approx(0.3)
    assert metrics["Fuente Ralentí"] == "banda"
    assert metrics["Horas Ralentí Base"] == pytest.approx(10.0)


def test_ralenti_cae_al_gps_sin_ecm_ni_banda():
    metrics = _metrics(**{"Hrs GPS": 8.0, "Tiempo en ralentí": 2.0})
    assert metrics["% Ralentí"] == pytest.approx(0.25)
    assert metrics["Fuente Ralentí"] == "gps"


def test_ralenti_sin_ninguna_fuente_es_cero():
    metrics = _metrics(**{"Hrs GPS": 8.0})
    assert metrics["% Ralentí"] == 0.0
    assert metrics["Fuente Ralentí"] == ""
    assert metrics["Horas Ralentí Base"] == 0.0


def test_ralenti_no_supera_el_cien_por_ciento():
    """Un vehículo que casi no reporta operación no puede marcar 1150 % de
    ralentí: era el efecto de dividir por un denominador casi cero."""
    metrics = _metrics(**{"Hrs GPS": 0.1, "Ralentí": 1.2})
    assert metrics["% Ralentí"] == pytest.approx(1.0)


def test_ralenti_ecm_y_gps_siguen_siendo_metricas_de_diagnostico():
    """Las columnas por fuente no cambian: sirven para explicar la diferencia."""
    metrics = _metrics(
        **{
            "Horas Inicio": 0.0,
            "Horas Fin": 10 * 3600.0,
            "Ralentí ECM Inicio": 0.0,
            "Ralentí ECM Fin": 3 * 3600.0,
            "Hrs GPS": 8.0,
            "Tiempo en ralentí": 2.0,
        }
    )
    assert metrics["% Ralentí ECM"] == pytest.approx(0.3)
    assert metrics["% Ralentí GPS"] == pytest.approx(0.25)
    assert metrics["% Ralentí"] == pytest.approx(0.3)
