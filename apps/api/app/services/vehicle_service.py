"""Lectura y activación/desactivación de vehículos por flota.

Los vehículos son réplica del snapshot de Navi Vehículos (clave natural
`plate`); cuelgan directo de una flota (`fleet_id`, réplica de customer_id) y
opcionalmente de una base geotab. El portal no crea vehículos; solo los lista y
permite activar/desactivar.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.fleet import Fleet
from app.models.master_data import FleetVehicleGroup, Vehicle


async def list_vehicles_by_fleet(
    db: AsyncSession, fleet_id: uuid.UUID, *, only_active: bool | None = None
) -> Sequence[Vehicle]:
    stmt = (
        select(Vehicle)
        .where(Vehicle.fleet_id == fleet_id)
        .options(selectinload(Vehicle.geotab_database), selectinload(Vehicle.fleet))
        .order_by(Vehicle.plate)
    )
    if only_active is not None:
        stmt = stmt.where(Vehicle.is_active.is_(only_active))
    result = await db.execute(stmt)
    return result.scalars().all()


async def list_vehicles_by_fleets(
    db: AsyncSession,
    fleet_ids: Sequence[uuid.UUID],
    *,
    only_active: bool | None = None,
    group_ids: Sequence[uuid.UUID] | None = None,
) -> Sequence[Vehicle]:
    if not fleet_ids:
        return []
    stmt = (
        select(Vehicle)
        .where(Vehicle.fleet_id.in_(fleet_ids))
        .options(selectinload(Vehicle.geotab_database), selectinload(Vehicle.fleet))
        .order_by(Vehicle.plate)
    )
    if only_active is not None:
        stmt = stmt.where(Vehicle.is_active.is_(only_active))
    if group_ids:
        # El llamador manda el/los nodos YA expandidos (nodo + descendientes,
        # el árbol completo viaja en GET /vehicles/groups). Un grupo fuera del
        # alcance no matchea ningún vehículo del alcance: fail-closed.
        stmt = stmt.where(Vehicle.vehicle_group_id.in_(group_ids))
    result = await db.execute(stmt)
    return result.scalars().all()


async def list_fleet_vehicle_groups(
    db: AsyncSession, fleet_ids: Sequence[uuid.UUID]
) -> Sequence[FleetVehicleGroup]:
    """Árbol plano de grupos internos de las flotas del alcance.

    Incluye grupos inactivos: un vehículo puede seguir apuntando a uno y la UI
    debe poder nombrarlo (los pinta como inactivos, no los ofrece al filtrar).
    """
    if not fleet_ids:
        return []
    stmt = (
        select(FleetVehicleGroup)
        .where(FleetVehicleGroup.fleet_id.in_(fleet_ids))
        .order_by(FleetVehicleGroup.fleet_id, FleetVehicleGroup.name)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def list_vehicles_by_fleets_paginated(
    db: AsyncSession,
    fleet_ids: Sequence[uuid.UUID],
    *,
    search: str | None = None,
    marca: str | None = None,
    motor_type: str | None = None,
    only_active: bool | None = None,
    group_ids: Sequence[uuid.UUID] | None = None,
    sort_by: str | None = None,
    sort_order: str = "asc",
    limit: int = 20,
    offset: int = 0,
) -> tuple[Sequence[Vehicle], int, int, int]:
    if not fleet_ids:
        return [], 0, 0, 0

    sort_columns = {
        "plate": Vehicle.plate,
        "fleet": Fleet.name,
        "marca": Vehicle.marca,
        "motor_type": Vehicle.motor_type,
        "ano_modelo": Vehicle.ano_modelo,
    }
    sort_column = sort_columns.get(sort_by or "plate", Vehicle.plate)
    sort_expression = sort_column.desc() if sort_order == "desc" else sort_column.asc()
    needs_fleet_join = bool(search) or sort_by == "fleet"
    stmt = select(Vehicle).where(Vehicle.fleet_id.in_(fleet_ids))
    count_stmt = select(func.count(Vehicle.id)).where(Vehicle.fleet_id.in_(fleet_ids))
    total_all_stmt = select(func.count(Vehicle.id)).where(Vehicle.fleet_id.in_(fleet_ids))
    active_count_stmt = select(func.count(Vehicle.id)).where(Vehicle.fleet_id.in_(fleet_ids))
    if needs_fleet_join:
        stmt = stmt.outerjoin(Fleet, Vehicle.fleet_id == Fleet.id)
        count_stmt = count_stmt.outerjoin(Fleet, Vehicle.fleet_id == Fleet.id)
        total_all_stmt = total_all_stmt.outerjoin(Fleet, Vehicle.fleet_id == Fleet.id)
        active_count_stmt = active_count_stmt.outerjoin(Fleet, Vehicle.fleet_id == Fleet.id)
    tie_breaker = Vehicle.id if sort_by in (None, "plate") else Vehicle.plate.asc()
    stmt = stmt.options(
        selectinload(Vehicle.geotab_database), selectinload(Vehicle.fleet)
    ).order_by(sort_expression.nulls_last(), tie_breaker)

    if only_active is not None:
        stmt = stmt.where(Vehicle.is_active.is_(only_active))
        count_stmt = count_stmt.where(Vehicle.is_active.is_(only_active))

    if marca:
        stmt = stmt.where(Vehicle.marca == marca)
        count_stmt = count_stmt.where(Vehicle.marca == marca)
        total_all_stmt = total_all_stmt.where(Vehicle.marca == marca)
        active_count_stmt = active_count_stmt.where(Vehicle.marca == marca)

    if motor_type:
        stmt = stmt.where(Vehicle.motor_type == motor_type)
        count_stmt = count_stmt.where(Vehicle.motor_type == motor_type)
        total_all_stmt = total_all_stmt.where(Vehicle.motor_type == motor_type)
        active_count_stmt = active_count_stmt.where(Vehicle.motor_type == motor_type)

    if group_ids:
        # Nodos ya expandidos por el llamador (ver list_vehicles_by_fleets).
        group_condition = Vehicle.vehicle_group_id.in_(group_ids)
        stmt = stmt.where(group_condition)
        count_stmt = count_stmt.where(group_condition)
        total_all_stmt = total_all_stmt.where(group_condition)
        active_count_stmt = active_count_stmt.where(group_condition)

    if search:
        pattern = f"%{search.lower()}%"
        condition = or_(
            func.lower(Vehicle.plate).like(pattern),
            func.lower(Vehicle.marca).like(pattern),
            func.lower(Vehicle.linea).like(pattern),
            func.lower(Vehicle.marketing_model_name).like(pattern),
            func.lower(Vehicle.service_model_name).like(pattern),
            func.lower(Vehicle.motor_type).like(pattern),
            func.lower(Vehicle.ano_modelo).like(pattern),
            func.lower(Vehicle.tipo_combustible).like(pattern),
            func.lower(Vehicle.nombre_vehiculo).like(pattern),
            func.lower(Fleet.name).like(pattern),
            func.lower(Fleet.code).like(pattern),
        )
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
        total_all_stmt = total_all_stmt.where(condition)
        active_count_stmt = active_count_stmt.where(condition)

    items_result = await db.execute(stmt.limit(limit).offset(offset))
    total = await db.scalar(count_stmt)
    total_all = await db.scalar(total_all_stmt)
    active_total = await db.scalar(active_count_stmt.where(Vehicle.is_active.is_(True)))
    return (
        items_result.scalars().all(),
        int(total or 0),
        int(total_all or 0),
        int(active_total or 0),
    )


async def get_vehicle_in_fleet(
    db: AsyncSession, fleet_id: uuid.UUID, vehicle_id: uuid.UUID
) -> Vehicle | None:
    stmt = (
        select(Vehicle)
        .where(Vehicle.id == vehicle_id, Vehicle.fleet_id == fleet_id)
        .options(selectinload(Vehicle.geotab_database), selectinload(Vehicle.fleet))
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_vehicle_by_id(db: AsyncSession, vehicle_id: uuid.UUID) -> Vehicle | None:
    stmt = (
        select(Vehicle)
        .where(Vehicle.id == vehicle_id)
        .options(selectinload(Vehicle.geotab_database), selectinload(Vehicle.fleet))
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def set_vehicle_active(db: AsyncSession, vehicle: Vehicle, is_active: bool) -> Vehicle:
    vehicle.is_active = is_active
    await db.flush()
    return vehicle
