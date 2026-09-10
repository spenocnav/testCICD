"""Tests E2E sobre endpoints CRUD de usuarios y RBAC en HTTP."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


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
    return f"user-{uuid.uuid4().hex[:8]}@portalclientes.test"


def test_admin_can_create_list_update_deactivate_user(admin_client: TestClient) -> None:
    email = _new_email()
    # Create
    resp = admin_client.post(
        "/api/v1/users",
        json={
            "email": email,
            "password": "TestPass123!",
            "full_name": "Test User",
            "role_codes": ["viewer"],
        },
    )
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["id"]

    # List
    resp = admin_client.get("/api/v1/users", params={"search": email.split("@")[0]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert any(u["email"].lower() == email.lower() for u in body["items"])

    # Update
    resp = admin_client.patch(
        f"/api/v1/users/{user_id}",
        json={"full_name": "Renamed User", "role_codes": ["conductor"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["full_name"] == "Renamed User"
    assert any(r["code"] == "conductor" for r in resp.json()["roles"])

    # Soft delete
    resp = admin_client.delete(f"/api/v1/users/{user_id}")
    assert resp.status_code == 204

    # Confirma estado inactivo vía list con only_active=False
    resp = admin_client.get(
        "/api/v1/users",
        params={"search": email.split("@")[0], "only_active": False},
    )
    body = resp.json()
    target = next((u for u in body["items"] if u["id"] == user_id), None)
    assert target is not None
    assert target["is_active"] is False


def test_duplicate_email_returns_409(admin_client: TestClient) -> None:
    email = _new_email()
    payload = {
        "email": email,
        "password": "TestPass123!",
        "full_name": "Dup User",
        "role_codes": ["viewer"],
    }
    r1 = admin_client.post("/api/v1/users", json=payload)
    assert r1.status_code == 201, r1.text
    r2 = admin_client.post("/api/v1/users", json=payload)
    assert r2.status_code == 409


def test_viewer_cannot_create_users(admin_client: TestClient) -> None:
    # Admin crea un viewer ad-hoc
    email = _new_email()
    password = "ViewerPass123!"
    resp = admin_client.post(
        "/api/v1/users",
        json={
            "email": email,
            "password": password,
            "full_name": "Viewer Live",
            "role_codes": ["viewer"],
        },
    )
    assert resp.status_code == 201, resp.text

    viewer_client = TestClient(app)
    resp = viewer_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200, resp.text

    # Viewer puede listar
    resp = viewer_client.get("/api/v1/users")
    assert resp.status_code == 200

    # Pero NO puede crear
    resp = viewer_client.post(
        "/api/v1/users",
        json={
            "email": _new_email(),
            "password": "NopePass123!",
            "full_name": "Forbidden",
            "role_codes": ["viewer"],
        },
    )
    assert resp.status_code == 403

    # Ni eliminar
    resp = viewer_client.delete(f"/api/v1/users/{uuid.uuid4()}")
    assert resp.status_code == 403


def test_roles_and_permissions_endpoints_require_roles_read(admin_client: TestClient) -> None:
    resp = admin_client.get("/api/v1/roles")
    assert resp.status_code == 200
    role_codes = {r["code"] for r in resp.json()}
    assert {"admin", "viewer", "conductor"} <= role_codes

    resp = admin_client.get("/api/v1/permissions")
    assert resp.status_code == 200
    perm_codes = {p["code"] for p in resp.json()}
    assert "users.edit" in perm_codes


def _make_user_manager_client(
    admin_client: TestClient,
) -> tuple[TestClient, str, str]:
    """Crea un rol delegado con gestión de usuarios y roles, y devuelve su sesión."""
    role_code = f"umgr_{uuid.uuid4().hex[:8]}"
    resp = admin_client.post(
        "/api/v1/roles",
        json={
            "code": role_code,
            "name": "User Manager",
            "description": None,
            "permission_codes": [
                "users.view",
                "users.edit",
                "roles.view",
                "roles.edit",
                "flotas.edit",
                "mantenimiento.edit",
                "reportes.edit",
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    fleet_resp = admin_client.post(
        "/api/v1/fleets",
        json={
            "code": f"UMGR-{uuid.uuid4().hex[:8].upper()}",
            "name": "Flota del gestor",
        },
    )
    assert fleet_resp.status_code == 201, fleet_resp.text
    fleet_id = fleet_resp.json()["id"]

    email = _new_email()
    password = "MgrPass123!"
    resp = admin_client.post(
        "/api/v1/users",
        json={
            "email": email,
            "password": password,
            "full_name": "User Manager",
            "role_codes": [role_code],
            "fleet_ids": [fleet_id],
        },
    )
    assert resp.status_code == 201, resp.text

    client = TestClient(app)
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return client, role_code, fleet_id


def test_non_admin_with_users_edit_cannot_grant_admin(admin_client: TestClient) -> None:
    mgr, manager_role_code, manager_fleet_id = _make_user_manager_client(admin_client)

    # No puede escalar creando un admin.
    resp = mgr.post(
        "/api/v1/users",
        json={
            "email": _new_email(),
            "password": "TestPass123!",
            "full_name": "Escalado",
            "role_codes": ["admin"],
        },
    )
    assert resp.status_code == 403, resp.text

    # Las sincronizaciones manuales tienen efectos globales y tampoco se
    # delegan por permisos de tenant.
    assert mgr.post("/api/v1/fleets/sync").status_code == 403
    assert mgr.post("/api/v1/mantenimiento/sync").status_code == 403
    assert mgr.post("/api/v1/data-quality/reportes/trigger").status_code == 403

    # Sin una flota asignada tampoco puede crear cuentas huérfanas fuera de
    # cualquier alcance administrable.
    resp = mgr.post(
        "/api/v1/users",
        json={
            "email": _new_email(),
            "password": "TestPass123!",
            "full_name": "Sin alcance",
            "role_codes": [manager_role_code],
            "fleet_ids": [],
        },
    )
    assert resp.status_code == 403, resp.text

    # Aunque tenga roles.edit, la matriz de permisos sigue siendo exclusiva
    # del administrador de plataforma.
    resp = mgr.post(
        "/api/v1/roles",
        json={
            "code": f"delegated_{uuid.uuid4().hex[:8]}",
            "name": "Rol no autorizado",
            "description": None,
            "permission_codes": ["users.view"],
        },
    )
    assert resp.status_code == 403, resp.text

    # Solo puede delegar el rol que ya posee; no puede regalar permisos ajenos.
    resp = mgr.post(
        "/api/v1/users",
        json={
            "email": _new_email(),
            "password": "TestPass123!",
            "full_name": "Normal",
            "role_codes": [manager_role_code],
            "fleet_ids": [manager_fleet_id],
        },
    )
    assert resp.status_code == 201, resp.text

    # Un rol operativo con permisos que el gestor no posee queda bloqueado.
    resp = mgr.post(
        "/api/v1/users",
        json={
            "email": _new_email(),
            "password": "TestPass123!",
            "full_name": "Fuera de alcance",
            "role_codes": ["viewer"],
        },
    )
    assert resp.status_code == 403, resp.text


def test_non_admin_cannot_promote_existing_user_to_admin(admin_client: TestClient) -> None:
    mgr, _, _ = _make_user_manager_client(admin_client)
    resp = admin_client.post(
        "/api/v1/users",
        json={
            "email": _new_email(),
            "password": "TestPass123!",
            "full_name": "Target",
            "role_codes": ["viewer"],
        },
    )
    assert resp.status_code == 201, resp.text
    target_id = resp.json()["id"]
    # El gestor intenta promoverlo a admin vía PATCH.
    resp = mgr.patch(f"/api/v1/users/{target_id}", json={"role_codes": ["admin"]})
    assert resp.status_code == 403, resp.text


def test_last_admin_cannot_be_deactivated_or_demoted(admin_client: TestClient) -> None:
    me = admin_client.get("/api/v1/me")
    assert me.status_code == 200, me.text
    admin_id = me.json()["user"]["id"]

    # No se puede desactivar al último admin.
    resp = admin_client.delete(f"/api/v1/users/{admin_id}")
    assert resp.status_code == 409, resp.text

    # No se le puede quitar el rol admin.
    resp = admin_client.patch(f"/api/v1/users/{admin_id}", json={"role_codes": ["viewer"]})
    assert resp.status_code == 409, resp.text
