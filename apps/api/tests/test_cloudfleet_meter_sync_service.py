from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.services import cloudfleet_meter_sync_service as svc
from app.services import geotab_service


@pytest.mark.asyncio
async def test_read_target_meters_converts_geotab_meters_and_seconds(monkeypatch) -> None:
    readings = iter(
        [
            geotab_service.GeotabMeterReading(
                date_time=datetime(2026, 8, 12, 14, 30, tzinfo=UTC),
                value=Decimal("986275000"),
            ),
            geotab_service.GeotabMeterReading(
                date_time=datetime(2026, 8, 12, 14, 31, tzinfo=UTC),
                value=Decimal("4444200"),
            ),
        ]
    )

    async def latest(*_args, **_kwargs):
        return next(readings)

    monkeypatch.setattr(geotab_service, "get_latest_meter_reading", latest)
    target = svc.MeterTarget(
        vehicle_id=uuid.uuid4(),
        vehicle_code="ABC236",
        database_key="fleet_a",
        geotab_device_id="device-1",
    )
    session = geotab_service.GeotabSession(server="my.geotab.com", credentials={})
    async with httpx.AsyncClient() as client:
        result = await svc._read_target_meters(
            client,
            session,
            target,
            from_date=datetime(2026, 8, 5, tzinfo=UTC),
        )

    assert [(item.meter_type, item.meter_value) for item in result] == [
        ("distance", Decimal("986275")),
        ("hours", Decimal("1234.5")),
    ]
