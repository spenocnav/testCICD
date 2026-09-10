"""Funciones puras del extractor de episodios de ralentí.

La regla de banda "Ralentí" de Geotab dispara un evento cada vez que las RPM
entran en la banda: un vehículo detenido con el motor encendido produce cientos
de eventos de segundos por día. El módulo consolida eventos consecutivos en
episodios; estas pruebas fijan esa consolidación, la identidad estable del
episodio, la estadística de RPM, la elección de posición y los dtypes del frame
(un lote sin RPM no puede hacer nacer la columna como TEXT).
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from extract.extract_ralenti import (  # noqa: E402
    COLUMNS,
    Episode,
    episode_row,
    merge_episodes,
    pick_position,
    resolve_rule,
    rows_frame,
    rpm_stats,
)

T0 = datetime(2026, 8, 5, 14, 0, tzinfo=timezone.utc)


def _ev(eid: str, start_s: float, dur_s: float, rule: str = "r1") -> dict:
    start = T0 + timedelta(seconds=start_s)
    return {
        "id": eid,
        "rule": {"id": rule},
        "activeFrom": start,
        "activeTo": start + timedelta(seconds=dur_s),
    }


def test_merge_consecutive_events_within_gap():
    events = [_ev("a", 0, 10), _ev("b", 30, 5), _ev("c", 50, 20)]  # gaps 20 s y 15 s
    episodes = merge_episodes(events, gap_seconds=60, min_seconds=0)
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep.first_event_id == "a"
    assert ep.eventos_fuente == 3
    assert ep.inicio == T0
    assert ep.fin == T0 + timedelta(seconds=70)
    assert ep.duracion_segundos == 70


def test_gap_larger_than_threshold_splits_episodes():
    events = [_ev("a", 0, 10), _ev("b", 200, 5)]  # gap 190 s
    episodes = merge_episodes(events, gap_seconds=60, min_seconds=0)
    assert [e.first_event_id for e in episodes] == ["a", "b"]


def test_merge_is_order_independent_and_uses_max_end():
    # Un evento largo seguido de uno corto contenido en él: el fin es el máximo.
    events = [_ev("b", 20, 5), _ev("a", 0, 100)]
    episodes = merge_episodes(events, gap_seconds=60, min_seconds=0)
    assert len(episodes) == 1
    assert episodes[0].first_event_id == "a"
    assert episodes[0].fin == T0 + timedelta(seconds=100)


def test_min_seconds_drops_noise():
    events = [_ev("a", 0, 3), _ev("b", 500, 120)]
    episodes = merge_episodes(events, gap_seconds=60, min_seconds=5)
    assert [e.first_event_id for e in episodes] == ["b"]


def test_rpm_stats_ignores_invalid_and_zero():
    avg, mx, mn, n = rpm_stats([700, "800", None, 0, "x", 900.0])
    assert n == 3
    assert mx == 900.0 and mn == 700.0
    assert abs(avg - 800.0) < 1e-9
    assert rpm_stats([]) == (None, None, None, 0)


def test_pick_position_prefers_record_inside_episode():
    inicio, fin = T0, T0 + timedelta(seconds=60)
    records = [
        # fuera (antes), más cerca del inicio que el de adentro
        {"dateTime": T0 - timedelta(seconds=5), "latitude": 1.0, "longitude": 1.0, "speed": 30},
        {"dateTime": T0 + timedelta(seconds=20), "latitude": 2.0, "longitude": 2.0, "speed": 0},
        {"dateTime": T0 + timedelta(seconds=40), "latitude": 2.1, "longitude": 2.1, "speed": 4},
    ]
    lat, lon, vmax = pick_position(records, inicio, fin)
    assert (lat, lon) == (2.0, 2.0)
    # La velocidad máxima solo mira DENTRO del episodio.
    assert vmax == 4


def test_pick_position_falls_back_to_margin_and_none():
    inicio, fin = T0, T0 + timedelta(seconds=10)
    records = [{"dateTime": T0 + timedelta(seconds=70), "latitude": 5.0, "longitude": 6.0}]
    assert pick_position(records, inicio, fin) == (5.0, 6.0, None)
    assert pick_position([], inicio, fin) == (None, None, None)


def test_resolve_rule_falls_back_to_geotab_idling():
    # Sin data maestra (tests) rules_for cae al catálogo hardcoded por motor.
    rule_id, source = resolve_rule("navitrans", "F4.5")
    assert source == "banda" and rule_id == "almm_jw2wNkGw7zoNkunfkg"
    rule_id, source = resolve_rule("navitrans", "MOTOR-INEXISTENTE")
    assert (rule_id, source) == ("RuleIdlingId", "geotab_idling")


def test_episode_row_is_local_day_and_stable_key():
    # 03:30 UTC del 6 de agosto es 22:30 del 5 de agosto en Bogotá.
    inicio = datetime(2026, 8, 6, 3, 30, tzinfo=timezone.utc)
    ep = Episode("evt-1", "r1", inicio, inicio + timedelta(minutes=7), eventos_fuente=4)
    ep.rpm = [650, 700]
    ep.latitud, ep.longitud, ep.velocidad_maxima_kmh = 3.69, -76.43, 0.0
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    row = episode_row(ep, database_name="navitrans", device_id="b349", rule_source="banda", extracted_at=now)
    assert row["fecha"] == date(2026, 8, 5)
    assert row["date_key"] == 20260805
    assert row["hora_local"] == 22
    assert row["duracion_segundos"] == 420
    assert row["eventos_fuente"] == 4
    assert row["rpm_promedio"] == 675 and row["rpm_muestras"] == 2
    again = episode_row(ep, database_name="navitrans", device_id="b349", rule_source="banda", extracted_at=now)
    assert row["event_sk"] == again["event_sk"]
    other = episode_row(ep, database_name="navitrans", device_id="b356", rule_source="banda", extracted_at=now)
    assert row["event_sk"] != other["event_sk"]
    assert set(row) == set(COLUMNS)


def test_rows_frame_keeps_numeric_dtypes_without_data():
    empty = rows_frame([])
    assert str(empty["rpm_promedio"].dtype) == "float64"
    assert str(empty["eventos_fuente"].dtype) == "Int64"
    assert str(empty["inicio"].dtype) == "datetime64[ns, UTC]"
    row = episode_row(
        Episode("e", "r", T0, T0 + timedelta(seconds=30)),
        database_name="navitrans", device_id="b349", rule_source="banda",
        extracted_at=T0,
    )
    df = rows_frame([row])
    assert df["rpm_promedio"].isna().all()
    assert str(df["rpm_promedio"].dtype) == "float64"
    assert list(df.columns) == list(COLUMNS)
