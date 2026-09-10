"""Tests integrales del flujo de autenticación.

Asumen DB seeded (admin bootstrap presente).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Response
from fastapi.testclient import TestClient

from app.api.v1.auth import logout
from app.core.config import settings
from app.core.deps import ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _login_admin(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": settings.bootstrap_admin_email,
            "password": settings.bootstrap_admin_password,
        },
    )
    assert response.status_code == 200, response.text


def test_login_ok_sets_cookies_and_returns_permissions(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": settings.bootstrap_admin_email,
            "password": settings.bootstrap_admin_password,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["email"].lower() == settings.bootstrap_admin_email.lower()
    assert "users.edit" in body["permissions"]
    assert "roles.edit" in body["permissions"]
    assert ACCESS_COOKIE_NAME in client.cookies
    assert REFRESH_COOKIE_NAME in client.cookies


def test_login_invalid_credentials_returns_401(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": settings.bootstrap_admin_email, "password": "wrong"},
    )
    assert response.status_code == 401


def test_login_identifier_without_email_format_returns_same_401(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "usuario-sin-correo", "password": "wrong"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Credenciales inválidas"


def test_me_requires_auth(client: TestClient) -> None:
    response = client.get("/api/v1/me")
    assert response.status_code == 401


def test_me_returns_current_user_after_login(client: TestClient) -> None:
    _login_admin(client)
    response = client.get("/api/v1/me")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["email"].lower() == settings.bootstrap_admin_email.lower()
    assert "users.edit" in body["permissions"]


def test_refresh_rotates_tokens(client: TestClient) -> None:
    _login_admin(client)
    old_refresh = client.cookies.get(REFRESH_COOKIE_NAME)
    response = client.post("/api/v1/auth/refresh")
    assert response.status_code == 200, response.text
    new_refresh = client.cookies.get(REFRESH_COOKIE_NAME)
    assert new_refresh is not None
    assert new_refresh != old_refresh

    # El refresh viejo ya no debe servir.
    client.cookies.set(REFRESH_COOKIE_NAME, old_refresh)
    response = client.post("/api/v1/auth/refresh")
    assert response.status_code == 401


def test_logout_revokes_session(client: TestClient) -> None:
    _login_admin(client)
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 204

    response = client.get("/api/v1/me")
    assert response.status_code == 401


def test_logout_without_auth_clears_cookies(client: TestClient) -> None:
    """Logout sin autenticación debe ser idempotente: 204 y limpia cookies.

    Para borrar cookies, Starlette emite Set-Cookie con Max-Age=0 (o Expires
    en el pasado) y Path. HttpOnly NO es necesario en el borrado: el atributo
    aplica al momento de setear, no al de expirar.
    """
    # Pre-poblar el cookie jar con tokens ficticios para simular una sesión
    # obsoleta en el navegador.
    client.cookies.set(ACCESS_COOKIE_NAME, "garbage.access")
    client.cookies.set(REFRESH_COOKIE_NAME, "garbage.refresh")

    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 204

    set_cookies = response.headers.get_list("set-cookie")
    for name in (ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME):
        matches = [c for c in set_cookies if c.split("=", 1)[0].strip() == name]
        assert matches, f"Missing Set-Cookie for {name}: {set_cookies}"
        cookie = matches[0]
        assert "Max-Age=0" in cookie, cookie
        assert "Path=/" in cookie, cookie


def test_logout_with_invalid_refresh_still_clears_cookies(client: TestClient) -> None:
    """Un refresh corrupto NO debe impedir la limpieza de cookies (idempotente)."""
    client.cookies.set(ACCESS_COOKIE_NAME, "garbage.access")
    client.cookies.set(REFRESH_COOKIE_NAME, "not-a-valid-jwt-at-all")

    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 204

    set_cookies = response.headers.get_list("set-cookie")
    for name in (ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME):
        matches = [c for c in set_cookies if c.split("=", 1)[0].strip() == name]
        assert matches, f"Missing Set-Cookie for {name}: {set_cookies}"
        assert "Max-Age=0" in matches[0]


def test_logout_idempotent_when_already_logged_out(client: TestClient) -> None:
    """Llamar logout dos veces seguidas debe seguir devolviendo 204 y cookies
    de limpieza, sin requerir autenticación ni reventar en la segunda llamada."""
    client.cookies.set(ACCESS_COOKIE_NAME, "garbage.access")
    client.cookies.set(REFRESH_COOKIE_NAME, "garbage.refresh")

    first = client.post("/api/v1/auth/logout")
    second = client.post("/api/v1/auth/logout")
    assert first.status_code == 204
    assert second.status_code == 204
    for resp in (first, second):
        set_cookies = resp.headers.get_list("set-cookie")
        for name in (ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME):
            matches = [c for c in set_cookies if c.split("=", 1)[0].strip() == name]
            assert matches, f"Missing Set-Cookie for {name} in repeated logout"
            assert "Max-Age=0" in matches[0]


def _assert_clears_both_cookies(response: Response) -> None:
    """Helper: comprueba status 204 y dos Set-Cookie de borrado (Max-Age=0)."""
    assert response.status_code == 204, response.status_code
    set_cookies = response.headers.getlist("set-cookie")
    for name in (ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME):
        matches = [c for c in set_cookies if c.split("=", 1)[0].strip() == name]
        assert matches, f"Missing Set-Cookie for {name}: {set_cookies}"
        assert "Max-Age=0" in matches[0], matches[0]


@pytest.mark.asyncio
async def test_logout_clears_cookies_when_revoke_raises() -> None:
    """Logout directo (sin TestClient, sin DB real): si revoke_refresh_token
    lanza, el endpoint debe seguir devolviendo 204 y limpiando las dos
    cookies, gracias al try/finally."""
    response = Response()
    db = AsyncMock()
    db.rollback = AsyncMock()
    db.commit = AsyncMock()

    with patch(
        "app.api.v1.auth.auth_service.revoke_refresh_token",
        new=AsyncMock(side_effect=RuntimeError("revoke boom")),
    ), patch(
        "app.api.v1.auth.decode_token",
        return_value={"sub": "user-id", "type": "refresh"},
    ):
        result = await logout(
            response=response,
            refresh_token="any.refresh.token",
            db=db,
        )

    _assert_clears_both_cookies(result)
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_logout_clears_cookies_when_rollback_also_raises() -> None:
    """Logout directo: si revoke lanza Y rollback también lanza, el
    endpoint debe seguir devolviendo 204 y limpiando las dos cookies.
    El rollback best-effort debe suprimir su propia excepción."""
    response = Response()
    db = AsyncMock()
    db.rollback = AsyncMock(side_effect=RuntimeError("rollback boom"))
    db.commit = AsyncMock()

    with patch(
        "app.api.v1.auth.auth_service.revoke_refresh_token",
        new=AsyncMock(side_effect=RuntimeError("revoke boom")),
    ), patch(
        "app.api.v1.auth.decode_token",
        return_value={"sub": "user-id", "type": "refresh"},
    ):
        # No debe propagar la excepción del rollback best-effort.
        result = await logout(
            response=response,
            refresh_token="any.refresh.token",
            db=db,
        )

    _assert_clears_both_cookies(result)
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_logout_clears_cookies_when_commit_raises() -> None:
    """Logout directo: si commit lanza, se hace rollback best-effort y se
    sigue devolviendo 204 con cookies borradas."""
    response = Response()
    db = AsyncMock()
    db.rollback = AsyncMock()
    db.commit = AsyncMock(side_effect=RuntimeError("commit boom"))

    with patch(
        "app.api.v1.auth.auth_service.revoke_refresh_token",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.api.v1.auth.decode_token",
        return_value={"sub": "user-id", "type": "refresh"},
    ):
        result = await logout(
            response=response,
            refresh_token="any.refresh.token",
            db=db,
        )

    _assert_clears_both_cookies(result)
    db.rollback.assert_awaited_once()
