"""Dependencias FastAPI: usuario actual + chequeo de permisos."""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import TokenError, decode_token
from app.db.session import get_db
from app.models.user import User
from app.services.fleet_service import user_can_access_fleet
from app.services.rbac import user_has_permission, user_is_admin
from app.services.user_service import get_user_by_id


async def get_report_fleet_ids(
    user: CurrentUser,
    selected_fleets: SelectedFleets,
    db: AsyncSession = Depends(get_db),
) -> list[uuid.UUID]:
    """Filtro de flotas para endpoints de reportes, interseccionado con el
    acceso del usuario. Una selección vacía significa "Todas las flotas del
    filtro", nunca un alcance global sin scoping: también para admin se
    devuelven únicamente las flotas activas accesibles. Así, vehículos que
    CloudFleet conoce pero que aún no pertenecen al maestro/flota del portal
    no pueden entrar en reportes de mantenimiento.
    """
    from app.services import fleet_service

    accessible = await fleet_service.accessible_fleets(db, user)
    accessible_ids = {f.id for f in accessible}
    if selected_fleets:
        return [fid for fid in selected_fleets if fid in accessible_ids]
    return [fleet.id for fleet in accessible]


ReportFleetIds = Annotated[list[uuid.UUID], Depends(get_report_fleet_ids)]

ACCESS_COOKIE_NAME = "access_token"
REFRESH_COOKIE_NAME = "refresh_token"


async def get_current_user(
    request: Request,
    access_token: Annotated[str | None, Cookie(alias=ACCESS_COOKIE_NAME)] = None,
    db: AsyncSession = Depends(get_db),
) -> User:
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión requerida",
        )

    try:
        payload = decode_token(access_token, expected_type="access")
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token inválido: {exc}",
        ) from exc

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token sin sujeto válido",
        ) from exc

    user = await get_user_by_id(db, user_id)
    if not user or not user.is_active or user.is_archived:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado o inactivo",
        )
    # El scope se comparte con el middleware (BaseHTTPMiddleware reutiliza el
    # mismo dict), así que el registro de uso puede leer la identidad después
    # de call_next sin depender de contextvars, que no propagan hacia arriba.
    request.state.usage_user_id = user.id
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]

FLEET_HEADER_NAME = "X-Fleet-Id"


async def get_selected_fleets(
    user: CurrentUser,
    x_fleet_id: Annotated[str | None, Header(alias=FLEET_HEADER_NAME)] = None,
) -> list[uuid.UUID]:
    """Flotas seleccionadas en el filtro (header `X-Fleet-Id`).

    `None` o vacío representa "Todas"; `get_report_fleet_ids` lo resuelve a
    las flotas activas accesibles. Soporta múltiples UUIDs separados por coma.
    Cada flota se valida contra el acceso del usuario.
    """
    if not x_fleet_id:
        return []
    ids = [raw.strip() for raw in x_fleet_id.split(",") if raw.strip()]
    result: list[uuid.UUID] = []
    for raw in ids:
        try:
            fleet_id = uuid.UUID(raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{FLEET_HEADER_NAME} inválido: {raw}",
            ) from exc
        if not user_can_access_fleet(user, fleet_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Sin acceso a la flota {fleet_id}",
            )
        result.append(fleet_id)
    return result


SelectedFleets = Annotated[list[uuid.UUID], Depends(get_selected_fleets)]


def require_permission(permission_code: str) -> Callable[[User], Awaitable[User]]:
    """Factory de dependencia que exige un permiso específico."""

    async def _checker(user: CurrentUser) -> User:
        if not user_has_permission(user, permission_code):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Falta permiso: {permission_code}",
            )
        return user

    return _checker


def require_any_permission(*permission_codes: str) -> Callable[[User], Awaitable[User]]:
    """Exige al menos uno de varios permisos, manteniendo el bypass admin."""

    async def _checker(user: CurrentUser) -> User:
        if not any(user_has_permission(user, code) for code in permission_codes):
            joined = ", ".join(permission_codes)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Falta uno de los permisos: {joined}",
            )
        return user

    return _checker


async def require_platform_admin(user: CurrentUser) -> User:
    """Exige el rol global `admin` para operaciones sin alcance de tenant."""
    if not user_is_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo un administrador puede realizar esta operación global",
        )
    return user


async def require_fleet_access(fleet_id: uuid.UUID, user: CurrentUser) -> uuid.UUID:
    if not user_can_access_fleet(user, fleet_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flota no encontrada")
    return fleet_id


SYNC_INGEST_HEADER = "X-Sync-Ingest-Key"


async def require_sync_ingest_key(
    x_sync_ingest_key: Annotated[str | None, Header(alias=SYNC_INGEST_HEADER)] = None,
) -> None:
    """Auth máquina-a-máquina para la ingesta de auditoría de syncs externos.

    Compara el header contra `settings.sync_ingest_api_key` en tiempo constante.
    503 si no está configurado (endpoint deshabilitado), 401 si no coincide."""
    expected = settings.sync_ingest_api_key
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ingesta de sync no configurada (SYNC_INGEST_API_KEY ausente).",
        )
    if not x_sync_ingest_key or not secrets.compare_digest(x_sync_ingest_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"{SYNC_INGEST_HEADER} inválido o ausente.",
        )
