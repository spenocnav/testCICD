from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.services import cloudfleet_tracking_ingest_service as ingest_module
from app.services.cloudfleet_tracking_ingest_service import ingest_runtime, read_runtime

CATALOG = {
    "generatedAt": "2026-08-17T14:05:00Z",
    "orders": [
        {
            "number": 10482,
            "vehicleCode": "ABC123",
            "status": "opened",
            "type": "Correctivo",
            "city": {"id": 2, "name": "Tintalito"},
            "startDate": "2026-08-17T12:00:00Z",
        }
    ],
}
LEDGER_ROW = {
    "work_order_number": 10482,
    "tracking_id": 501,
    "tracking_key": "id:501",
    "tracking_date": "2026-08-17T13:00:00Z",
    "tracking_comment": "En diagnostico",
    "tracking_observed_from": "2026-08-17T13:55:00Z",
    "tracking_observed_at": "2026-08-17T14:00:00Z",
    "tracking_observation_kind": "live",
    "extracted_at": "2026-08-17T14:00:00Z",
}


def _runtime(tmp_path: Path) -> Path:
    (tmp_path / "order_catalog.json").write_text(
        json.dumps(CATALOG), encoding="utf-8"
    )
    (tmp_path / "tracking_ledger.jsonl").write_text(
        json.dumps(LEDGER_ROW) + "\n", encoding="utf-8"
    )
    return tmp_path


class _FakeSession:
    """Records the calls the ingest makes without touching a database."""

    def __init__(self) -> None:
        self.executed: list[Any] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.executed.append(statement)
        raise AssertionError("La ingesta no debe emitir SQL sin pasar por los helpers")


@pytest.mark.asyncio
async def test_ingest_parses_a_stored_iso_watermark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``get_watermark`` returns ISO text; the cutoff must stay a datetime.

    Regression: subtracting a timedelta from the raw string raised TypeError on
    every cycle after the first, and the loop swallowed it forever.
    """
    seen: dict[str, Any] = {}

    async def fake_get_watermark(db: Any, key: str) -> str:
        return "2026-08-17T13:00:00+00:00"

    async def fake_set_watermark(db: Any, key: str, value: Any, now: Any) -> None:
        seen["watermark"] = value

    async def fake_upsert_events(db: Any, rows: Any, **kwargs: Any) -> int:
        seen["events"] = list(rows)
        return len(seen["events"])

    monkeypatch.setattr(ingest_module, "get_watermark", fake_get_watermark)
    monkeypatch.setattr(ingest_module, "_set_watermark", fake_set_watermark)
    monkeypatch.setattr(ingest_module, "upsert_tracking_events", fake_upsert_events)

    result = await ingest_runtime(_FakeSession(), _runtime(tmp_path))

    assert result["tracking_events_inserted"] == 1
    assert seen["watermark"] == datetime(2026, 8, 17, 14, tzinfo=UTC)


@pytest.mark.asyncio
async def test_ingest_never_writes_the_reduced_catalog_to_work_orders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The catalog is a reduced projection and must stay in memory.

    ``cloudfleet_work_orders`` is owned by the CloudFleet sync, which carries
    costs, reason, detected issue and raw. A second writer holding fewer columns
    would blank them on every cycle.
    """

    async def fake_get_watermark(db: Any, key: str) -> None:
        return None

    async def fake_set_watermark(db: Any, key: str, value: Any, now: Any) -> None:
        return None

    captured: list[Any] = []

    async def fake_upsert_events(db: Any, rows: Any, **kwargs: Any) -> int:
        captured.extend(rows)
        return len(captured)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("La ingesta no debe upsertear cloudfleet_work_orders")

    monkeypatch.setattr(ingest_module, "get_watermark", fake_get_watermark)
    monkeypatch.setattr(ingest_module, "_set_watermark", fake_set_watermark)
    monkeypatch.setattr(ingest_module, "upsert_tracking_events", fake_upsert_events)
    monkeypatch.setattr(
        ingest_module, "upsert_work_orders", forbidden, raising=False
    )

    result = await ingest_runtime(_FakeSession(), _runtime(tmp_path))

    assert result["catalog_orders"] == 1
    assert "orders_upserted" not in result
    # The catalog still enriches the event in memory.
    assert captured[0]["vehicle_code"] == "ABC123"
    assert captured[0]["work_order_status"] == "opened"
    assert captured[0]["label_id"] == 3


def test_read_runtime_skips_rows_below_the_cutoff(tmp_path: Path) -> None:
    snapshot = read_runtime(
        _runtime(tmp_path),
        tracking_after=datetime(2026, 8, 17, 15, tzinfo=UTC),
    )

    assert snapshot.tracking_rows == []
    assert snapshot.latest_extracted_at is None
