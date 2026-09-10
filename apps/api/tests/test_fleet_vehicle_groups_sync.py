"""Réplica de los grupos internos de vehículos (fleet_vehicle_groups).

Mismo protocolo que test_sync_service.py: `apply_snapshot` sobre una sesión con
ROLLBACK al final (no commitea), marcado integration porque exige PostgreSQL.

Invariantes que se fijan aquí:
- el árbol (parent_id) se replica y el vehículo apunta a su nodo local;
- un grupo que deja de venir se DESACTIVA (nunca se borra), también en un
  sync incremental, porque los grupos viajan completos por cliente;
- un payload viejo sin la clave `groups`/`customer_group_id` no toca nada:
  la ausencia de la clave no es evidencia de una baja;
- un incremental que trae al vehículo pero no a su cliente resuelve el grupo
  contra la réplica local en vez de limpiar la asignación;
- `customer_group_id: null` explícito sí limpia.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from app.db.session import AsyncSessionLocal
from app.services.sync_service import apply_snapshot

_GENERATED_AT = "2026-08-31T12:00:00Z"

# source_ids altos y propios para no chocar con seed/flotas reales.
_CUSTOMER_ID = 991012
_GROUP_REGIONAL = 991201
_GROUP_CEDI = 991202
_GROUP_OTRO = 991203
_PLATE = "GRPT001"


def _vehicle(customer_group_id: int | None, *, include_key: bool = True) -> dict[str, Any]:
    v: dict[str, Any] = {
        "plate": _PLATE,
        "vin": "GRPVIN0001",
        "geotab_device_id": None,
        "geotab_device_synced_at": None,
        "customer_id": _CUSTOMER_ID,
        "customer_database_id": None,
        "geotab_customer_database_id": None,
        "geotab_customer_status": "not_applicable",
        "engine_number": "E1",
        "technical_number": "T1",
        "cpl": "1000",
        "motor_type": "GRPMOT",
        "vocacional": False,
        "category": "Flota Administrada",
        "updated_at": "2026-08-31T08:00:00Z",
    }
    if include_key:
        v["customer_group_id"] = customer_group_id
    return v


def _payload(
    *,
    groups: list[dict[str, Any]] | None,
    vehicle_group: int | None,
    include_groups_key: bool = True,
    include_vehicle_group_key: bool = True,
) -> dict[str, Any]:
    customer: dict[str, Any] = {
        "id": _CUSTOMER_ID,
        "name": "GroupTest Transportes",
        "updated_at": "2026-08-31T10:00:00Z",
        "databases": [],
    }
    if include_groups_key:
        customer["groups"] = groups or []
    return {
        "generated_at": _GENERATED_AT,
        "since": None,
        "customers": [customer],
        "vehicles": [_vehicle(vehicle_group, include_key=include_vehicle_group_key)],
    }


def _tree() -> list[dict[str, Any]]:
    return [
        {
            "id": _GROUP_REGIONAL,
            "parent_id": None,
            "name": "Regional Antioquia",
            "is_active": True,
            "updated_at": "2026-08-31T09:00:00Z",
        },
        {
            "id": _GROUP_CEDI,
            "parent_id": _GROUP_REGIONAL,
            "name": "CEDI Medellin",
            "is_active": True,
            "updated_at": "2026-08-31T09:00:00Z",
        },
        {
            "id": _GROUP_OTRO,
            "parent_id": None,
            "name": "Regional Costa",
            "is_active": True,
            "updated_at": "2026-08-31T09:00:00Z",
        },
    ]


async def _group_rows(s) -> dict[int, Any]:
    rows = (
        await s.execute(
            text(
                "SELECT source_id, id, parent_id, name, is_active "
                "FROM fleet_vehicle_groups WHERE source_id = ANY(:sids)"
            ),
            {"sids": [_GROUP_REGIONAL, _GROUP_CEDI, _GROUP_OTRO]},
        )
    ).all()
    return {int(r.source_id): r for r in rows}


async def _vehicle_group_id(s):
    return (
        await s.execute(
            text("SELECT vehicle_group_id FROM vehicles WHERE plate = :p"),
            {"p": _PLATE},
        )
    ).scalar_one()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_replicates_tree_and_vehicle_assignment() -> None:
    async with AsyncSessionLocal() as s:
        try:
            res = await apply_snapshot(
                s, _payload(groups=_tree(), vehicle_group=_GROUP_CEDI), full=False
            )
            assert res.vehicle_groups == 3

            groups = await _group_rows(s)
            assert set(groups) == {_GROUP_REGIONAL, _GROUP_CEDI, _GROUP_OTRO}
            assert groups[_GROUP_CEDI].parent_id == groups[_GROUP_REGIONAL].id
            assert groups[_GROUP_REGIONAL].parent_id is None
            assert all(g.is_active for g in groups.values())

            assert await _vehicle_group_id(s) == groups[_GROUP_CEDI].id
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_missing_group_is_deactivated_not_deleted() -> None:
    async with AsyncSessionLocal() as s:
        try:
            await apply_snapshot(
                s, _payload(groups=_tree(), vehicle_group=_GROUP_CEDI), full=False
            )
            # Segundo sync: "Regional Costa" ya no viene (se borró en el origen).
            remaining = [g for g in _tree() if g["id"] != _GROUP_OTRO]
            await apply_snapshot(
                s, _payload(groups=remaining, vehicle_group=_GROUP_CEDI), full=False
            )

            groups = await _group_rows(s)
            # Sigue existiendo, desactivado; los demás intactos.
            assert set(groups) == {_GROUP_REGIONAL, _GROUP_CEDI, _GROUP_OTRO}
            assert groups[_GROUP_OTRO].is_active is False
            assert groups[_GROUP_REGIONAL].is_active is True
            assert groups[_GROUP_CEDI].is_active is True
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_old_payload_without_keys_touches_nothing() -> None:
    async with AsyncSessionLocal() as s:
        try:
            await apply_snapshot(
                s, _payload(groups=_tree(), vehicle_group=_GROUP_CEDI), full=False
            )
            before = await _group_rows(s)

            # Payload viejo: ni `groups` en el cliente ni `customer_group_id`
            # en el vehículo. Nada se desactiva y la asignación se conserva.
            await apply_snapshot(
                s,
                _payload(
                    groups=None,
                    vehicle_group=None,
                    include_groups_key=False,
                    include_vehicle_group_key=False,
                ),
                full=False,
            )

            after = await _group_rows(s)
            assert {sid: r.is_active for sid, r in after.items()} == {
                sid: r.is_active for sid, r in before.items()
            }
            assert await _vehicle_group_id(s) == before[_GROUP_CEDI].id
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_incremental_without_customer_resolves_group_locally() -> None:
    async with AsyncSessionLocal() as s:
        try:
            await apply_snapshot(
                s, _payload(groups=_tree(), vehicle_group=_GROUP_CEDI), full=False
            )
            groups = await _group_rows(s)

            # Incremental: cambió el vehículo pero su cliente no vino.
            incremental = {
                "generated_at": _GENERATED_AT,
                "since": "2026-08-31T11:00:00Z",
                "customers": [],
                "vehicles": [_vehicle(_GROUP_CEDI)],
            }
            await apply_snapshot(s, incremental, full=False)

            assert await _vehicle_group_id(s) == groups[_GROUP_CEDI].id
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_explicit_null_clears_vehicle_group() -> None:
    async with AsyncSessionLocal() as s:
        try:
            await apply_snapshot(
                s, _payload(groups=_tree(), vehicle_group=_GROUP_CEDI), full=False
            )
            assert await _vehicle_group_id(s) is not None

            await apply_snapshot(
                s, _payload(groups=_tree(), vehicle_group=None), full=False
            )
            assert await _vehicle_group_id(s) is None
        finally:
            await s.rollback()
