from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_permission
from app.core.rbac_constants import ADMIN_ROLE_CODE
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import PaginatedUsers, UserCreate, UserRead, UserUpdate
from app.services import user_service
from app.services.rbac import user_is_admin

router = APIRouter(prefix="/users", tags=["users"])

_LAST_ADMIN_DETAIL = "No se puede dejar el sistema sin administradores activos"


def _guard_admin_role_grant(requester: User, role_codes: list[str] | None) -> None:
    """Solo un admin puede otorgar el rol admin (bypass total de permisos)."""
    if role_codes and ADMIN_ROLE_CODE in role_codes and not user_is_admin(requester):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo un administrador puede asignar el rol admin",
        )


@router.get(
    "",
    response_model=PaginatedUsers,
    dependencies=[Depends(require_permission("users.view"))],
)
async def list_users(
    requester: CurrentUser,
    search: str | None = Query(default=None, max_length=120),
    role: str | None = Query(default=None, max_length=64),
    only_active: bool | None = Query(default=None),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedUsers:
    fleet_ids = (
        None
        if user_is_admin(requester)
        else [fleet.id for fleet in requester.fleets if fleet.is_active]
    )
    items, total = await user_service.list_users(
        db,
        search=search,
        role_code=role,
        only_active=only_active,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
        fleet_ids=fleet_ids,
    )
    return PaginatedUsers(
        items=[UserRead.model_validate(u) for u in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    payload: UserCreate,
    requester: Annotated[User, Depends(require_permission("users.edit"))],
    db: AsyncSession = Depends(get_db),
) -> UserRead:
    _guard_admin_role_grant(requester, payload.role_codes)
    await user_service.validate_user_delegation(
        db,
        requester,
        role_codes=payload.role_codes,
        fleet_ids=payload.fleet_ids,
    )
    existing = await user_service.get_user_by_email(db, payload.email)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email ya registrado",
        )
    user = await user_service.create_user(
        db,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        role_codes=payload.role_codes,
        fleet_ids=payload.fleet_ids,
        is_active=payload.is_active,
    )
    await db.commit()
    fresh = await user_service.get_user_by_id(db, user.id)
    assert fresh is not None
    return UserRead.model_validate(fresh)


@router.patch(
    "/{user_id}",
    response_model=UserRead,
)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    requester: Annotated[User, Depends(require_permission("users.edit"))],
    db: AsyncSession = Depends(get_db),
) -> UserRead:
    user = await user_service.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")
    _guard_admin_role_grant(requester, payload.role_codes)
    await user_service.validate_user_delegation(
        db,
        requester,
        role_codes=payload.role_codes,
        fleet_ids=payload.fleet_ids,
        target=user,
    )

    target_is_admin = user_is_admin(user)
    removes_admin_role = (
        payload.role_codes is not None and ADMIN_ROLE_CODE not in payload.role_codes
    )
    deactivates = payload.is_active is False
    archives = payload.is_archived is True
    if (
        target_is_admin
        and (removes_admin_role or deactivates or archives)
        and await user_service.count_active_admins(db) <= 1
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_LAST_ADMIN_DETAIL)

    updated = await user_service.update_user(
        db,
        user,
        full_name=payload.full_name,
        is_active=payload.is_active,
        role_codes=payload.role_codes,
        fleet_ids=payload.fleet_ids,
        password=payload.password,
        is_archived=payload.is_archived,
    )
    await db.commit()
    fresh = await user_service.get_user_by_id(db, updated.id)
    assert fresh is not None
    return UserRead.model_validate(fresh)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def deactivate_user(
    user_id: uuid.UUID,
    requester: Annotated[User, Depends(require_permission("users.edit"))],
    db: AsyncSession = Depends(get_db),
) -> None:
    user = await user_service.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")
    await user_service.validate_user_delegation(
        db,
        requester,
        role_codes=None,
        fleet_ids=None,
        target=user,
    )
    if user_is_admin(user) and await user_service.count_active_admins(db) <= 1:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_LAST_ADMIN_DETAIL)
    await user_service.deactivate_user(db, user)
    await db.commit()
