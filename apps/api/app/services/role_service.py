"""Operaciones de dominio sobre Roles y su matriz de permisos."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.rbac_constants import ACTION_EDIT, ACTION_VIEW, perm_code
from app.models.permission import Permission
from app.models.role import Role


async def list_roles(db: AsyncSession) -> Sequence[Role]:
    result = await db.execute(
        select(Role).options(selectinload(Role.permissions)).order_by(Role.name)
    )
    return result.scalars().all()


async def get_role(db: AsyncSession, role_id: uuid.UUID) -> Role | None:
    result = await db.execute(
        select(Role).options(selectinload(Role.permissions)).where(Role.id == role_id)
    )
    return result.scalar_one_or_none()


async def get_role_by_code(db: AsyncSession, code: str) -> Role | None:
    result = await db.execute(
        select(Role).options(selectinload(Role.permissions)).where(Role.code == code)
    )
    return result.scalar_one_or_none()


def _expand_edit_implies_view(codes: Sequence[str]) -> set[str]:
    """Si se concede `<modulo>.edit`, asegura también `<modulo>.view`."""
    expanded = set(codes)
    for code in codes:
        if code.endswith(f".{ACTION_EDIT}"):
            module = code.rsplit(".", 1)[0]
            expanded.add(perm_code(module, ACTION_VIEW))
    return expanded


async def _resolve_permissions(db: AsyncSession, codes: Sequence[str]) -> list[Permission]:
    wanted = _expand_edit_implies_view(codes)
    if not wanted:
        return []
    result = await db.execute(select(Permission).where(Permission.code.in_(wanted)))
    found = result.scalars().all()
    found_codes = {p.code for p in found}
    missing = wanted - found_codes
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Permisos inexistentes: {', '.join(sorted(missing))}",
        )
    return list(found)


async def create_role(
    db: AsyncSession,
    *,
    code: str,
    name: str,
    description: str | None,
    permission_codes: Sequence[str],
) -> Role:
    existing = await get_role_by_code(db, code)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya existe un rol con code '{code}'",
        )
    role = Role(
        code=code,
        name=name,
        description=description,
        is_system=False,
        permissions=await _resolve_permissions(db, permission_codes),
    )
    db.add(role)
    await db.flush()
    return role


async def update_role(
    db: AsyncSession,
    role: Role,
    *,
    name: str | None,
    description: str | None,
) -> Role:
    if name is not None:
        role.name = name
    if description is not None:
        role.description = description
    await db.flush()
    return role


async def set_role_permissions(
    db: AsyncSession,
    role: Role,
    *,
    permission_codes: Sequence[str],
) -> Role:
    role.permissions = await _resolve_permissions(db, permission_codes)
    await db.flush()
    return role


async def delete_role(db: AsyncSession, role: Role) -> None:
    if role.is_system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No se puede eliminar un rol del sistema",
        )
    await db.delete(role)
    await db.flush()
