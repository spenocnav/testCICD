from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.services.cloudfleet_tracking_ingest_service import read_worker_health
from app.services.tracking_health_service import build_status

NOW = datetime(2026, 8, 18, 16, 0, tzinfo=UTC)


def _row(
    *,
    heartbeat_age: int = 20,
    ingest_ok: bool = True,
    worker: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "watermark": NOW - timedelta(seconds=heartbeat_age),
        "detail": {
            "ingest": {"ok": ingest_ok, "counters": {}, "error": error},
            "worker": worker,
        },
    }


def _worker(*, age: int = 30, reasons: list[str] | None = None) -> dict[str, Any]:
    return {
        "status": "degraded" if reasons else "ready",
        "ready": not reasons,
        "generatedAt": (NOW - timedelta(seconds=age)).isoformat(),
        "reasons": reasons or [],
        "metrics": {"activeOrders": 158, "activeTrackingBacklog": 98},
    }


def _build(row: dict[str, Any] | None, **kwargs: Any) -> dict[str, Any]:
    defaults = {"events_total": 265, "events_with_label": 4, "last_event_at": None, "now": NOW}
    return build_status(row, **{**defaults, **kwargs})


def test_fresh_heartbeat_and_healthy_worker_is_green() -> None:
    result = _build(_row(worker=_worker()))

    assert result["status"] == "ok"
    assert result["active_orders"] == 158


def test_accepted_bootstrap_reason_does_not_degrade_the_light() -> None:
    """Arrancar sin bootstrap fue una decisión, no una falla del pipeline."""
    result = _build(_row(worker=_worker(reasons=["historical_bootstrap_required"])))

    assert result["status"] == "ok"
    # Pero la condición sigue visible.
    assert any("bootstrap" in note.lower() for note in result["notes"])


def test_any_other_worker_reason_degrades() -> None:
    result = _build(_row(worker=_worker(reasons=["api_errors"])))

    assert result["status"] == "degraded"
    assert "CloudFleet devolvió errores en el último ciclo" in result["notes"]


def test_stale_heartbeat_is_down_even_when_the_worker_looks_fine() -> None:
    """El caso que motivó el heartbeat: si solo miráramos el watermark de datos,
    un ingestor muerto en un taller sin novedades se vería igual que uno sano."""
    result = _build(_row(heartbeat_age=600, worker=_worker()))

    assert result["status"] == "down"


def test_failed_ingest_reports_down_with_its_error() -> None:
    result = _build(_row(ingest_ok=False, error="RuntimeError: ledger corrupto", worker=_worker()))

    assert result["status"] == "down"
    assert "RuntimeError: ledger corrupto" in result["notes"]


def test_missing_worker_health_degrades_but_is_not_down() -> None:
    result = _build(_row(worker=None))

    assert result["status"] == "degraded"


def test_stale_worker_health_degrades() -> None:
    result = _build(_row(worker=_worker(age=5000)))

    assert result["status"] == "degraded"


def test_no_heartbeat_at_all_is_unknown_not_green() -> None:
    result = _build(None)

    assert result["status"] == "unknown"


def test_green_pipeline_without_labels_warns_about_the_human_process() -> None:
    result = _build(_row(worker=_worker()), events_with_label=0)

    assert result["status"] == "ok"
    assert any("vocabulario" in note for note in result["notes"])


def test_worker_health_reader_keeps_only_the_allowlist(tmp_path: Path) -> None:
    (tmp_path / "worker_health.json").write_text(
        json.dumps(
            {
                "status": "degraded",
                "ready": False,
                "generatedAt": "2026-08-18T15:04:46+00:00",
                "reasons": ["api_errors"],
                "metrics": {"activeOrders": 158, "cursorPath": "/runtime/state.json"},
                "runtimeDir": "/runtime",
            }
        ),
        encoding="utf-8",
    )

    health = read_worker_health(tmp_path)

    assert health is not None
    assert health["status"] == "degraded"
    assert health["metrics"] == {"activeOrders": 158}
    # Rutas y campos internos no cruzan la frontera del API.
    assert "runtimeDir" not in health
    assert "cursorPath" not in health["metrics"]


def test_worker_health_reader_tolerates_a_missing_file(tmp_path: Path) -> None:
    assert read_worker_health(tmp_path) is None
