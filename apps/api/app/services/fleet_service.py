"""Operaciones de dominio sobre Flotas y asignación usuario<->flota."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fleet import Fleet
from app.models.master_data import Vehicle
from app.models.user import User
from app.services.rbac import user_is_admin


async def fleet_ids_with_active_vehicles(
    db: AsyncSession, fleet_ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    """Subconjunto de `fleet_ids` con >=1 vehículo activo."""
    if not fleet_ids:
        return set()
    result = await db.execute(
        select(Vehicle.fleet_id)
        .where(Vehicle.is_active, Vehicle.fleet_id.in_(list(fleet_ids)))
        .distinct()
    )
    return {row[0] for row in result if row[0] is not None}


async def get_fleet_by_id(db: AsyncSession, fleet_id: uuid.UUID) -> Fleet | None:
    result = await db.execute(select(Fleet).where(Fleet.id == fleet_id))
    return result.scalar_one_or_none()


async def get_fleet_by_code(db: AsyncSession, code: str) -> Fleet | None:
    result = await db.execute(select(Fleet).where(Fleet.code == code))
    return result.scalar_one_or_none()


async def list_fleets(
    db: AsyncSession, *, only_active: bool | None = None
) -> Sequence[Fleet]:
    stmt = select(Fleet)
    if only_active is not None:
        stmt = stmt.where(Fleet.is_active.is_(only_active))
    stmt = stmt.order_by(Fleet.name)
    result = await db.execute(stmt)
    return result.scalars().all()


async def list_fleets_for_user(
    db: AsyncSession,
    user: User,
    *,
    only_active: bool | None = None,
) -> Sequence[Fleet]:
    """Lista el catálogo global solo para admin; el resto ve sus asignadas."""
    if user_is_admin(user):
        return await list_fleets(db, only_active=only_active)

    fleet_ids = [fleet.id for fleet in user.fleets]
    if not fleet_ids:
        return []
    stmt = select(Fleet).where(Fleet.id.in_(fleet_ids))
    if only_active is not None:
        stmt = stmt.where(Fleet.is_active.is_(only_active))
    stmt = stmt.order_by(Fleet.name)
    result = await db.execute(stmt)
    return result.scalars().all()


async def load_fleets_by_ids(
    db: AsyncSession, ids: Sequence[uuid.UUID]
) -> Sequence[Fleet]:
    if not ids:
        return []
    result = await db.execute(select(Fleet).where(Fleet.id.in_(ids)))
    return result.scalars().all()


async def create_fleet(
    db: AsyncSession, *, code: str, name: str, is_active: bool = True
) -> Fleet:
    fleet = Fleet(code=code, name=name, is_active=is_active)
    db.add(fleet)
    await db.flush()
    await db.refresh(fleet)
    return fleet


async def update_fleet(
    db: AsyncSession,
    fleet: Fleet,
    *,
    code: str | None = None,
    name: str | None = None,
    is_active: bool | None = None,
    ralenti_analysis_enabled: bool | None = None,
) -> Fleet:
    if code is not None:
        fleet.code = code
    if name is not None:
        fleet.name = name
    if is_active is not None:
        fleet.is_active = is_active
    if ralenti_analysis_enabled is not None:
        fleet.ralenti_analysis_enabled = ralenti_analysis_enabled
    await db.flush()
    await db.refresh(fleet)
    return fleet


async def deactivate_fleet(db: AsyncSession, fleet: Fleet) -> Fleet:
    fleet.is_active = False
    await db.flush()
    return fleet


async def accessible_fleets(db: AsyncSession, user: User) -> Sequence[Fleet]:
    """Flotas que el usuario puede ver. admin = todas las activas."""
    if user_is_admin(user):
        return await list_fleets(db, only_active=True)
    return [f for f in user.fleets if f.is_active]


def user_can_access_fleet(user: User, fleet_id: uuid.UUID) -> bool:
    """¿El usuario tiene acceso a esa flota? admin siempre."""
    if user_is_admin(user):
        return True
    return any(f.id == fleet_id for f in user.fleets)
