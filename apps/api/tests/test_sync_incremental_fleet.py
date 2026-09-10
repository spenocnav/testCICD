"""Un incremental que trae al vehículo pero no a su cliente conserva la flota.

Mismo protocolo que test_sync_service.py: `apply_snapshot` sobre una sesión con
ROLLBACK al final, marcado integration porque exige PostgreSQL.

Defecto que fija (2026-09-02, CFS LOGISTICS): el export de Navi manda un
vehículo en un incremental cuando cambia él o su binding Geotab, sin traer al
cliente si este no cambió. `fleet_by_source`/`db_by_source` se armaban sólo con
los clientes del payload, así que el upsert escribía `fleet_id` y
`geotab_database_id` en NULL y la flota perdía sus vehículos hasta el siguiente
full sync. La resolución debe caer a la réplica local, como ya hacían los grupos.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from app.db.session import AsyncSessionLocal
from app.services.sync_service import apply_snapshot

_GENERATED_AT = "2026-09-02T12:00:00Z"
_CUSTOMER_ID = 992024
_DATABASE_ID = 992026
_PLATE = "INCF001"


def _vehicle(customer_id: int | None, database_id: int | None) -> dict[str, Any]:
    return {
        "plate": _PLATE,
        "vin": "INCVIN0001",
        "geotab_device_id": "b9ZZ",
        "geotab_device_synced_at": "2026-09-02T11:00:00Z",
        "customer_id": customer_id,
        "customer_database_id": database_id,
        "geotab_customer_database_id": database_id,
        "geotab_customer_status": "found" if database_id is not None else "not_applicable",
        "engine_number": "E1",
        "technical_number": "T1",
        "cpl": "5248",
        "motor_type": "INCMOT",
        "vocacional": False,
        "category": "Experiencia Superior",
        "updated_at": "2026-09-02T11:00:00Z",
    }


def _full_payload() -> dict[str, Any]:
    return {
        "generated_at": _GENERATED_AT,
        "since": None,
        "customers": [
            {
                "id": _CUSTOMER_ID,
                "name": "IncTest Logistics",
                "updated_at": "2026-08-25T15:00:00Z",
                "databases": [
                    {
                        "id": _DATABASE_ID,
                        "database_name": "inctest_db",
                        "database_key": "inctest_db",
                        "connection_type": "geotab",
                        "provider_config": {},
                        "updated_at": "2026-08-25T15:00:00Z",
                        "credentials": [],
                        "rules": [],
                    }
                ],
            }
        ],
        "vehicles": [_vehicle(_CUSTOMER_ID, _DATABASE_ID)],
    }


async def _vehicle_links(s) -> tuple[Any, Any]:
    row = await s.execute(
        text("SELECT fleet_id, geotab_database_id FROM vehicles WHERE plate = :p"),
        {"p": _PLATE},
    )
    return row.one()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_incremental_without_customer_keeps_fleet_and_database() -> None:
    async with AsyncSessionLocal() as s:
        try:
            await apply_snapshot(s, _full_payload(), full=False)
            fleet_id, db_id = await _vehicle_links(s)
            assert fleet_id is not None and db_id is not None

            # Incremental: cambió el vehículo (o su binding) pero el cliente no vino.
            incremental = {
                "generated_at": _GENERATED_AT,
                "since": "2026-09-02T09:00:00Z",
                "customers": [],
                "vehicles": [_vehicle(_CUSTOMER_ID, _DATABASE_ID)],
            }
            await apply_snapshot(s, incremental, full=False)

            assert await _vehicle_links(s) == (fleet_id, db_id)
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_explicit_unassignment_still_clears_fleet() -> None:
    """`customer_id: null` es una desasignación real y debe seguir limpiando."""
    async with AsyncSessionLocal() as s:
        try:
            await apply_snapshot(s, _full_payload(), full=False)
            incremental = {
                "generated_at": _GENERATED_AT,
                "since": "2026-09-02T09:00:00Z",
                "customers": [],
                "vehicles": [_vehicle(None, None)],
            }
            await apply_snapshot(s, incremental, full=False)

            assert await _vehicle_links(s) == (None, None)
        finally:
            await s.rollback()
