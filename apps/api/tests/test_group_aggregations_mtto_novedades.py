"""Agregación por grupo interno de vehículos (endpoints aditivos).

Contrato que fijan estos tests:

- `GET /novedades/por-grupo` agrega por grupo HOJA (`vehicle_group_id`
  exacto), con bucket `group_id = null` para placas sin grupo, y separa
  abiertas (incluye no verificadas) de resueltas (`external_is_done IS TRUE`);
- negativa de alcance: un usuario limitado a la flota A no ve conteos de la
  flota B;
- `GET /mantenimiento/disponibilidad/por-grupo` responde 200 con lista vacía
  sin datos, y el rollup (`_group_disponibilidad_rollup`, función pura) usa el
  `vehicle_group_id` del mapa de `_scoped_plates` sin duplicar la fórmula de
  disponibilidad.
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
from app.services.mantenimiento_service import _group_disponibilidad_rollup

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
        json={"code": f"GAG-{uuid.uuid4().hex[:8].upper()}", "name": "Flota agregación"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _seed_group(fleet_id: str, name: str) -> str:
    async def _do() -> str:
        async with AsyncSessionLocal() as db:
            group = FleetVehicleGroup(
                source_id=int(uuid.uuid4().int % 10**9),
                fleet_id=uuid.UUID(fleet_id),
                parent_id=None,
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
            plate = f"GAG{uuid.uuid4().hex[:4].upper()}"
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


def _seed_novedad(
    fleet_id: str,
    vehicle_id: str,
    plate: str,
    *,
    external_is_done: bool | None = None,
) -> str:
    async def _do() -> str:
        async with AsyncSessionLocal() as db:
            novedad = Novedad(
                vehicle_id=uuid.UUID(vehicle_id),
                fleet_id=uuid.UUID(fleet_id),
                vehicle_code=plate,
                reported_at=datetime.now(UTC),
                reported_by_id=23,
                priority="low",
                comment="prueba agregación por grupo",
                send_mail=False,
                cloudfleet_status="sent",
                cloudfleet_issue_number=int(uuid.uuid4().int % 10**6),
                external_is_done=external_is_done,
            )
            db.add(novedad)
            await db.commit()
            return str(novedad.id)

    return asyncio.run(_do())


def _restricted_client(admin: TestClient, fleet_id: str) -> TestClient:
    email = f"gag-{uuid.uuid4().hex[:8]}@portalclientes.test"
    password = "GagPass123!"
    resp = admin.post(
        "/api/v1/users",
        json={
            "email": email,
            "password": password,
            "full_name": "Usuario agregación",
            "role_codes": ["reportante_novedades"],
            "fleet_ids": [fleet_id],
        },
    )
    assert resp.status_code == 201, resp.text
    client = TestClient(app)
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return client


def test_novedades_por_grupo_buckets() -> None:
    admin = _admin_client()
    fleet_a = _create_fleet(admin)
    grupo_1 = _seed_group(fleet_a, "Regional Norte")
    grupo_2 = _seed_group(fleet_a, "Regional Sur")
    vid_1, plate_1 = _seed_vehicle(fleet_a, group_id=grupo_1)
    vid_2, plate_2 = _seed_vehicle(fleet_a, group_id=grupo_2)
    vid_3, plate_3 = _seed_vehicle(fleet_a, group_id=None)

    # grupo_1: una abierta (no verificada) y una resuelta; grupo_2 y sin
    # grupo: una abierta cada uno.
    _seed_novedad(fleet_a, vid_1, plate_1)
    _seed_novedad(fleet_a, vid_1, plate_1, external_is_done=True)
    _seed_novedad(fleet_a, vid_2, plate_2, external_is_done=False)
    _seed_novedad(fleet_a, vid_3, plate_3)

    client = _restricted_client(admin, fleet_a)
    resp = client.get("/api/v1/novedades/por-grupo")
    assert resp.status_code == 200, resp.text
    buckets = {b["group_id"]: b for b in resp.json()}
    assert len(buckets) == 3

    assert buckets[grupo_1] == {
        "group_id": grupo_1,
        "total": 2,
        "abiertas": 1,
        "resueltas": 1,
    }
    assert buckets[grupo_2] == {
        "group_id": grupo_2,
        "total": 1,
        "abiertas": 1,
        "resueltas": 0,
    }
    # Placa sin grupo: bucket group_id = null.
    assert buckets[None] == {
        "group_id": None,
        "total": 1,
        "abiertas": 1,
        "resueltas": 0,
    }

    # Rango invertido: mismo 422 que el listado.
    resp = client.get(
        "/api/v1/novedades/por-grupo",
        params={"date_from": "2026-08-31", "date_to": "2026-08-01"},
    )
    assert resp.status_code == 422, resp.text

    # date_to inclusivo (fin de día): la novedad de hoy entra en el rango
    # que termina hoy.
    today = datetime.now(UTC).date().isoformat()
    resp = client.get(
        "/api/v1/novedades/por-grupo",
        params={"date_from": today, "date_to": today},
    )
    assert resp.status_code == 200, resp.text
    assert sum(b["total"] for b in resp.json()) == 4


def test_novedades_por_grupo_scope_negative() -> None:
    admin = _admin_client()
    fleet_a = _create_fleet(admin)
    fleet_b = _create_fleet(admin)
    grupo_b = _seed_group(fleet_b, "Regional Ajena")
    vid_b, plate_b = _seed_vehicle(fleet_b, group_id=grupo_b)
    _seed_novedad(fleet_b, vid_b, plate_b)

    # Usuario limitado a la flota A: no ve conteos de B, ni siquiera el bucket.
    client = _restricted_client(admin, fleet_a)
    resp = client.get("/api/v1/novedades/por-grupo")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []

    # Admin acotado a la flota B sí los ve (control positivo del mismo dato).
    resp = admin.get("/api/v1/novedades/por-grupo", headers={"X-Fleet-Id": fleet_b})
    assert resp.status_code == 200, resp.text
    buckets = {b["group_id"]: b for b in resp.json()}
    assert buckets[grupo_b]["total"] == 1


def test_disponibilidad_por_grupo_empty_scope_is_200_empty_list() -> None:
    admin = _admin_client()
    fleet_a = _create_fleet(admin)
    # Flota recién creada, sin vehículos en el maestro CloudFleet: el alcance
    # de `_scoped_plates` queda vacío y la respuesta es una lista vacía.
    resp = admin.get(
        "/api/v1/mantenimiento/disponibilidad/por-grupo",
        headers={"X-Fleet-Id": fleet_a},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_group_disponibilidad_rollup_uses_scope_map() -> None:
    """El rollup agrega por el `vehicle_group_id` del mapa de `_scoped_plates`.

    Función pura: recibe el detalle por placa ya calculado (fake) y no
    recalcula disponibilidad.
    """
    g1 = uuid.uuid4()
    g2 = uuid.uuid4()
    scope = {
        "AAA111": {"vehicle_group_id": g1, "fleet_name": "F", "cd": "CD"},
        "BBB222": {"vehicle_group_id": g1, "fleet_name": "F", "cd": "CD"},
        "CCC333": {"vehicle_group_id": g2, "fleet_name": "F", "cd": "CD"},
        "DDD444": {"vehicle_group_id": None, "fleet_name": "F", "cd": "CD"},
    }
    per_plate_hours = {"AAA111": 10.0, "BBB222": 2.5, "CCC333": 4.0}
    # DDD444 sin downtime a propósito: debe salir igual, con 0.0.

    buckets = _group_disponibilidad_rollup(scope, per_plate_hours, 100.0)

    by_group = {b["group_id"]: b for b in buckets}
    assert len(buckets) == 3
    assert by_group[g1] == {
        "group_id": g1,
        "placas": 2,
        "should_hours": 200.0,
        "downtime_hours": 12.5,
    }
    assert by_group[g2] == {
        "group_id": g2,
        "placas": 1,
        "should_hours": 100.0,
        "downtime_hours": 4.0,
    }
    assert by_group[None] == {
        "group_id": None,
        "placas": 1,
        "should_hours": 100.0,
        "downtime_hours": 0.0,
    }
    # Orden estable: downtime DESC, luego group_id (None primero en empates).
    assert [b["group_id"] for b in buckets] == [g1, g2, None]
