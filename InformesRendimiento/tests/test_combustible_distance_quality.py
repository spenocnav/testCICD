from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import ECM_COUNTER_DIAGNOSTICS
from transform.transform_combustible import (
    calculate_metrics,
    evaluate_distance_quality,
    process_ecm_counters,
    process_gps_trips,
)


def _status_day(day: str, odo_km: list[float], hours: float = 10.0) -> pd.DataFrame:
    start = pd.Timestamp(f"{day}T05:00:00Z")
    rows = [
        {
            "dateTime": start + pd.Timedelta(hours=index * 4),
            "diagnostic_id": ECM_COUNTER_DIAGNOSTICS["odometro"],
            "data": value * 1000.0,
        }
        for index, value in enumerate(odo_km)
    ]
    rows.extend(
        [
            {
                "dateTime": start,
                "diagnostic_id": ECM_COUNTER_DIAGNOSTICS["horas_motor"],
                "data": 0.0,
            },
            {
                "dateTime": start + pd.Timedelta(hours=12),
                "diagnostic_id": ECM_COUNTER_DIAGNOSTICS["horas_motor"],
                "data": hours * 3600.0,
            },
        ]
    )
    return pd.DataFrame(rows)


def _trips(day: str, kms: float, hours: float = 10.0) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "vehicle_id": "v1",
                "stop": pd.Timestamp(f"{day}T17:00:00Z"),
                "distance": kms,
                "idling_duration_s": 0.0,
                "driving_duration_s": hours * 3600.0,
            }
        ]
    )


def _quality(status: pd.DataFrame, trips: pd.DataFrame) -> pd.Series:
    ecm = process_ecm_counters(status)
    gps = process_gps_trips(trips)
    return evaluate_distance_quality(status, ecm, gps, is_f28=False).iloc[0]


def test_contadores_usan_primera_y_ultima_lectura_no_minimo_maximo():
    status = _status_day("2026-08-18", [100.0, 21_215.6, 120.0]).sample(
        frac=1, random_state=17
    )

    counters = process_ecm_counters(status).iloc[0]

    assert (counters["Odometro Fin"] - counters["Odometro Inicio"]) / 1000 == pytest.approx(20.0)


def test_salto_ecm_inequivoco_cae_automaticamente_a_gps():
    status = _status_day("2026-08-18", [100.0, 21_215.6, 120.0])

    quality = _quality(status, _trips("2026-08-18", 19.5))

    assert quality["Distance Quality Status"] == "critical"
    assert quality["Distance Quality Reason"] == "ecm_reset_or_negative_step"
    assert quality["Distance Source"] == "gps_auto"
    assert quality["Kms Effective"] == pytest.approx(19.5)
    assert quality["Revision"] is True or bool(quality["Revision"])


@pytest.mark.parametrize(
    ("odo_km", "hours", "gps_km", "expected_reason"),
    [
        ([100.0, 1_800.0, 1_810.0], 20.0, 1_000.0, "ecm_internal_jump"),
        ([100.0, 150.0, 160.0, 220.0], 10.0, 120.0, "ecm_boundary_interpolation"),
        ([100.0, 900.0, 1_700.0], 20.0, 1_000.0, "ecm_daily_limit"),
        ([100.0, 350.0, 600.0], 2.0, 480.0, "ecm_physical_speed"),
    ],
)
def test_invalidaciones_ecm_inequivocas_autocorrigen_con_gps(
    odo_km: list[float],
    hours: float,
    gps_km: float,
    expected_reason: str,
):
    status = _status_day("2026-08-18", odo_km, hours=hours)

    quality = _quality(status, _trips("2026-08-18", gps_km, hours=10.0))

    assert quality["Distance Quality Reason"] == expected_reason
    assert quality["Distance Source"] == "gps_auto"
    assert quality["Kms Effective"] == pytest.approx(gps_km)


def test_diferencia_ambigua_excluye_distancia_hasta_revision():
    status = _status_day("2026-08-18", [100.0, 150.0, 200.0])

    quality = _quality(status, _trips("2026-08-18", 50.0))

    assert quality["Distance Quality Status"] == "warning"
    assert quality["Distance Quality Reason"] == "ecm_gps_warning_mismatch"
    assert quality["Distance Source"] == "none"
    assert pd.isna(quality["Kms Effective"])


def test_menos_de_tres_lecturas_ecm_cae_a_gps_utilizable():
    status = _status_day("2026-08-18", [100.0, 150.0])

    quality = _quality(status, _trips("2026-08-18", 49.0))

    assert quality["Distance Quality Reason"] == "insufficient_ecm_readings"
    assert quality["Distance Source"] == "gps_auto"
    assert quality["Kms Effective"] == pytest.approx(49.0)


