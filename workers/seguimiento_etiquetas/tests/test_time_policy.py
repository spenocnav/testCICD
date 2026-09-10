from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from build_label_history import build_events, build_transitions  # noqa: E402
from label_config import parse_explicit_datetime  # noqa: E402


def tracking_frame(comment: str, observed_from: str | None, observed_at: str | None) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "work_order_number": 900001,
                "tracking_id": 700001,
                "tracking_comment": comment,
                "tracking_date": "2026-08-11T05:00:00Z",
                "tracking_observed_from": observed_from,
                "tracking_observed_at": observed_at,
                "vehicle_code": "DEM001",
                "client": "Cliente demo",
                "cd": "CD demo",
                "work_order_status": "opened",
                "work_order_type": "corrective",
                "created_by_id": None,
                "created_by_name": "Usuario demo",
            }
        ]
    )


def test_date_without_time_is_not_an_explicit_precise_datetime() -> None:
    parsed, display = parse_explicit_datetime("Etiqueta: 9 Fecha: 11/08/2026")
    assert parsed is None
    # The date-only display may be preserved for traceability, but is not a
    # precise datetime and must never be converted to midnight.
    assert display == "11/08/2026"


def test_live_event_uses_observation_window_end() -> None:
    observed_from = "2026-08-11T14:00:00Z"
    observed_to = "2026-08-11T14:05:00Z"
    events, rejected, stats = build_events(
        tracking_frame("Etiqueta: 9 Fecha: 11/08/2026", observed_from, observed_to)
    )

    assert rejected.empty
    assert stats["recognized"] == 1
    event = events.iloc[0]
    assert event["event_at_source"] == "observation_window_end"
    assert pd.Timestamp(event["event_at"]) == pd.Timestamp(observed_to)
    assert pd.Timestamp(event["observation_to"]) == pd.Timestamp(observed_to)
    assert event["observation_window_minutes"] == 5.0


def test_comment_with_explicit_time_has_priority_over_live_window() -> None:
    events, rejected, _ = build_events(
        tracking_frame(
            "Etiqueta: 10 Fecha: 11/08/2026 14:30",
            "2026-08-11T20:00:00Z",
            "2026-08-11T20:05:00Z",
        )
    )

    assert rejected.empty
    event = events.iloc[0]
    assert event["event_at_source"] == "comment_explicit"
    assert pd.Timestamp(event["event_at"]) == pd.Timestamp("2026-08-11T19:30:00Z")


def test_baseline_without_explicit_time_keeps_tracking_date() -> None:
    events, rejected, _ = build_events(
        tracking_frame("Etiqueta: 9 Fecha: 11/08/2026", None, None)
    )

    assert rejected.empty
    event = events.iloc[0]
    assert event["event_at_source"] == "tracking_date_baseline"
    assert event["observation_from"] is None
    assert event["observation_to"] is None


def test_first_poll_baseline_does_not_become_live_just_because_observed_at_exists() -> None:
    frame = tracking_frame(
        "Etiqueta: 9 Fecha: 11/08/2026",
        None,
        "2026-08-11T14:05:00Z",
    )
    frame["tracking_observation_kind"] = "baseline"

    events, rejected, _ = build_events(frame)

    assert rejected.empty
    event = events.iloc[0]
    assert event["event_at_source"] == "tracking_date_baseline"
    assert pd.Timestamp(event["event_at"]) == pd.Timestamp("2026-08-11T05:00:00Z")


def test_reaffirming_same_label_does_not_reset_residence_clock() -> None:
    events = pd.DataFrame(
        [
            {
                "work_order_number": 900001,
                "tracking_id": 1,
                "label_id": 3,
                "label_name": "En diagnóstico",
                "event_at": "2026-08-11T10:00:00Z",
                "event_time_quality": "live_window",
            },
            {
                "work_order_number": 900001,
                "tracking_id": 2,
                "label_id": 3,
                "label_name": "En diagnóstico",
                "event_at": "2026-08-11T11:00:00Z",
                "event_time_quality": "live_window",
            },
            {
                "work_order_number": 900001,
                "tracking_id": 3,
                "label_id": 9,
                "label_name": "En intervención",
                "event_at": "2026-08-11T13:00:00Z",
                "event_time_quality": "live_window",
            },
        ]
    )

    transitions, durations = build_transitions(events)

    assert len(transitions) == 2
    change = transitions.iloc[1]
    assert change["previous_label_id"] == 3
    assert change["label_id"] == 9
    assert change["time_to_label_minutes"] == 180.0
    first_segment = durations.iloc[0]
    assert first_segment["duration_hours"] == 3.0
