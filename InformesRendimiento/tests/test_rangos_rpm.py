"""Cálculo de bandas por RANGOS DE RPM (flotas con `range_mode='rpm'`).

Se prueba la función pura `compute_daily_bands`: sin red, sin Geotab y sin
disco. Las reglas que se verifican son las del reporte de referencia: duración =
delta hasta la muestra anterior, huecos largos descartados, piso de RPM, motor
apagado fuera, velocidad 0 = Ralentí y descenso = carga casi nula en movimiento.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from extract.extract_rangos_rpm import (
    _colombia_midnight_floor,
    compute_daily_bands,
)

# Partición del eje usada por el reporte de referencia.
BANDS = [
    {"band": "Rango Bajo", "rpm_min": 600, "rpm_max": 1100},
    {"band": "Rango Economico", "rpm_min": 1100, "rpm_max": 1450},
    {"band": "Rango Balanceado", "rpm_min": 1450, "rpm_max": 1800},
    {"band": "Rango Potencia", "rpm_min": 1800, "rpm_max": 2300},
    {"band": "Rango Potencia Ineficiente", "rpm_min": 2300, "rpm_max": 2750},
    {"band": "Exceso RPM", "rpm_min": 2750, "rpm_max": None},
]

# Medianoche del 2026-08-10 en Colombia = 05:00 UTC.
DAY_START = datetime(2026, 8, 10, 5, 0, tzinfo=timezone.utc)


def _samples(values: list[tuple[int, float]], start: datetime = DAY_START) -> pd.DataFrame:
    """`[(segundos_desde_start, rpm), ...]` -> serie de RPM."""
    return pd.DataFrame(
        {
            "dateTime": [start + timedelta(seconds=offset) for offset, _ in values],
            "rpm": [rpm for _, rpm in values],
        }
    )


def _constant(column: str, value: float, start: datetime = DAY_START) -> pd.DataFrame:
    """Una lectura previa a la ventana: `merge_asof` backward la propaga."""
    return pd.DataFrame({"dateTime": [start - timedelta(hours=1)], column: [value]})


def _empty(column: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "dateTime": pd.Series(dtype="datetime64[ns, UTC]"),
            column: pd.Series(dtype="float64"),
        }
    )


def _run(
    rpm: pd.DataFrame,
    *,
    ignition: float | pd.DataFrame = 1,
    load: float | pd.DataFrame = 60,
    speed: float | pd.DataFrame = 45,
    window_start: datetime = DAY_START,
) -> dict[tuple[date, str], float]:
    frames = {}
    for name, value in (("ignition", ignition), ("load_factor", load), ("speed", speed)):
        frames[name] = value if isinstance(value, pd.DataFrame) else _constant(name, value)
    result = compute_daily_bands(
        rpm, frames["ignition"], frames["load_factor"], frames["speed"], BANDS, window_start
    )
    return {
        (row["fecha_colombia"], row["band"]): round(row["duration_hours"] * 3600, 6)
        for _, row in result.iterrows()
    }


def test_duration_is_the_gap_to_the_previous_sample() -> None:
    totals = _run(_samples([(0, 1200), (10, 1200), (20, 1200)]))
    # La primera muestra no tiene anterior dentro de la ventana: 2 deltas de 10 s.
    assert totals == {(date(2026, 8, 10), "Rango Economico"): 20.0}


def test_lookback_sample_gives_duration_to_the_first_in_window_sample() -> None:
    rpm = _samples([(-5, 1200), (0, 1200), (10, 1200)])
    totals = _run(rpm)
    # 5 s del arrastre + 10 s del intervalo interno.
    assert totals == {(date(2026, 8, 10), "Rango Economico"): 15.0}


def test_long_gaps_are_not_operation_time() -> None:
    """Un hueco de telemetría no es tiempo en banda: se descarta."""
    totals = _run(_samples([(0, 1200), (10, 1200), (600, 1200), (610, 1200)]))
    assert totals == {(date(2026, 8, 10), "Rango Economico"): 20.0}


def test_samples_below_the_engine_floor_are_ignored() -> None:
    totals = _run(_samples([(0, 500), (10, 550), (20, 590)]))
    assert totals == {}


def test_engine_off_is_ignored() -> None:
    totals = _run(_samples([(0, 1200), (10, 1200)]), ignition=0)
    assert totals == {}


def test_missing_ignition_reading_is_ignored() -> None:
    totals = _run(_samples([(0, 1200), (10, 1200)]), ignition=_empty("ignition"))
    assert totals == {}


def test_stopped_with_engine_on_is_idle_not_a_band() -> None:
    totals = _run(_samples([(0, 1200), (10, 1200)]), speed=0)
    assert totals == {(date(2026, 8, 10), "Ralentí"): 10.0}


def test_each_band_gets_its_own_range() -> None:
    # La primera muestra no tiene anterior: aporta el arrastre de la segunda.
    totals = _run(
        _samples([(0, 900), (10, 900), (20, 1200), (30, 1600), (40, 2000), (50, 2500), (60, 3000)])
    )
    assert totals == {
        (date(2026, 8, 10), "Rango Bajo"): 10.0,
        (date(2026, 8, 10), "Rango Economico"): 10.0,
        (date(2026, 8, 10), "Rango Balanceado"): 10.0,
        (date(2026, 8, 10), "Rango Potencia"): 10.0,
        (date(2026, 8, 10), "Rango Potencia Ineficiente"): 10.0,
        (date(2026, 8, 10), "Exceso RPM"): 10.0,
    }


def test_band_edges_are_inclusive_below_and_exclusive_above() -> None:
    totals = _run(_samples([(0, 1100), (10, 1100), (20, 1450)]))
    assert totals == {
        (date(2026, 8, 10), "Rango Economico"): 10.0,
        (date(2026, 8, 10), "Rango Balanceado"): 10.0,
    }


def test_descenso_is_a_subset_of_its_band() -> None:
    """Carga casi nula en movimiento: el tiempo cuenta en la banda Y en su
    variante de descenso, igual que en el modo por reglas."""
    totals = _run(_samples([(0, 1200), (10, 1200)]), load=2, speed=50)
    assert totals == {
        (date(2026, 8, 10), "Rango Economico"): 10.0,
        (date(2026, 8, 10), "Rango Economico Descenso"): 10.0,
    }


def test_low_load_while_stopped_is_not_descenso() -> None:
    totals = _run(_samples([(0, 1200), (10, 1200)]), load=2, speed=0)
    assert totals == {(date(2026, 8, 10), "Ralentí"): 10.0}


def test_high_load_in_movement_is_not_descenso() -> None:
    totals = _run(_samples([(0, 1200), (10, 1200)]), load=65, speed=50)
    assert totals == {(date(2026, 8, 10), "Rango Economico"): 10.0}


def test_missing_load_reading_is_not_descenso() -> None:
    totals = _run(_samples([(0, 1200), (10, 1200)]), load=_empty("load_factor"))
    assert totals == {(date(2026, 8, 10), "Rango Economico"): 10.0}


def test_days_are_split_at_colombia_midnight() -> None:
    """La medianoche que corta el día es la de Colombia (UTC-5), no la UTC.

    El tiempo de una muestra se le acredita al día de ESA muestra, igual que en
    el reporte de referencia: el intervalo que cruza la medianoche cae del lado
    del día que empieza.
    """
    midnight = DAY_START + timedelta(days=1)
    rpm = pd.DataFrame(
        {
            "dateTime": [
                midnight - timedelta(seconds=20),
                midnight - timedelta(seconds=10),
                midnight,
                midnight + timedelta(seconds=10),
            ],
            "rpm": [1200, 1200, 1200, 1200],
        }
    )
    totals = _run(rpm)
    assert totals == {
        (date(2026, 8, 10), "Rango Economico"): 10.0,
        (date(2026, 8, 11), "Rango Economico"): 20.0,
    }


def test_no_rpm_samples_produces_nothing() -> None:
    assert _run(_empty("rpm")) == {}


def test_without_configured_bands_nothing_is_computed() -> None:
    result = compute_daily_bands(
        _samples([(0, 1200), (10, 1200)]),
        _constant("ignition", 1),
        _constant("load_factor", 60),
        _constant("speed", 45),
        [],
        DAY_START,
    )
    assert result.empty


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (DAY_START, DAY_START),
        (DAY_START + timedelta(hours=14), DAY_START),
        (DAY_START + timedelta(hours=23, minutes=59), DAY_START),
        (DAY_START + timedelta(days=1), DAY_START + timedelta(days=1)),
    ],
)
def test_window_start_snaps_down_to_colombia_midnight(
    moment: datetime, expected: datetime
) -> None:
    """Un día solo se publica completo: la ventana se extiende hacia atrás
    hasta la medianoche local en vez de guardar medio día."""
    assert _colombia_midnight_floor(moment) == expected


# ---------------------------------------------------------------------------
# Integración con el transform: el pivot alimenta el MISMO cálculo de %
# ---------------------------------------------------------------------------
def test_rpm_bands_feed_the_same_percentages_as_rules() -> None:
    """El modo por RPM solo cambia el ORIGEN del tiempo por banda.

    Una vez pivotado, `calculate_metrics` reparte porcentajes, descenso y
    "sin descenso" exactamente igual que con las bandas por regla Geotab.
    """
    from transform.transform_combustible import calculate_metrics, process_rpm_bands

    daily = pd.DataFrame(
        [
            {"fecha_colombia": date(2026, 8, 10), "band": "Rango Economico", "duration_hours": 6.0},
            {"fecha_colombia": date(2026, 8, 10), "band": "Rango Potencia", "duration_hours": 2.0},
            {
                "fecha_colombia": date(2026, 8, 10),
                "band": "Rango Economico Descenso",
                "duration_hours": 2.0,
            },
            {"fecha_colombia": date(2026, 8, 10), "band": "Ralentí", "duration_hours": 1.0},
        ]
    )

    pivot = process_rpm_bands(daily)
    assert list(pivot["Fecha"]) == [date(2026, 8, 10)]

    metrics = calculate_metrics(pivot.copy(), "ABC123")
    row = metrics.iloc[0]

    assert row["Tiempo Total en Rango"] == pytest.approx(8.0)
    assert row["% Rango Economico"] == pytest.approx(6.0 / 8.0)
    assert row["% Rango Potencia"] == pytest.approx(2.0 / 8.0)
    # El descenso es un subconjunto de su banda, no una banda extra.
    assert row["Tiempo Total en Rango de Descenso"] == pytest.approx(2.0)
    assert row["% Rango Economico Descenso"] == pytest.approx(1.0)
    assert row["Tiempo Total en Rango Sin Descenso"] == pytest.approx(6.0)
    assert row["% Rango Economico Sin Descenso"] == pytest.approx(4.0 / 6.0)
    assert row["% Rango Potencia Sin Descenso"] == pytest.approx(2.0 / 6.0)
    # Ralentí es métrica de comparación: no entra en la distribución de bandas.
    assert row["Ralentí"] == pytest.approx(1.0)


def test_process_rpm_bands_without_data_is_empty() -> None:
    from transform.transform_combustible import process_rpm_bands

    assert list(process_rpm_bands(pd.DataFrame()).columns) == ["Fecha"]


# ---------------------------------------------------------------------------
# extract_chunk: forma de las peticiones y de las filas guardadas
# ---------------------------------------------------------------------------
class _FakeApi:
    """Captura las peticiones y responde en el mismo orden."""

    def __init__(self, responses: list[list]) -> None:
        self.requests: list = []
        self._responses = responses

    def multi_call(self, requests_list: list) -> list:
        self.requests = requests_list
        return self._responses


def _status_rows(values: list[tuple[int, float]]) -> list[dict]:
    return [
        {"dateTime": DAY_START + timedelta(seconds=offset), "data": value}
        for offset, value in values
    ]


def test_extract_chunk_builds_four_requests_per_device_and_aggregates(monkeypatch) -> None:
    from extract import extract_rangos_rpm as module

    monkeypatch.setitem(
        module.VEHICLE_BY_DB_DEVICE,
        ("base_x", "dev1"),
        {"database_name": "base_x", "device_id": "dev1", "placa": "ABC123", "motor_type": "X11"},
    )
    monkeypatch.setattr(module, "rpm_bands_for", lambda motor: BANDS if motor == "X11" else [])

    api = _FakeApi([
        _status_rows([(0, 1200), (10, 1200), (20, 1200)]),   # rpm
        _status_rows([(-60, 1)]),                            # ignición
        _status_rows([(-60, 55)]),                           # factor de carga
        [{"dateTime": DAY_START - timedelta(seconds=60), "speed": 40}],  # LogRecord
    ])
    monkeypatch.setattr(module, "safe_multi_call", lambda _api, requests: _api.multi_call(requests))

    frame = module.extract_chunk(
        api, ["dev1"], "2026-08-10T05:00:00.000Z", "2026-08-11T04:59:59.999Z", "base_x"
    )

    assert [request[1]["typeName"] for request in api.requests] == [
        "StatusData", "StatusData", "StatusData", "LogRecord",
    ]
    assert [request[1]["search"].get("diagnosticSearch", {}).get("id") for request in api.requests[:3]] == [
        module.RPM_DIAG_ID, module.IGNITION_DIAG_ID, module.LOAD_DIAG_ID,
    ]

    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["database_name"] == "base_x"
    assert row["device_id"] == "dev1"
    assert row["band"] == "Rango Economico"
    assert row["duration_hours"] * 3600 == pytest.approx(20.0)
    assert row["fecha_colombia"] == date(2026, 8, 10)
    # row_id estable: reprocesar el día produce exactamente la misma clave.
    assert row["row_id"] == frame.iloc[0]["row_id"]


def test_extract_chunk_skips_devices_without_configured_bands(monkeypatch) -> None:
    from extract import extract_rangos_rpm as module

    monkeypatch.setitem(
        module.VEHICLE_BY_DB_DEVICE,
        ("base_x", "dev_sin_rangos"),
        {"database_name": "base_x", "device_id": "dev_sin_rangos", "motor_type": "SIN_RANGOS"},
    )
    monkeypatch.setattr(module, "rpm_bands_for", lambda motor: [])

    api = _FakeApi([
        _status_rows([(0, 1200), (10, 1200)]),
        _status_rows([(-60, 1)]),
        _status_rows([(-60, 55)]),
        [{"dateTime": DAY_START - timedelta(seconds=60), "speed": 40}],
    ])
    monkeypatch.setattr(module, "safe_multi_call", lambda _api, requests: _api.multi_call(requests))

    frame = module.extract_chunk(
        api, ["dev_sin_rangos"], "2026-08-10T05:00:00.000Z", "2026-08-11T04:59:59.999Z", "base_x"
    )
    assert frame.empty
