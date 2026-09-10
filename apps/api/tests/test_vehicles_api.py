"""Tests E2E de vehículos por flota: listar y activar/desactivar + RBAC."""

from __future__ import annotations

import asyncio
import uuid

from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.master_data import GeotabDatabase, Vehicle


def _admin_client() -> TestClient:
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


def _seed_vehicle(fleet_id: str, *, is_active: bool = True) -> str:
    """Inserta una base geotab + un vehículo bajo la flota; devuelve vehicle_id."""

    async def _do() -> str:
        async with AsyncSessionLocal() as db:
            db_name = f"db-{uuid.uuid4().hex[:8]}"
            gd = GeotabDatabase(
                fleet_id=uuid.UUID(fleet_id),
                database_name=db_name,
                database_key=db_name.lower(),
                is_active=True,
            )
            db.add(gd)
            await db.flush()
            v = Vehicle(
                fleet_id=uuid.UUID(fleet_id),
                geotab_database_id=gd.id,
                geotab_device_id=f"dev-{uuid.uuid4().hex[:6]}",
                plate=f"TST{uuid.uuid4().hex[:4].upper()}",
                motor_type=None,
                is_active=is_active,
            )
            db.add(v)
            await db.flush()
            await db.commit()
            return str(v.id)

    return asyncio.run(_do())


