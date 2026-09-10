from __future__ import annotations

from datetime import UTC, datetime

from app.services.cloudfleet_tracking_service import (
    normalize_label,
    normalize_tracking_row,
    parse_label_comment,
)


def test_normalize_label_accepts_ids_accents_and_known_alias() -> None:
    assert normalize_label("3") == (3, "En diagnóstico")
    assert normalize_label("En diagnostico") == (3, "En diagnóstico")
    assert normalize_label("En intevencion") == (9, "En intervención")
    assert normalize_label("estado no controlado") is None


def test_parse_label_comment_extracts_explicit_bogota_time() -> None:
    parsed = parse_label_comment("Etiqueta: En diagnostico Fecha: 05/08/2026 14:30")

    assert parsed is not None
    assert parsed[0:2] == (3, "En diagnóstico")
    assert parsed[2] == datetime(2026, 8, 5, 19, 30, tzinfo=UTC)


def test_parse_label_comment_matches_worker_date_equals_format() -> None:
    parsed = parse_label_comment("En diagnostico date=05/08/2026 14:30")

    assert parsed is not None
    assert parsed[0:2] == (3, "En diagnóstico")
    assert parsed[2] == datetime(2026, 8, 5, 19, 30, tzinfo=UTC)


def test_normalize_tracking_row_uses_observation_window_for_live_label() -> None:
    row = normalize_tracking_row(
        {
            "work_order_number": 10482,
            "tracking_id": 99,
            "tracking_comment": "9",
            "tracking_date": "2026-08-17T14:00:00Z",
            "tracking_observation_kind": "live",
            "tracking_observed_from": "2026-08-17T14:00:00Z",
            "tracking_observed_at": "2026-08-17T14:04:00Z",
            "extracted_at": "2026-08-17T14:04:00Z",
        }
    )

    assert row["tracking_key"] == "id:99"
    assert row["label_id"] == 9
    assert row["event_at"] == datetime(2026, 8, 17, 14, 4, tzinfo=UTC)
    assert row["event_at_source"] == "observation_window_end"
    assert row["event_time_quality"] == "live_window"
    assert row["observation_window_minutes"] == 4.0


def test_normalize_tracking_row_downgrades_stale_live_window_to_tracking_date() -> None:
    row = normalize_tracking_row(
        {
            "work_order_number": 10482,
            "tracking_id": 100,
            "tracking_comment": "9",
            "tracking_date": "2026-08-16T05:00:00Z",
            "tracking_observation_kind": "live",
            "tracking_observed_from": "2026-08-16T10:00:00Z",
            "tracking_observed_at": "2026-08-17T14:00:00Z",
            "extracted_at": "2026-08-17T14:00:00Z",
        }
    )

    assert row["event_at"] == datetime(2026, 8, 16, 5, tzinfo=UTC)
    assert row["event_at_source"] == "tracking_date_baseline"
    assert row["event_time_quality"] == "historical_date"


def test_normalize_tracking_row_keeps_unrecognized_observation_without_event() -> None:
    row = normalize_tracking_row(
        {
            "work_order_number": 10482,
            "tracking_date": "2026-08-17T14:00:00Z",
            "tracking_comment": "Aprobado por operaciones",
            "tracking_observation_kind": "historical_bootstrap",
            "extracted_at": "2026-08-17T14:00:00Z",
        }
    )

    assert row["tracking_key"].startswith("sha256:")
    assert row["label_id"] is None
    assert row["event_at"] is None