def test_gps_incompleto_no_es_utilizable():
    status = _status_day("2026-08-18", [100.0, 125.0, 150.0])
    trips = _trips("2026-08-18", 50.0)
    trips.loc[0, "distance"] = float("nan")

    quality = _quality(status, trips)

    assert not quality["GPS Quality Valid"]
    assert quality["Distance Quality Status"] == "ok"
    assert quality["Distance Source"] == "ecm"


@pytest.mark.parametrize(
    ("kms_ecm", "kms_gps", "expected_status", "expected_reason"),
    [
        (120.0, 90.0, "ok", "ok"),
        (121.0, 90.0, "warning", "ecm_gps_warning_mismatch"),
        (200.0, 100.0, "warning", "ecm_gps_warning_mismatch"),
        (201.0, 100.0, "critical", "ecm_gps_critical_mismatch"),
    ],
)
def test_umbrales_de_diferencia_son_estrictamente_mayores(
    kms_ecm: float,
    kms_gps: float,
    expected_status: str,
    expected_reason: str,
):
    status = _status_day(
        "2026-08-18", [100.0, 100.0 + kms_ecm / 2, 100.0 + kms_ecm]
    )

    quality = _quality(status, _trips("2026-08-18", kms_gps))

    assert quality["Distance Quality Status"] == expected_status
    assert quality["Distance Quality Reason"] == expected_reason


def test_velocidad_operativa_respeta_umbral_especial_f28():
    status = _status_day("2026-08-18", [100.0, 500.0, 900.0])
    ecm = process_ecm_counters(status)
    gps = process_gps_trips(_trips("2026-08-18", 800.0))

    normal = evaluate_distance_quality(status, ecm, gps, is_f28=False).iloc[0]
    f28 = evaluate_distance_quality(status, ecm, gps, is_f28=True).iloc[0]

    assert normal["Distance Quality Reason"] == "ecm_operational_speed"
    assert normal["Distance Source"] == "none"
    assert f28["Distance Quality Status"] == "ok"
    assert f28["Distance Source"] == "ecm"


def test_distancia_excluida_no_se_convierte_en_cero_en_ratios():
    metrics = calculate_metrics(
        pd.DataFrame(
            {
                "Fecha": [pd.Timestamp("2026-08-18").date()],
                "Odometro Inicio": [100_000.0],
                "Odometro Fin": [200_000.0],
                "Horas Inicio": [0.0],
                "Horas Fin": [10 * 3600.0],
                "Combustible Inicio": [0.0],
                "Combustible Fin": [37.854118],
                "Combustible Disponible": [True],
                "Kms Effective": [float("nan")],
                "Distance Source": ["none"],
            }
        ),
        "ABC123",
    ).iloc[0]

    assert pd.isna(metrics["Kms Effective"])
    assert pd.isna(metrics["km/gal Effective"])
    assert pd.isna(metrics["Velocidad Promedio Effective"])
    assert metrics["Comb"] == pytest.approx(10.0)


def test_calculo_normaliza_gps_quality_valid_como_booleano():
    metrics = calculate_metrics(
        pd.DataFrame(
            {
                "Fecha": [pd.Timestamp("2026-08-18").date()] * 2,
                "Odometro Inicio": [100_000.0, 200_000.0],
                "Odometro Fin": [110_000.0, 210_000.0],
                "Horas Inicio": [0.0, 0.0],
                "Horas Fin": [3600.0, 3600.0],
                "Combustible Inicio": [0.0, 0.0],
                "Combustible Fin": [3.7854118, 3.7854118],
                "Combustible Disponible": [True, True],
                "GPS Quality Valid": [True, None],
            }
        ),
        "ABC123",
    )

    assert metrics["GPS Quality Valid"].dtype == bool
    assert metrics["GPS Quality Valid"].tolist() == [True, False]


def test_baseline_historico_no_mira_el_dia_actual():
    statuses = []
    trips = []
    start = pd.Timestamp("2026-07-01")
    for offset in range(15):
        day = (start + pd.Timedelta(days=offset)).date().isoformat()
        kms = 1_300.0 if offset == 14 else 1_000.0
        statuses.append(_status_day(day, [10_000.0, 10_000.0 + kms / 2, 10_000.0 + kms], hours=20.0))
        trips.append(_trips(day, 1_000.0, hours=20.0))

    status = pd.concat(statuses, ignore_index=True)
    ecm = process_ecm_counters(status)
    gps = process_gps_trips(pd.concat(trips, ignore_index=True))
    quality = evaluate_distance_quality(status, ecm, gps, is_f28=False)

    assert (quality.iloc[:14]["Distance Quality Status"] == "ok").all()
    assert quality.iloc[14]["Distance Quality Reason"] == "historical_ecm_gps_deviation"
    assert pd.isna(quality.iloc[14]["Kms Effective"])
