"""El rol de sistema `reportante_novedades` alcanza para operar novedades y nada más.

Fija el contrato del rol que se le entrega a un cliente (conductores):

- existe tras las migraciones, es de sistema y tiene exactamente
  `novedades.view` + `novedades.edit`;
- con una flota asignada puede listar placas de esa flota, crear una novedad
  (CloudFleet simulado) y leerla;
- no ve novedades de otra flota (404, no 403), no puede borrar (403), y no
  entra a mantenimiento, usuarios ni flotas (403).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import novedades as novedades_router
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.master_data import GeotabDatabase, Vehicle
from app.models.novedad import Novedad
from app.services.cloudfleet_service import CloudfleetCreateResult

ROLE_CODE = "reportante_novedades"


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


def _create_fleet(admin: TestClient) -> str:
    resp = admin.post(
        "/api/v1/fleets",
        json={"code": f"REP-{uuid.uuid4().hex[:8].upper()}", "name": "Flota reportante"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _seed_vehicle(fleet_id: str) -> tuple[str, str]:
    async def _do() -> tuple[str, str]:
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
            plate = f"REP{uuid.uuid4().hex[:4].upper()}"
            v = Vehicle(
                fleet_id=uuid.UUID(fleet_id),
                geotab_database_id=gd.id,
                geotab_device_id=f"dev-{uuid.uuid4().hex[:6]}",
                plate=plate,
                motor_type=None,
                is_active=True,
            )
            db.add(v)
            await db.flush()
            await db.commit()
            return str(v.id), plate

    return asyncio.run(_do())


def _seed_novedad(fleet_id: str) -> str:
    vehicle_id, plate = _seed_vehicle(fleet_id)

    async def _do() -> str:
        async with AsyncSessionLocal() as db:
            novedad = Novedad(
                vehicle_id=uuid.UUID(vehicle_id),
                fleet_id=uuid.UUID(fleet_id),
                vehicle_code=plate,
                reported_at=datetime.now(UTC),
                reported_by_id=23,
                priority="low",
                comment="ajena",
                send_mail=False,
                cloudfleet_status="sent",
                cloudfleet_issue_number=555_001,
            )
            db.add(novedad)
            await db.commit()
            return str(novedad.id)

    return asyncio.run(_do())


def _reportante_client(admin: TestClient, fleet_id: str) -> TestClient:
    email = f"rep-{uuid.uuid4().hex[:8]}@portalclientes.test"
    password = "RepPass123!"
    resp = admin.post(
        "/api/v1/users",
        json={
            "email": email,
            "password": password,
            "full_name": "Conductor Reportante",
            "role_codes": [ROLE_CODE],
            "fleet_ids": [fleet_id],
        },
    )
    assert resp.status_code == 201, resp.text
    client = TestClient(app)
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return client


def test_role_exists_as_system_role_with_only_novedades_permissions() -> None:
    admin = _admin_client()
    roles = admin.get("/api/v1/roles").json()
    role = next((r for r in roles if r["code"] == ROLE_CODE), None)
    assert role is not None, "la migración v8w9x0y10048 debe crear el rol"
    assert role["is_system"] is True
    codes = sorted(p["code"] if isinstance(p, dict) else p for p in role["permissions"])
    assert codes == ["novedades.edit", "novedades.view"]


def test_reportante_can_list_vehicles_create_and_read_novedades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _admin_client()
    fleet_id = _create_fleet(admin)
    other_fleet_id = _create_fleet(admin)
    vehicle_id, plate = _seed_vehicle(fleet_id)
    _seed_vehicle(other_fleet_id)
    client = _reportante_client(admin, fleet_id)

    me = client.get("/api/v1/me").json()
    assert sorted(me["permissions"]) == ["novedades.edit", "novedades.view"]

    # Catálogo de placas: sólo su flota.
    vehicles = client.get("/api/v1/vehicles")
    assert vehicles.status_code == 200, vehicles.text
    plates = {v["plate"] for v in vehicles.json()}
    assert plate in plates
    assert all(v["fleet_id"] == fleet_id for v in vehicles.json())

    sent: list[dict] = []

    async def fake_create_issue(payload: dict, **kwargs: object) -> CloudfleetCreateResult:
        sent.append(payload)
        return CloudfleetCreateResult(issue_number=777, response={"number": 777})

    monkeypatch.setattr(novedades_router.cloudfleet_service, "create_issue", fake_create_issue)
    monkeypatch.setattr(novedades_router.settings, "cloudfleet_id_reportedby", 23)

    resp = client.post(
        "/api/v1/novedades",
        data={
            "vehicle_id": vehicle_id,
            "reported_at": datetime.now(UTC).isoformat(),
            "priority": "medium",
            "comment": "Ruido en frenos",
            "send_mail": "false",
        },
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["cloudfleet_status"] == "sent"
    assert created["cloudfleet_issue_number"] == 777
    assert created["external_is_done"] is None
    assert sent and sent[0]["vehicleCode"] == plate
    assert "Reportado por Conductor Reportante" in sent[0]["comment"]
    # El detalle técnico de CloudFleet es sólo para admin.
    assert created["cloudfleet_response"] is None

    listing = client.get("/api/v1/novedades")
    assert listing.status_code == 200
    assert {item["id"] for item in listing.json()["items"]} == {created["id"]}

    detail = client.get(f"/api/v1/novedades/{created['id']}")
    assert detail.status_code == 200


def test_reportante_is_scoped_and_cannot_reach_other_modules() -> None:
    admin = _admin_client()
    fleet_id = _create_fleet(admin)
    other_fleet_id = _create_fleet(admin)
    foreign_novedad_id = _seed_novedad(other_fleet_id)
    own_novedad_id = _seed_novedad(fleet_id)
    client = _reportante_client(admin, fleet_id)

    # Otra flota: 404, no 403 (no se confirma que exista).
    assert client.get(f"/api/v1/novedades/{foreign_novedad_id}").status_code == 404
    assert client.post(f"/api/v1/novedades/{foreign_novedad_id}/retry").status_code == 404

    # Borrar y sincronizar estado son de administrador.
    assert client.delete(f"/api/v1/novedades/{own_novedad_id}").status_code == 403
    assert client.post("/api/v1/novedades/sync-status").status_code == 403

    # Fuera del módulo: nada.
    assert client.get("/api/v1/mantenimiento/placas").status_code == 403
    assert client.get("/api/v1/users").status_code == 403
    assert client.get("/api/v1/roles").status_code == 403
    assert client.get("/api/v1/fleets").status_code == 403
    assert client.get("/api/v1/data-quality/sync-runs").status_code == 403
