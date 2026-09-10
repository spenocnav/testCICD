from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission, require_platform_admin
from app.db.session import get_db
from app.models.user import User
from app.schemas.role import RoleCreate, RolePermissionsUpdate, RoleRead, RoleUpdate
from app.services import role_service

router = APIRouter(prefix="/roles", tags=["roles"])


@router.get(
    "",
    response_model=list[RoleRead],
    dependencies=[Depends(require_permission("roles.view"))],
)
async def list_roles(db: AsyncSession = Depends(get_db)) -> list[RoleRead]:
    roles = await role_service.list_roles(db)
    return [RoleRead.model_validate(r) for r in roles]


@router.post(
    "",
    response_model=RoleRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_platform_admin)],
)
async def create_role(payload: RoleCreate, db: AsyncSession = Depends(get_db)) -> RoleRead:
    role = await role_service.create_role(
        db,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        permission_codes=payload.permission_codes,
    )
    await db.commit()
    fresh = await role_service.get_role(db, role.id)
    assert fresh is not None
    return RoleRead.model_validate(fresh)


@router.patch(
    "/{role_id}",
    response_model=RoleRead,
    dependencies=[Depends(require_platform_admin)],
)
async def update_role(
    role_id: uuid.UUID,
    payload: RoleUpdate,
    db: AsyncSession = Depends(get_db),
) -> RoleRead:
    role = await role_service.get_role(db, role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rol no encontrado")
    await role_service.update_role(
        db, role, name=payload.name, description=payload.description
    )
    await db.commit()
    fresh = await role_service.get_role(db, role_id)
    assert fresh is not None
    return RoleRead.model_validate(fresh)


@router.put(
    "/{role_id}/permissions",
    response_model=RoleRead,
)
async def set_role_permissions(
    role_id: uuid.UUID,
    payload: RolePermissionsUpdate,
    _requester: Annotated[User, Depends(require_platform_admin)],
    db: AsyncSession = Depends(get_db),
) -> RoleRead:
    role = await role_service.get_role(db, role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rol no encontrado")
    await role_service.set_role_permissions(db, role, permission_codes=payload.permission_codes)
    await db.commit()
    fresh = await role_service.get_role(db, role_id)
    assert fresh is not None
    return RoleRead.model_validate(fresh)


@router.delete(
    "/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_platform_admin)],
)
async def delete_role(role_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    role = await role_service.get_role(db, role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rol no encontrado")
    await role_service.delete_role(db, role)
    await db.commit()
