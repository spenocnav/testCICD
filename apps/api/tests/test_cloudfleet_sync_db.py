from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text

from app.db.session import AsyncSessionLocal
from app.services.cloudfleet_sync_service import (
    replace_schedules_window,
    upsert_vehicles,
    upsert_work_orders,
)


@pytest.mark.asyncio
async def test_cloudfleet_upserts_batch_deduplicate_and_skip_unchanged_rows() -> None:
    vehicles = [
        {"code": "BATCH-001", "brandName": "Marca A"},
        {"code": "BATCH-001", "brandName": "Marca final"},
        {"code": "BATCH-002", "brandName": "Marca B"},
    ]
    work_orders = [
        {"number": 9_900_001, "vehicleCode": "BATCH-001", "status": "OPEN"},
        {"number": 9_900_001, "vehicleCode": "BATCH-001", "status": "CLOSED"},
    ]

    async with AsyncSessionLocal() as session:
        assert await upsert_vehicles(session, vehicles) == 2
        assert await upsert_work_orders(session, work_orders) == 1
        await session.commit()

    async with AsyncSessionLocal() as session:
        before = (
            await session.execute(
                text("SELECT xmin::text FROM cloudfleet_vehicles WHERE code = 'BATCH-001'")
            )
        ).scalar_one()
        brand = (
            await session.execute(
                text("SELECT brand_name FROM cloudfleet_vehicles WHERE code = 'BATCH-001'")
            )
        ).scalar_one()
        status = (
            await session.execute(
                text("SELECT status FROM cloudfleet_work_orders WHERE number = 9900001")
            )
        ).scalar_one()
        assert brand == "Marca final"
        assert status == "CLOSED"

    async with AsyncSessionLocal() as session:
        assert await upsert_vehicles(session, vehicles) == 2
        await session.commit()

    async with AsyncSessionLocal() as session:
        after = (
            await session.execute(
                text("SELECT xmin::text FROM cloudfleet_vehicles WHERE code = 'BATCH-001'")
            )
        ).scalar_one()
    assert after == before


@pytest.mark.asyncio
async def test_schedule_window_is_replaced_with_batched_insert() -> None:
    first = [
        {
            "consecutive": 1,
            "vehicleCode": "BATCH-001",
            "dateToExecute": "2026-08-03T00:00:00Z",
            "task": "Aceite",
        },
        {
            "consecutive": 2,
            "vehicleCode": "BATCH-002",
            "dateToExecute": "2026-08-04T00:00:00Z",
            "task": "Filtro",
        },
    ]
    replacement = [first[1]]

    async with AsyncSessionLocal() as session:
        assert (
            await replace_schedules_window(
                session,
                first,
                date(2026, 8, 1),
                date(2026, 8, 31),
            )
            == 2
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        assert (
            await replace_schedules_window(
                session,
                replacement,
                date(2026, 8, 1),
                date(2026, 8, 31),
            )
            == 1
        )
        await session.commit()
        count = (
            await session.execute(
                text(
                    "SELECT count(*) FROM cloudfleet_maintenance_schedules "
                    "WHERE date_to_execute >= '2026-08-01' "
                    "AND date_to_execute < '2026-09-01'"
                )
            )
        ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_meter_update_runs_before_fetching_cloudfleet_vehicles(monkeypatch) -> None:
    from app.services import cloudfleet_meter_sync_service, cloudfleet_service
    from app.services.cloudfleet_sync_service import _sync_cloudfleet_core_unlocked

    calls: list[str] = []
    meter_summary = cloudfleet_meter_sync_service.empty_summary()
    meter_summary["meters_sent"] = 2

    async def sync_meters() -> dict[str, int]:
        calls.append("meters")
        return meter_summary

    async def list_vehicles() -> list[dict]:
        calls.append("vehicles")
        return []

    async def list_work_orders(*_args) -> list[dict]:
        calls.append("work_orders")
        return []

    async def list_schedules(*_args) -> list[dict]:
        calls.append("schedules")
        return []

    async def reconcile() -> int:
        calls.append("reconcile")
        return 0

    monkeypatch.setattr(cloudfleet_meter_sync_service, "sync_meters_before_snapshot", sync_meters)
    monkeypatch.setattr(cloudfleet_meter_sync_service, "reconcile_uncertain_meters", reconcile)
    monkeypatch.setattr(cloudfleet_service, "list_vehicles", list_vehicles)
    monkeypatch.setattr(cloudfleet_service, "list_work_orders", list_work_orders)
    monkeypatch.setattr(cloudfleet_service, "list_maintenance_schedules", list_schedules)

    summary = await _sync_cloudfleet_core_unlocked(date_from=date.today())

    assert calls.index("meters") < calls.index("vehicles") < calls.index("reconcile")
    assert summary["meters_sent"] == 2
