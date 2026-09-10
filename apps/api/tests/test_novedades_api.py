"""Tests HTTP del borrado administrativo de novedades.

`DELETE /novedades/{id}` existe para retirar novedades de prueba. CloudFleet
no permite borrar issues (responde 405), así que el borrado es sólo local:
fila, adjuntos en base y objetos en MinIO. Lo que estas pruebas fijan:

- sólo el administrador de plataforma puede borrar; `novedades.edit` no basta;
- los objetos de MinIO se intentan borrar, pero un almacenamiento caído no
  impide borrar la fila (los objetos huérfanos son basura recuperable, una
  fila sin objetos no lo es);
- el borrado arrastra los adjuntos en cascada.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.master_data import GeotabDatabase, Vehicle
from app.models.novedad import Novedad, NovedadAttachment
from app.services import object_storage


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
        json={"code": f"NOV-{uuid.uuid4().hex[:8].upper()}", "name": "Flota novedades"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _seed_vehicle(fleet_id: str) -> tuple[str, str]:
    """Inserta base Geotab + vehículo bajo la flota; devuelve (vehicle_id, placa)."""

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
            plate = f"TST{uuid.uuid4().hex[:4].upper()}"
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


def _seed_novedad(fleet_id: str, *, attachments: int = 1) -> tuple[str, list[str]]:
    """Inserta una novedad ya 'enviada' con N adjuntos ficticios."""
    vehicle_id, plate = _seed_vehicle(fleet_id)

    async def _do() -> tuple[str, list[str]]:
        async with AsyncSessionLocal() as db:
            novedad = Novedad(
                vehicle_id=uuid.UUID(vehicle_id),
                fleet_id=uuid.UUID(fleet_id),
                created_by_id=None,
                vehicle_code=plate,
                reported_at=datetime.now(UTC),
                reported_by_id=23,
                priority="low",
                comment="novedad de prueba",
                send_mail=False,
                cloudfleet_status="sent",
                cloudfleet_issue_number=999_001,
            )
            db.add(novedad)
            await db.flush()
            keys: list[str] = []
            for i in range(attachments):
                key = f"novedades/{novedad.id}/{uuid.uuid4().hex}.jpg"
                db.add(
                    NovedadAttachment(
                        novedad_id=novedad.id,
                        bucket=settings.minio_bucket,
                        object_key=key,
                        filename=f"foto-{i}.jpg",
                        content_type="image/jpeg",
                        size_bytes=1234,
                    )
                )
                keys.append(key)
            await db.commit()
            return str(novedad.id), keys

    return asyncio.run(_do())


def _rows_left(novedad_id: str) -> tuple[bool, int]:
    async def _do() -> tuple[bool, int]:
        async with AsyncSessionLocal() as db:
            nov = await db.get(Novedad, uuid.UUID(novedad_id))
            atts = (
                (
                    await db.execute(
                        select(NovedadAttachment).where(
                            NovedadAttachment.novedad_id == uuid.UUID(novedad_id)
                        )
                    )
                )
                .scalars()
                .all()
            )
            return nov is not None, len(atts)

    return asyncio.run(_do())


def _novedades_edit_client(admin: TestClient, fleet_id: str) -> TestClient:
    """Usuario con `novedades.edit` sobre la flota, sin rol admin."""
    role_code = f"nov_{uuid.uuid4().hex[:8]}"
    resp = admin.post(
        "/api/v1/roles",
        json={
            "code": role_code,
            "name": "Reportante",
            "description": None,
            "permission_codes": ["novedades.view", "novedades.edit"],
        },
    )
    assert resp.status_code == 201, resp.text
    email = f"nov-{uuid.uuid4().hex[:8]}@portalclientes.test"
    password = "NovPass123!"
    resp = admin.post(
        "/api/v1/users",
        json={
            "email": email,
            "password": password,
            "full_name": "Reportante",
            "role_codes": [role_code],
            "fleet_ids": [fleet_id],
        },
    )
    assert resp.status_code == 201, resp.text
    client = TestClient(app)
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return client


def test_admin_deletes_novedad_attachments_and_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    admin = _admin_client()
    fleet_id = _create_fleet(admin)
    novedad_id, keys = _seed_novedad(fleet_id, attachments=2)

    deleted: list[tuple[str, str]] = []

    async def _fake_delete(bucket: str, object_key: str) -> None:
        deleted.append((bucket, object_key))

    monkeypatch.setattr(object_storage, "delete_object", _fake_delete)

    resp = admin.delete(f"/api/v1/novedades/{novedad_id}")
    assert resp.status_code == 204, resp.text

    exists, attachments_left = _rows_left(novedad_id)
    assert exists is False
    assert attachments_left == 0
    assert sorted(k for _, k in deleted) == sorted(keys)
    assert {b for b, _ in deleted} == {settings.minio_bucket}

    # Segunda vez: ya no existe.
    assert admin.delete(f"/api/v1/novedades/{novedad_id}").status_code == 404


def test_delete_survives_storage_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    """MinIO caído no puede dejar la fila viva: el objeto huérfano es recuperable."""
    admin = _admin_client()
    fleet_id = _create_fleet(admin)
    novedad_id, _ = _seed_novedad(fleet_id, attachments=1)

    def _boom(bucket: str, object_key: str) -> None:
        raise RuntimeError("minio down")

    monkeypatch.setattr(object_storage, "_delete_object_sync", _boom)

    resp = admin.delete(f"/api/v1/novedades/{novedad_id}")
    assert resp.status_code == 204, resp.text
    exists, attachments_left = _rows_left(novedad_id)
    assert exists is False
    assert attachments_left == 0


def test_novedades_edit_cannot_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    admin = _admin_client()
    fleet_id = _create_fleet(admin)
    novedad_id, _ = _seed_novedad(fleet_id, attachments=1)
    editor = _novedades_edit_client(admin, fleet_id)

    async def _never(bucket: str, object_key: str) -> None:
        raise AssertionError("no debería intentar borrar objetos")

    monkeypatch.setattr(object_storage, "delete_object", _never)

    # El mismo usuario sí ve la novedad: el 403 es por rol, no por alcance.
    assert editor.get(f"/api/v1/novedades/{novedad_id}").status_code == 200
    resp = editor.delete(f"/api/v1/novedades/{novedad_id}")
    assert resp.status_code == 403, resp.text
    exists, attachments_left = _rows_left(novedad_id)
    assert exists is True
    assert attachments_left == 1


def test_delete_unknown_novedad_returns_404() -> None:
    admin = _admin_client()
    resp = admin.delete(f"/api/v1/novedades/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_delete_requires_session() -> None:
    anon = TestClient(app)
    resp = anon.delete(f"/api/v1/novedades/{uuid.uuid4()}")
    assert resp.status_code == 401
