"""Catálogo y filtro de grupos internos de vehículos (fleet_vehicle_groups).

Contrato que fijan estos tests:

- `GET /vehicles/groups` respeta el alcance de flota: un usuario limitado a una
  flota no ve los grupos de otra (negativa cross-flota);
- `GET /vehicles?group_id=` filtra por grupo, y un grupo de una flota fuera del
  alcance no devuelve nada (fail-closed, no 403: el filtro no confirma que el
  grupo exista);
- `GET /novedades?group_id=` filtra por la placa del vehículo asignado.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.master_data import FleetVehicleGroup, GeotabDatabase, Vehicle
from app.models.novedad import Novedad

pytestmark = pytest.mark.integration


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
        json={"code": f"GRP-{uuid.uuid4().hex[:8].upper()}", "name": "Flota grupos"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _seed_group(fleet_id: str, name: str, parent_id: str | None = None) -> str:
    async def _do() -> str:
        async with AsyncSessionLocal() as db:
            group = FleetVehicleGroup(
                source_id=int(uuid.uuid4().int % 10**9),
                fleet_id=uuid.UUID(fleet_id),
                parent_id=uuid.UUID(parent_id) if parent_id else None,
                name=name,
                is_active=True,
            )
            db.add(group)
            await db.commit()
            return str(group.id)

    return asyncio.run(_do())


def _seed_vehicle(fleet_id: str, group_id: str | None = None) -> tuple[str, str]:
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
            plate = f"GRP{uuid.uuid4().hex[:4].upper()}"
            v = Vehicle(
                fleet_id=uuid.UUID(fleet_id),
                geotab_database_id=gd.id,
                geotab_device_id=f"dev-{uuid.uuid4().hex[:6]}",
                plate=plate,
                motor_type=None,
                vehicle_group_id=uuid.UUID(group_id) if group_id else None,
                is_active=True,
            )
            db.add(v)
            await db.flush()
            await db.commit()
            return str(v.id), plate

    return asyncio.run(_do())


def _seed_novedad(fleet_id: str, vehicle_id: str, plate: str) -> str:
    async def _do() -> str:
        async with AsyncSessionLocal() as db:
            novedad = Novedad(
                vehicle_id=uuid.UUID(vehicle_id),
                fleet_id=uuid.UUID(fleet_id),
                vehicle_code=plate,
                reported_at=datetime.now(UTC),
                reported_by_id=23,
                priority="low",
                comment="prueba grupos",
                send_mail=False,
                cloudfleet_status="sent",
                cloudfleet_issue_number=int(uuid.uuid4().int % 10**6),
            )
            db.add(novedad)
            await db.commit()
            return str(novedad.id)

    return asyncio.run(_do())


def _restricted_client(admin: TestClient, fleet_id: str) -> TestClient:
    email = f"grp-{uuid.uuid4().hex[:8]}@portalclientes.test"
    password = "GrpPass123!"
    resp = admin.post(
        "/api/v1/users",
        json={
            "email": email,
            "password": password,
            "full_name": "Usuario grupos",
            "role_codes": ["reportante_novedades"],
            "fleet_ids": [fleet_id],
        },
    )
    assert resp.status_code == 201, resp.text
    client = TestClient(app)
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return client


def test_groups_catalog_is_fleet_scoped() -> None:
    admin = _admin_client()
    fleet_a = _create_fleet(admin)
    fleet_b = _create_fleet(admin)
    regional = _seed_group(fleet_a, "Regional Antioquia")
    cedi = _seed_group(fleet_a, "CEDI Medellin", parent_id=regional)
    ajeno = _seed_group(fleet_b, "Regional Ajena")

    # Usuario limitado a la flota A: ve el árbol de A y nada de B.
    client = _restricted_client(admin, fleet_a)
    resp = client.get("/api/v1/vehicles/groups")
    assert resp.status_code == 200, resp.text
    ids = {g["id"] for g in resp.json()}
    assert {regional, cedi} <= ids
    assert ajeno not in ids
    by_id = {g["id"]: g for g in resp.json()}
    assert by_id[cedi]["parent_id"] == regional
    assert by_id[regional]["parent_id"] is None

    # Admin con header X-Fleet-Id acotado a B: sólo los de B.
    resp = admin.get("/api/v1/vehicles/groups", headers={"X-Fleet-Id": fleet_b})
    assert resp.status_code == 200, resp.text
    ids_b = {g["id"] for g in resp.json()}
    assert ajeno in ids_b
    assert regional not in ids_b and cedi not in ids_b


def test_vehicles_filter_by_group_and_cross_fleet_is_empty() -> None:
    admin = _admin_client()
    fleet_a = _create_fleet(admin)
    fleet_b = _create_fleet(admin)
    grupo = _seed_group(fleet_a, "CEDI Norte")
    ajeno = _seed_group(fleet_b, "CEDI Ajeno")
    _vid_in, plate_in = _seed_vehicle(fleet_a, group_id=grupo)
    _vid_out, plate_out = _seed_vehicle(fleet_a, group_id=None)

    client = _restricted_client(admin, fleet_a)

    resp = client.get("/api/v1/vehicles", params={"group_id": grupo})
    assert resp.status_code == 200, resp.text
    plates = {v["plate"] for v in resp.json()}
    assert plates == {plate_in}
    assert all(v["vehicle_group_id"] == grupo for v in resp.json())

    # Un grupo de otra flota no devuelve nada dentro del alcance propio.
    resp = client.get("/api/v1/vehicles", params={"group_id": ajeno})
    assert resp.status_code == 200, resp.text
    assert resp.json() == []

    # Sin filtro, ambos vehículos están.
    resp = client.get("/api/v1/vehicles")
    plates = {v["plate"] for v in resp.json()}
    assert {plate_in, plate_out} <= plates


def test_novedades_filter_by_group() -> None:
    admin = _admin_client()
    fleet_a = _create_fleet(admin)
    grupo = _seed_group(fleet_a, "Regional Costa")
    vid_in, plate_in = _seed_vehicle(fleet_a, group_id=grupo)
    vid_out, plate_out = _seed_vehicle(fleet_a, group_id=None)
    _seed_novedad(fleet_a, vid_in, plate_in)
    _seed_novedad(fleet_a, vid_out, plate_out)

    client = _restricted_client(admin, fleet_a)

    resp = client.get("/api/v1/novedades", params={"group_id": grupo})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert {n["vehicle_code"] for n in body["items"]} == {plate_in}

    resp = client.get("/api/v1/novedades")
    assert resp.status_code == 200, resp.text
    assert {n["vehicle_code"] for n in resp.json()["items"]} >= {plate_in, plate_out}