def test_list_and_toggle_vehicle() -> None:
    admin = _admin_client()
    code = f"FLEET-{uuid.uuid4().hex[:6].upper()}"
    fleet_id = admin.post("/api/v1/fleets", json={"code": code, "name": "Flota Veh"}).json()["id"]
    vehicle_id = _seed_vehicle(fleet_id)

    # Listar: el vehículo aparece con database_name resuelto.
    resp = admin.get(f"/api/v1/fleets/{fleet_id}/vehicles")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    row = next(v for v in rows if v["id"] == vehicle_id)
    assert row["is_active"] is True
    assert row["database_name"] is not None

    # Desactivar.
    resp = admin.patch(
        f"/api/v1/fleets/{fleet_id}/vehicles/{vehicle_id}", json={"is_active": False}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False

    # Reactivar.
    resp = admin.patch(f"/api/v1/fleets/{fleet_id}/vehicles/{vehicle_id}", json={"is_active": True})
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True


def test_list_accessible_vehicles_for_admin() -> None:
    admin = _admin_client()
    code = f"FLEET-{uuid.uuid4().hex[:6].upper()}"
    fleet_id = admin.post("/api/v1/fleets", json={"code": code, "name": "Flota Veh"}).json()["id"]
    vehicle_id = _seed_vehicle(fleet_id)

    resp = admin.get("/api/v1/vehicles")
    assert resp.status_code == 200, resp.text
    assert any(v["id"] == vehicle_id for v in resp.json())


def test_list_accessible_vehicles_includes_inactive_by_default() -> None:
    admin = _admin_client()
    code = f"FLEET-{uuid.uuid4().hex[:6].upper()}"
    fleet_id = admin.post(
        "/api/v1/fleets", json={"code": code, "name": "Flota Veh", "is_active": False}
    ).json()["id"]
    vehicle_id = _seed_vehicle(fleet_id, is_active=False)

    resp = admin.get("/api/v1/vehicles")
    assert resp.status_code == 200, resp.text
    assert any(v["id"] == vehicle_id for v in resp.json())


def test_toggle_vehicle_not_in_fleet_404() -> None:
    admin = _admin_client()
    code = f"FLEET-{uuid.uuid4().hex[:6].upper()}"
    fleet_id = admin.post("/api/v1/fleets", json={"code": code, "name": "Flota Vacia"}).json()["id"]
    resp = admin.patch(
        f"/api/v1/fleets/{fleet_id}/vehicles/{uuid.uuid4()}", json={"is_active": False}
    )
    assert resp.status_code == 404


def test_viewer_can_list_but_not_toggle() -> None:
    admin = _admin_client()
    code = f"FLEET-{uuid.uuid4().hex[:6].upper()}"
    fleet_id = admin.post("/api/v1/fleets", json={"code": code, "name": "Flota RBAC"}).json()["id"]
    vehicle_id = _seed_vehicle(fleet_id)

    email = f"vehviewer-{uuid.uuid4().hex[:8]}@portalclientes.test"
    password = "ViewerVeh123!"
    admin.post(
        "/api/v1/users",
        json={
            "email": email,
            "password": password,
            "full_name": "Viewer Veh",
            "role_codes": ["viewer"],
            "fleet_ids": [fleet_id],
        },
    )
    viewer = TestClient(app)
    viewer.post("/api/v1/auth/login", json={"email": email, "password": password})

    assert viewer.get(f"/api/v1/fleets/{fleet_id}/vehicles").status_code == 200
    resp = viewer.patch(
        f"/api/v1/fleets/{fleet_id}/vehicles/{vehicle_id}", json={"is_active": False}
    )
    assert resp.status_code == 403


def test_accessible_vehicles_requires_reportes_permission() -> None:
    """El catálogo exige reportes.view o novedades.view.

    El rol de sistema `viewer` ya trae `reportes.view` por migración, así que
    la negativa se comprueba con un rol propio sin permisos.
    """
    admin = _admin_client()
    role_code = f"sin_permisos_{uuid.uuid4().hex[:8]}"
    resp = admin.post(
        "/api/v1/roles",
        json={"code": role_code, "name": "Sin permisos", "permission_codes": []},
    )
    assert resp.status_code == 201, resp.text
    email = f"veh-noreport-{uuid.uuid4().hex[:8]}@portalclientes.test"
    password = "ViewerVeh123!"
    admin.post(
        "/api/v1/users",
        json={
            "email": email,
            "password": password,
            "full_name": "No Report",
            "role_codes": [role_code],
        },
    )
    viewer = TestClient(app)
    viewer.post("/api/v1/auth/login", json={"email": email, "password": password})

    resp = viewer.get("/api/v1/vehicles")
    assert resp.status_code == 403


def test_accessible_vehicles_respects_x_fleet_id_header() -> None:
    """Con header `X-Fleet-Id` solo se devuelven vehículos de las flotas
    seleccionadas. Sin header se devuelven todas las accesibles (admin = todas)."""
    admin = _admin_client()
    fleet_a_code = f"FLEET-{uuid.uuid4().hex[:6].upper()}"
    fleet_b_code = f"FLEET-{uuid.uuid4().hex[:6].upper()}"
    fleet_a_id = admin.post(
        "/api/v1/fleets", json={"code": fleet_a_code, "name": "Flota A"}
    ).json()["id"]
    fleet_b_id = admin.post(
        "/api/v1/fleets", json={"code": fleet_b_code, "name": "Flota B"}
    ).json()["id"]
    veh_a = _seed_vehicle(fleet_a_id)
    veh_b = _seed_vehicle(fleet_b_id)

    # Sin header → ambos vehículos.
    resp = admin.get("/api/v1/vehicles")
    assert resp.status_code == 200
    ids = {v["id"] for v in resp.json()}
    assert {veh_a, veh_b} <= ids

    # Header con flota A → solo veh_a.
    resp_a = admin.get("/api/v1/vehicles", headers={"X-Fleet-Id": fleet_a_id})
    assert resp_a.status_code == 200
    ids_a = {v["id"] for v in resp_a.json()}
    assert veh_a in ids_a
    assert veh_b not in ids_a

    # Header con flota B → solo veh_b.
    resp_b = admin.get("/api/v1/vehicles", headers={"X-Fleet-Id": fleet_b_id})
    assert resp_b.status_code == 200
    ids_b = {v["id"] for v in resp_b.json()}
    assert veh_b in ids_b
    assert veh_a not in ids_b


def test_accessible_vehicles_rejects_invalid_fleet_header() -> None:
    """UUID con formato inválido en `X-Fleet-Id` devuelve 400."""
    admin = _admin_client()
    fleet_id = admin.post(
        "/api/v1/fleets",
        json={"code": f"FLEET-{uuid.uuid4().hex[:6].upper()}", "name": "Flota X"},
    ).json()["id"]
    _seed_vehicle(fleet_id)

    resp = admin.get("/api/v1/vehicles", headers={"X-Fleet-Id": "no-es-uuid"})
    assert resp.status_code == 400
