"""Tests E2E de flotas: CRUD, asignación a usuarios, scope en /me + header."""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.deps import get_selected_fleets
from app.main import app
from app.models.fleet import Fleet
from app.models.role import Role
from app.models.user import User
from app.services.fleet_service import user_can_access_fleet


@pytest.fixture
def admin_client() -> TestClient:
    c = TestClient(app)
    resp = c.post(
        "/api/v1/auth/login",
        json={
            "email": settings.bootstrap_admin_email,
            "password": settings.bootstrap_admin_password,
        },
    )
    assert resp.status_code == 200, resp.text
    return c


def _new_email() -> str:
    return f"fleetuser-{uuid.uuid4().hex[:8]}@portalclientes.test"


def _new_code() -> str:
    return f"FLEET-{uuid.uuid4().hex[:6].upper()}"


def test_admin_fleet_crud(admin_client: TestClient) -> None:
    code = _new_code()
    # Create
    resp = admin_client.post(
        "/api/v1/fleets", json={"code": code, "name": "Flota Test"}
    )
    assert resp.status_code == 201, resp.text
    fleet_id = resp.json()["id"]

    # Duplicate code -> 409
    resp = admin_client.post("/api/v1/fleets", json={"code": code, "name": "Otra"})
    assert resp.status_code == 409

    # List
    resp = admin_client.get("/api/v1/fleets")
    assert resp.status_code == 200
    assert any(f["id"] == fleet_id for f in resp.json())

    # Update
    resp = admin_client.patch(f"/api/v1/fleets/{fleet_id}", json={"name": "Renombrada"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Renombrada"
    # El flag de Análisis de Ralentí nace apagado y se enciende por PATCH.
    assert resp.json()["ralenti_analysis_enabled"] is False
    resp = admin_client.patch(
        f"/api/v1/fleets/{fleet_id}", json={"ralenti_analysis_enabled": True}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ralenti_analysis_enabled"] is True
    # Y /me lo publica en las flotas del alcance: es lo que gobierna la pestaña.
    me = admin_client.get("/api/v1/me").json()
    assert any(f["id"] == fleet_id and f["ralenti_analysis_enabled"] for f in me["fleets"])

    # Deactivate
    resp = admin_client.delete(f"/api/v1/fleets/{fleet_id}")
    assert resp.status_code == 204
    resp = admin_client.get("/api/v1/fleets", params={"only_active": True})
    assert all(f["id"] != fleet_id for f in resp.json())


def test_admin_me_has_global_fleet_scope(admin_client: TestClient) -> None:
    resp = admin_client.get("/api/v1/me")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fleet_scope_global"] is True
    # admin ve todas las flotas activas (al menos las sembradas).
    assert isinstance(body["fleets"], list)


def test_assigned_user_me_scoped_to_fleet(admin_client: TestClient) -> None:
    # Crea una flota y un usuario con esa flota asignada.
    code = _new_code()
    resp = admin_client.post("/api/v1/fleets", json={"code": code, "name": "Flota Cliente X"})
    fleet_id = resp.json()["id"]

    email = _new_email()
    password = "FleetPass123!"
    resp = admin_client.post(
        "/api/v1/users",
        json={
            "email": email,
            "password": password,
            "full_name": "Cliente Flota",
            "role_codes": ["admin_flota_cliente"],
            "fleet_ids": [fleet_id],
        },
    )
    assert resp.status_code == 201, resp.text
    assert [f["id"] for f in resp.json()["fleets"]] == [fleet_id]

    # Login como ese usuario: /me trae solo su flota, sin scope global.
    user_client = TestClient(app)
    resp = user_client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    me = user_client.get("/api/v1/me").json()
    assert me["fleet_scope_global"] is False
    assert [f["id"] for f in me["fleets"]] == [fleet_id]

    # El catálogo de flotas respeta el alcance asignado y las operaciones
    # globales no se pueden delegar mediante flotas.edit.
    fleets = user_client.get("/api/v1/fleets")
    assert fleets.status_code == 200, fleets.text
    assert [fleet["id"] for fleet in fleets.json()] == [fleet_id]
    resp = user_client.post(
        "/api/v1/fleets", json={"code": _new_code(), "name": "No autorizada"}
    )
    assert resp.status_code == 403, resp.text


def test_viewer_cannot_create_fleet(admin_client: TestClient) -> None:
    email = _new_email()
    password = "ViewerFleet123!"
    resp = admin_client.post(
        "/api/v1/users",
        json={
            "email": email,
            "password": password,
            "full_name": "Viewer Fleet",
            "role_codes": ["viewer"],
        },
    )
    assert resp.status_code == 201, resp.text

    viewer = TestClient(app)
    viewer.post("/api/v1/auth/login", json={"email": email, "password": password})
    # viewer tiene flotas.view -> puede listar
    assert viewer.get("/api/v1/fleets").status_code == 200
    # pero no editar
    resp = viewer.post("/api/v1/fleets", json={"code": _new_code(), "name": "Nope"})
    assert resp.status_code == 403


# --- Dependency de scoping (unit, sin DB) ---------------------------------


def _fleet(fid: uuid.UUID) -> Fleet:
    f = Fleet(code="C", name="N", is_active=True)
    f.id = fid
    return f


def _user(*, admin: bool, fleets: list[Fleet]) -> User:
    u = User(email="x@y.z", password_hash="x", full_name="X", is_active=True)
    u.roles = [Role(code="admin", name="Admin")] if admin else []
    u.fleets = fleets
    return u


def test_user_can_access_fleet_rules() -> None:
    fid = uuid.uuid4()
    other = uuid.uuid4()
    assigned = _user(admin=False, fleets=[_fleet(fid)])
    assert user_can_access_fleet(assigned, fid) is True
    assert user_can_access_fleet(assigned, other) is False
    # admin accede a cualquiera.
    assert user_can_access_fleet(_user(admin=True, fleets=[]), other) is True


@pytest.mark.asyncio
async def test_get_selected_fleet_validation() -> None:
    fid = uuid.uuid4()
    user = _user(admin=False, fleets=[_fleet(fid)])

    # Sin header -> "Todas" (vacío).
    assert await get_selected_fleets(user, None) == []
    assert await get_selected_fleets(user, "") == []
    # Header con flota propia -> devuelve lista con el uuid.
    assert await get_selected_fleets(user, str(fid)) == [fid]
    # Header inválido -> 400.
    with pytest.raises(HTTPException) as exc400:
        await get_selected_fleets(user, "no-uuid")
    assert exc400.value.status_code == 400
    # Flota sin acceso -> 403.
    with pytest.raises(HTTPException) as exc403:
        await get_selected_fleets(user, str(uuid.uuid4()))
    assert exc403.value.status_code == 403


def test_non_admin_cannot_access_unassigned_fleet(admin_client: TestClient) -> None:
    """Un no-admin con flotas.view no puede inspeccionar una flota ajena (404)."""
    resp = admin_client.post(
        "/api/v1/fleets", json={"code": _new_code(), "name": "Ajena"}
    )
    assert resp.status_code == 201, resp.text
    foreign_fleet_id = resp.json()["id"]

    role_code = f"flotaview_{uuid.uuid4().hex[:8]}"
    resp = admin_client.post(
        "/api/v1/roles",
        json={
            "code": role_code,
            "name": "Flota View",
            "description": None,
            "permission_codes": ["flotas.view"],
        },
    )
    assert resp.status_code == 201, resp.text

    email = _new_email()
    password = "FleetPass123!"
    resp = admin_client.post(
        "/api/v1/users",
        json={
            "email": email,
            "password": password,
            "full_name": "Sin Flota",
            "role_codes": [role_code],
            "fleet_ids": [],
        },
    )
    assert resp.status_code == 201, resp.text

    client = TestClient(app)
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        ).status_code
        == 200
    )

    assert (
        client.get(f"/api/v1/fleets/{foreign_fleet_id}/databases").status_code == 404
    )
    assert (
        client.get(f"/api/v1/fleets/{foreign_fleet_id}/vehicles").status_code == 404
    )
