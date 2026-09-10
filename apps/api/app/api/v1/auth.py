from __future__ import annotations

from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import (
    ACCESS_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
)
from app.core.security import TokenError, decode_token
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse
from app.schemas.fleet import FleetRead
from app.schemas.user import UserRead
from app.services import auth_service, fleet_service, login_rate_limit
from app.services.auth_service import AuthError
from app.services.login_rate_limit import LoginRateLimitError
from app.services.rbac import get_user_permission_codes, user_is_admin

router = APIRouter(prefix="/auth", tags=["auth"])

limiter = Limiter(
    key_func=get_remote_address,
    enabled=settings.environment != "test",
    storage_uri=settings.effective_redis_url,
    in_memory_fallback_enabled=True,
    swallow_errors=True,
    key_prefix="portal-clientes",
)


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    is_prod = settings.environment == "production"
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        access_token,
        max_age=settings.access_token_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=is_prod,
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        refresh_token,
        max_age=settings.refresh_token_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=is_prod,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE_NAME, path="/")
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/")


async def _build_login_response(db: AsyncSession, user: User) -> LoginResponse:
    fleets = await fleet_service.accessible_fleets(db, user)
    return LoginResponse(
        user=UserRead.model_validate(user),
        permissions=sorted(get_user_permission_codes(user)),
        fleets=[FleetRead.model_validate(f) for f in fleets],
        fleet_scope_global=user_is_admin(user),
    )


@router.post("/login", response_model=LoginResponse)
@limiter.limit("300/minute")
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    try:
        await login_rate_limit.assert_login_allowed(payload.email)
    except LoginRateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc

    try:
        user = await auth_service.authenticate(
            db, email=payload.email, password=payload.password
        )
    except AuthError as exc:
        await login_rate_limit.record_login_failure(payload.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    await login_rate_limit.clear_login_failures(payload.email)

    access_token = auth_service.build_access_token(user)
    refresh_token = await auth_service.issue_refresh_token(
        db,
        user,
        user_agent=request.headers.get("user-agent"),
        ip=get_remote_address(request),
    )
    await db.commit()

    _set_auth_cookies(response, access_token, refresh_token)
    return await _build_login_response(db, user)


@router.post("/refresh", response_model=LoginResponse)
@limiter.limit("300/minute")
async def refresh(
    request: Request,
    response: Response,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE_NAME)] = None,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh requerido"
        )
    try:
        user, new_access, new_refresh = await auth_service.rotate_refresh_token(
            db,
            refresh_token,
            user_agent=request.headers.get("user-agent"),
            ip=get_remote_address(request),
        )
    except AuthError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    await db.commit()
    _set_auth_cookies(response, new_access, new_refresh)
    return await _build_login_response(db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE_NAME)] = None,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Cierra la sesión del lado del cliente de forma idempotente.

    - No requiere autenticación: aunque el access token sea inválido o el
      usuario esté desactivado, siempre limpia las cookies y devuelve 204.
    - Si llega un refresh token, intenta revocarlo; un token corrupto,
      revocado, expirado o sin sujeto válido NO impide la limpieza de
      cookies (se ignora la excepción para mantener la idempotencia).
    """
    # Toda la lógica de revocación se ejecuta en un bloque protegido cuyo
    # `finally` garantiza que SIEMPRE limpiemos cookies y devolvamos 204,
    # sin importar si falla decode_token, revoke_refresh_token, commit o
    # rollback. Esto preserva la idempotencia del logout: la respuesta
    # observable (cookies borradas + 204) nunca depende del estado de la DB.
    # El comportamiento tolerante es DELIBERADO y único de este endpoint.
    try:
        if refresh_token:
            try:
                decode_token(refresh_token, expected_type="refresh")
            except TokenError:
                # Refresh inválido: no intentamos revocar, pero tampoco
                # bloqueamos la limpieza de cookies.
                pass
            else:
                await auth_service.revoke_refresh_token(db, refresh_token)
                await db.commit()
    except Exception:
        # Revocación, commit o decodificación inesperada fallaron. El
        # rollback también es best-effort: logout siempre debe poder borrar
        # las cookies locales, incluso si la base no está disponible.
        with suppress(Exception):
            await db.rollback()
    finally:
        _clear_auth_cookies(response)
        response.status_code = status.HTTP_204_NO_CONTENT
    return response
