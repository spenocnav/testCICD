"""Operaciones de dominio sobre Users."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.rbac_constants import ADMIN_ROLE_CODE
from app.core.security import hash_password_async
from app.models.fleet import Fleet, user_fleets
from app.models.role import Role
from app.models.user import User
from app.services.rbac import get_role_permission_codes, get_user_permission_codes, user_is_admin


def _with_roles_perms() -> tuple:
    return (
        selectinload(User.roles).selectinload(Role.permissions),
        selectinload(User.fleets),
    )


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await db.execute(select(User).options(*_with_roles_perms()).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).options(*_with_roles_perms()).where(User.email == email))
    return result.scalar_one_or_none()


async def list_users(
    db: AsyncSession,
    *,
    search: str | None = None,
    role_code: str | None = None,
    only_active: bool | None = None,
    include_archived: bool = False,
    limit: int = 20,
    offset: int = 0,
    fleet_ids: Sequence[uuid.UUID] | None = None,
) -> tuple[Sequence[User], int]:
    if fleet_ids is not None and not fleet_ids:
        return [], 0

    stmt = select(User)
    count_stmt = select(func.count(User.id))

    if fleet_ids is not None:
        scoped_user_ids = select(user_fleets.c.user_id).where(user_fleets.c.fleet_id.in_(fleet_ids))
        stmt = stmt.where(User.id.in_(scoped_user_ids))
        count_stmt = count_stmt.where(User.id.in_(scoped_user_ids))

    if search:
        pattern = f"%{search.lower()}%"
        condition = or_(
            func.lower(User.email).like(pattern),
            func.lower(User.full_name).like(pattern),
        )
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    if only_active is not None:
        stmt = stmt.where(User.is_active.is_(only_active))
        count_stmt = count_stmt.where(User.is_active.is_(only_active))

    if not include_archived:
        stmt = stmt.where(User.is_archived.is_(False))
        count_stmt = count_stmt.where(User.is_archived.is_(False))

    if role_code:
        stmt = stmt.join(User.roles).where(Role.code == role_code)
        count_stmt = count_stmt.join(User.roles).where(Role.code == role_code)

    stmt = (
        stmt.options(*_with_roles_perms())
        .order_by(User.created_at.desc(), User.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items_result = await db.execute(stmt)
    total_result = await db.execute(count_stmt)

    return items_result.scalars().unique().all(), total_result.scalar_one()


async def create_user(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str,
    role_codes: Sequence[str] = (),
    fleet_ids: Sequence[uuid.UUID] | None = None,
    is_active: bool = True,
) -> User:
    user = User(
        email=email,
        password_hash=await hash_password_async(password),
        full_name=full_name,
        is_active=is_active,
    )
    if role_codes:
        roles = await _load_roles_by_codes(db, role_codes)
        user.roles = list(roles)
    if fleet_ids:
        fleets = await _load_fleets_by_ids(db, fleet_ids)
        user.fleets = list(fleets)
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def validate_user_delegation(
    db: AsyncSession,
    requester: User,
    *,
    role_codes: Sequence[str] | None,
    fleet_ids: Sequence[uuid.UUID] | None,
    target: User | None = None,
) -> None:
    """Limita la delegación de usuarios al alcance efectivo del actor."""
    if user_is_admin(requester):
        return

    requester_permissions = get_user_permission_codes(requester)
    requester_fleet_ids = {fleet.id for fleet in requester.fleets if fleet.is_active}

    if target is not None:
        target_fleet_ids = {fleet.id for fleet in target.fleets}
        if not target_fleet_ids or not target_fleet_ids.issubset(requester_fleet_ids):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No puedes gestionar usuarios fuera de tus flotas",
            )
        if get_user_permission_codes(target) - requester_permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No puedes gestionar un usuario con permisos superiores",
            )

    if role_codes is not None:
        normalized_roles = list(dict.fromkeys(role_codes))
        roles = await _load_roles_by_codes(db, normalized_roles)
        found_codes = {role.code for role in roles}
        missing_codes = set(normalized_roles) - found_codes
        if missing_codes:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Roles inexistentes: {', '.join(sorted(missing_codes))}",
            )
        if "admin" in found_codes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo un administrador puede asignar el rol admin",
            )
        delegated_permissions = get_role_permission_codes(roles)
        if not delegated_permissions.issubset(requester_permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No puedes asignar permisos que no posees",
            )

    if fleet_ids is not None:
        requested_fleet_ids = set(fleet_ids)
        if not requested_fleet_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Debes asignar al usuario al menos una flota de tu alcance",
            )
        if not requested_fleet_ids.issubset(requester_fleet_ids):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No puedes asignar flotas fuera de tu alcance",
            )
        fleets = await _load_fleets_by_ids(db, list(requested_fleet_ids))
        if len(fleets) != len(requested_fleet_ids):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Una o más flotas no existen",
            )


async def update_user(
    db: AsyncSession,
    user: User,
    *,
    full_name: str | None = None,
    is_active: bool | None = None,
    role_codes: Sequence[str] | None = None,
    fleet_ids: Sequence[uuid.UUID] | None = None,
    password: str | None = None,
    is_archived: bool | None = None,
) -> User:
    if full_name is not None:
        user.full_name = full_name
    if is_active is not None:
        user.is_active = is_active
    if is_archived is not None:
        user.is_archived = is_archived
    if password is not None:
        user.password_hash = await hash_password_async(password)
    if role_codes is not None:
        roles = await _load_roles_by_codes(db, role_codes)
        user.roles = list(roles)
    if fleet_ids is not None:
        fleets = await _load_fleets_by_ids(db, fleet_ids)
        user.fleets = list(fleets)
    await db.flush()
    await db.refresh(user)
    return user


async def deactivate_user(db: AsyncSession, user: User) -> User:
    user.is_active = False
    await db.flush()
    return user


async def count_active_admins(db: AsyncSession) -> int:
    """Número de usuarios activos que tienen el rol admin.

    Se usa para impedir el auto-bloqueo (dejar el sistema sin administradores).
    """
    stmt = (
        select(func.count(func.distinct(User.id)))
        .join(User.roles)
        .where(
            Role.code == ADMIN_ROLE_CODE,
            User.is_active.is_(True),
            User.is_archived.is_(False),
        )
    )
    return (await db.execute(stmt)).scalar_one()


async def _load_roles_by_codes(db: AsyncSession, codes: Sequence[str]) -> Sequence[Role]:
    if not codes:
        return []
    result = await db.execute(select(Role).where(Role.code.in_(codes)))
    return result.scalars().all()


async def _load_fleets_by_ids(db: AsyncSession, ids: Sequence[uuid.UUID]) -> Sequence[Fleet]:
    if not ids:
        return []
    result = await db.execute(select(Fleet).where(Fleet.id.in_(ids)))
    return result.scalars().all()
