"""Curvas de par y potencia del motor: emparejamiento y réplica de metadatos.

Dos bloques con costos distintos:

- `resolve_curves` es una función pura y se prueba sin base de datos. Ahí vive la
  regla del negocio (CPL exacto > único documento del motor > ambiguo), que es
  lo que no se debe romper;
- la réplica y la detección de cambio necesitan PostgreSQL y van marcadas
  `integration`, con ROLLBACK al final igual que el resto de los tests del sync.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.main import app
from app.services import motor_curve_service
from app.services.motor_curve_service import (
    MATCH_AMBIGUOUS,
    MATCH_CPL,
    MATCH_MOTOR,
    AttachmentRow,
    VehicleGroup,
    resolve_curves,
)
from app.services.sync_service import apply_snapshot

_MOTOR = "CURVMOT"
_OTHER_MOTOR = "CURVMOT2"


def _attachment(
    source_id: int,
    *,
    cpl: str | None,
    motor_type: str = _MOTOR,
    stored: str = "obj.pdf",
) -> AttachmentRow:
    return AttachmentRow(
        id=uuid.UUID(int=source_id),
        source_id=source_id,
        motor_type=motor_type,
        cpl=cpl,
        original_filename=f"curva-{source_id}.pdf",
        content_type="application/pdf",
        file_size=1024,
        source_updated_at=datetime(2026, 8, 1, tzinfo=UTC),
        fetch_status="pending",
        cached=False,
    )


# ---------------------------------------------------------------------------
# Emparejamiento (función pura)
# ---------------------------------------------------------------------------
def test_exact_cpl_wins_over_the_other_documents_of_the_motor() -> None:
    resolution = resolve_curves(
        [VehicleGroup(motor_type=_MOTOR, cpl="5376", vehicle_count=28)],
        [
            _attachment(1, cpl="3705"),
            _attachment(2, cpl="5376"),
            _attachment(3, cpl="9999"),
        ],
    )
    assert [c.attachment.source_id for c in resolution.curves] == [2]
    assert resolution.curves[0].match == MATCH_CPL
    assert resolution.without_curve == ()


def test_two_documents_with_the_same_cpl_both_apply() -> None:
    """Navi permite dos adjuntos con el mismo CPL. Elegir uno por fecha sería
    esconder el otro sin que nadie lo haya decidido."""
    resolution = resolve_curves(
        [VehicleGroup(motor_type=_MOTOR, cpl="3705", vehicle_count=1)],
        [_attachment(1, cpl="3705"), _attachment(2, cpl="3705")],
    )
    assert [c.attachment.source_id for c in resolution.curves] == [1, 2]
    assert {c.match for c in resolution.curves} == {MATCH_CPL}


def test_single_document_of_the_motor_applies_even_if_the_cpl_differs() -> None:
    resolution = resolve_curves(
        [VehicleGroup(motor_type=_MOTOR, cpl="3315", vehicle_count=4)],
        [_attachment(1, cpl="3065")],
    )
    assert [c.attachment.source_id for c in resolution.curves] == [1]
    assert resolution.curves[0].match == MATCH_MOTOR


def test_several_documents_and_no_cpl_match_is_ambiguous_not_a_guess() -> None:
    """Con varios candidatos y ningún CPL coincidente se ofrecen TODOS. La
    alternativa —elegir el más nuevo o el de CPL más parecido— mostraría la
    curva equivocada bajo el nombre correcto."""
    resolution = resolve_curves(
        [VehicleGroup(motor_type=_MOTOR, cpl="4595", vehicle_count=4)],
        [_attachment(1, cpl="3705"), _attachment(2, cpl="5376")],
    )
    assert [c.attachment.source_id for c in resolution.curves] == [1, 2]
    assert {c.match for c in resolution.curves} == {MATCH_AMBIGUOUS}


def test_vehicle_without_cpl_never_matches_by_cpl() -> None:
    """Un CPL ausente no coincide con un adjunto sin CPL: no saber el CPL del
    vehículo no es evidencia de que sea el del documento."""
    resolution = resolve_curves(
        [VehicleGroup(motor_type=_MOTOR, cpl=None, vehicle_count=2)],
        [_attachment(1, cpl=None), _attachment(2, cpl="3705")],
    )
    assert {c.match for c in resolution.curves} == {MATCH_AMBIGUOUS}


def test_motor_without_documents_is_reported_not_hidden() -> None:
    resolution = resolve_curves(
        [VehicleGroup(motor_type="SIN_DOC", cpl="1234", vehicle_count=8)],
        [_attachment(1, cpl="3705")],
    )
    assert resolution.curves == ()
    assert [g.motor_type for g in resolution.without_curve] == ["SIN_DOC"]


def test_match_is_per_group_not_only_per_document() -> None:
    """El mismo PDF puede ser coincidencia exacta para unos vehículos y el único
    candidato del motor para otros. Publicar sólo el más fuerte diría "coincide
    el CPL" sobre vehículos cuyo CPL no coincide con nada."""
    resolution = resolve_curves(
        [
            VehicleGroup(motor_type=_MOTOR, cpl="3065", vehicle_count=117),
            VehicleGroup(motor_type=_MOTOR, cpl="3315", vehicle_count=4),
        ],
        [_attachment(1, cpl="3065")],
    )
    curve = resolution.curves[0]
    assert curve.match == MATCH_CPL
    assert {(g.cpl, g.match) for g in curve.covered} == {
        ("3065", MATCH_CPL),
        ("3315", MATCH_MOTOR),
    }
    # El conteo suma los dos grupos que el documento cubre.
    assert curve.vehicle_count == 121


def test_documents_of_another_motor_never_leak() -> None:
    resolution = resolve_curves(
        [VehicleGroup(motor_type=_MOTOR, cpl="3705", vehicle_count=1)],
        [_attachment(1, cpl="3705", motor_type=_OTHER_MOTOR)],
    )
    assert resolution.curves == ()
    assert [g.motor_type for g in resolution.without_curve] == [_MOTOR]


def test_cpl_comparison_ignores_case_and_whitespace() -> None:
    resolution = resolve_curves(
        [VehicleGroup(motor_type=_MOTOR, cpl=" 5376 ", vehicle_count=3)],
        [_attachment(1, cpl="5376"), _attachment(2, cpl="3705")],
    )
    assert [c.attachment.source_id for c in resolution.curves] == [1]
    assert resolution.curves[0].match == MATCH_CPL


def test_output_order_is_stable() -> None:
    """La pantalla lista documentos: una lista que se reordena entre peticiones
    idénticas es un defecto de interfaz."""
    groups = [VehicleGroup(motor_type=_MOTOR, cpl="4595", vehicle_count=1)]
    attachments = [
        _attachment(30, cpl="5376"),
        _attachment(10, cpl="3705"),
        _attachment(20, cpl="3705"),
    ]
    first = resolve_curves(groups, attachments)
    second = resolve_curves(groups, list(reversed(attachments)))
    assert [c.attachment.source_id for c in first.curves] == [10, 20, 30]
    assert [c.attachment.source_id for c in second.curves] == [10, 20, 30]


# ---------------------------------------------------------------------------
# Réplica de metadatos y detección de cambio (requieren PostgreSQL)
# ---------------------------------------------------------------------------
_GENERATED_AT = "2026-08-28T12:00:00Z"
_SOURCE_ID = 990501


def _snapshot(attachments: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "generated_at": _GENERATED_AT,
        "since": None,
        "customers": [],
        "vehicles": [],
        "motors": [
            {
                "motor_type": _MOTOR,
                "updated_at": _GENERATED_AT,
                "governed_speed_rpm": None,
                "max_overspeed_rpm": None,
                "rpm_bands": [],
                "attachments": attachments,
            }
        ],
    }


def _attachment_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "id": _SOURCE_ID,
        "cpl": "5376",
        "original_filename": "curva.pdf",
        "content_type": "application/pdf",
        "file_size": 1024,
        "stored_filename": "aaaa.pdf",
        "updated_at": "2026-08-01T10:00:00Z",
    }
    payload.update(overrides)
    return payload


async def _row(session) -> Any:
    return (
        await session.execute(
            text(
                "SELECT cpl, file_size, source_stored_filename, object_key, "
                "content_sha256, fetch_status, fetch_error, is_active "
                "FROM motor_attachments WHERE source_id = :sid"
            ),
            {"sid": _SOURCE_ID},
        )
    ).one()


async def _mark_cached(session) -> None:
    await session.execute(
        text(
            "UPDATE motor_attachments SET object_key = 'k', content_sha256 = 'sha', "
            "fetch_status = 'ready' WHERE source_id = :sid"
        ),
        {"sid": _SOURCE_ID},
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_snapshot_replicates_attachment_metadata() -> None:
    async with AsyncSessionLocal() as s:
        try:
            result = await apply_snapshot(s, _snapshot([_attachment_payload()]), full=False)
            assert result.motor_attachments == 1
            row = await _row(s)
            assert row.cpl == "5376"
            assert row.source_stored_filename == "aaaa.pdf"
            # El binario NO viaja en el snapshot: nace sin caché.
            assert row.fetch_status == "pending"
            assert row.object_key is None
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unchanged_attachment_keeps_the_cached_binary() -> None:
    """Un sync que no trae nada nuevo no puede tirar la caché: si lo hiciera,
    cada corrida diaria volvería a descargar los doce documentos."""
    async with AsyncSessionLocal() as s:
        try:
            await apply_snapshot(s, _snapshot([_attachment_payload()]), full=False)
            await _mark_cached(s)
            await apply_snapshot(s, _snapshot([_attachment_payload()]), full=False)
            row = await _row(s)
            assert row.object_key == "k"
            assert row.content_sha256 == "sha"
            assert row.fetch_status == "ready"
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_replaced_binary_invalidates_the_cache() -> None:
    """`stored_filename` es un uuid4 nuevo por cada carga en Navi: si cambia, el
    documento cambió y la copia local ya no es el documento vigente."""
    async with AsyncSessionLocal() as s:
        try:
            await apply_snapshot(s, _snapshot([_attachment_payload()]), full=False)
            await _mark_cached(s)
            await apply_snapshot(
                s,
                _snapshot(
                    [
                        _attachment_payload(
                            stored_filename="bbbb.pdf",
                            updated_at="2026-08-20T10:00:00Z",
                            file_size=2048,
                        )
                    ]
                ),
                full=False,
            )
            row = await _row(s)
            assert row.object_key is None
            assert row.content_sha256 is None
            assert row.fetch_status == "pending"
            assert row.file_size == 2048
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_only_the_cpl_changing_keeps_the_cache() -> None:
    """Corregir el CPL en Navi no cambia el PDF. Invalidar ahí obligaría a
    volver a descargar un archivo idéntico."""
    async with AsyncSessionLocal() as s:
        try:
            await apply_snapshot(s, _snapshot([_attachment_payload()]), full=False)
            await _mark_cached(s)
            await apply_snapshot(s, _snapshot([_attachment_payload(cpl="3705")]), full=False)
            row = await _row(s)
            assert row.cpl == "3705"
            assert row.object_key == "k"
            assert row.fetch_status == "ready"
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_failed_download_is_not_reset_by_an_unchanged_sync() -> None:
    """El enfriamiento del reintento lo decide el servicio. Si cada sync
    devolviera la fila a 'pending', un origen caído se martillaría de nuevo."""
    async with AsyncSessionLocal() as s:
        try:
            await apply_snapshot(s, _snapshot([_attachment_payload()]), full=False)
            await s.execute(
                text(
                    "UPDATE motor_attachments SET fetch_status = 'failed', "
                    "fetch_error = 'provider_error' WHERE source_id = :sid"
                ),
                {"sid": _SOURCE_ID},
            )
            await apply_snapshot(s, _snapshot([_attachment_payload()]), full=False)
            row = await _row(s)
            assert row.fetch_status == "failed"
            assert row.fetch_error == "provider_error"
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_malformed_attachment_is_dropped_without_breaking_the_sync() -> None:
    """Una curva es documentación, no un dato de cálculo: un adjunto ilegible no
    puede tumbar el sync del catálogo de motores."""
    async with AsyncSessionLocal() as s:
        try:
            result = await apply_snapshot(
                s,
                _snapshot([{"cpl": "5376"}, "no-soy-un-objeto", _attachment_payload()]),
                full=False,
            )
            assert result.motor_attachments == 1
            assert (await _row(s)).cpl == "5376"
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_old_payload_without_attachments_touches_nothing() -> None:
    async with AsyncSessionLocal() as s:
        try:
            await apply_snapshot(s, _snapshot([_attachment_payload()]), full=False)
            snapshot = _snapshot([])
            del snapshot["motors"][0]["attachments"]
            result = await apply_snapshot(s, snapshot, full=False)
            assert result.motor_attachments == 0
            # Sin la clave no hay prueba de una baja: la fila sigue ofreciéndose.
            assert (await _row(s)).is_active is True
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_sync_deactivates_an_attachment_deleted_upstream() -> None:
    async with AsyncSessionLocal() as s:
        try:
            await apply_snapshot(s, _snapshot([_attachment_payload()]), full=False)
            other = _attachment_payload(id=_SOURCE_ID + 1, stored_filename="cccc.pdf")
            await apply_snapshot(s, _snapshot([other]), full=True)
            row = await _row(s)
            # No se borra la fila: la copia en MinIO sigue referenciada y un
            # borrado accidental en el origen no debe destruir el documento.
            assert row.is_active is False
        finally:
            await s.rollback()


# ---------------------------------------------------------------------------
# Endpoints (alcance de flota y degradación del proveedor)
# ---------------------------------------------------------------------------
def _admin_client() -> TestClient:
    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/login",
        json={
            "email": settings.bootstrap_admin_email,
            "password": settings.bootstrap_admin_password,
        },
    )
    assert resp.status_code == 200, resp.text
    return client


def _seed_motor_with_curve(motor_type: str, cpl: str) -> tuple[str, str, str]:
    """Crea flota + base + vehículo + adjunto. Devuelve (fleet_id, plate, curve_id)."""

    async def _do() -> tuple[str, str, str]:
        async with AsyncSessionLocal() as db:
            suffix = uuid.uuid4().hex[:8]
            fleet_id = (
                await db.execute(
                    text(
                        "INSERT INTO fleets (id, code, name, is_active, created_at, updated_at) "
                        "VALUES (gen_random_uuid(), :code, :name, true, now(), now()) "
                        "RETURNING id"
                    ),
                    {"code": f"CURV-{suffix.upper()}", "name": "Flota curvas"},
                )
            ).scalar_one()
            await db.execute(
                text(
                    "INSERT INTO motor_catalog (motor_type, created_at, updated_at) "
                    "VALUES (:mt, now(), now()) ON CONFLICT (motor_type) DO NOTHING"
                ),
                {"mt": motor_type},
            )
            plate = f"CV{suffix[:5].upper()}"
            await db.execute(
                text(
                    "INSERT INTO vehicles (id, plate, fleet_id, motor_type, cpl, "
                    "geotab_customer_status, vocacional, category, is_active, "
                    "created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :plate, :fleet, :mt, :cpl, 'unknown', "
                    "false, 'Ninguna', true, now(), now())"
                ),
                {"plate": plate, "fleet": fleet_id, "mt": motor_type, "cpl": cpl},
            )
            curve_id = (
                await db.execute(
                    text(
                        "INSERT INTO motor_attachments (id, source_id, motor_type, cpl, "
                        "original_filename, content_type, file_size, source_stored_filename, "
                        "fetch_status, is_active, created_at, updated_at) "
                        "VALUES (gen_random_uuid(), :sid, :mt, :cpl, 'curva.pdf', "
                        "'application/pdf', 1024, :stored, 'pending', true, now(), now()) "
                        "RETURNING id"
                    ),
                    {
                        "sid": int(uuid.uuid4().int % 10_000_000) + 900_000_000,
                        "mt": motor_type,
                        "cpl": cpl,
                        "stored": f"{suffix}.pdf",
                    },
                )
            ).scalar_one()
            await db.commit()
            return str(fleet_id), plate, str(curve_id)

    return asyncio.run(_do())


@pytest.mark.integration
def test_endpoint_lists_only_the_curves_of_the_selected_scope() -> None:
    motor = f"CURV{uuid.uuid4().hex[:6].upper()}"
    other_motor = f"CURV{uuid.uuid4().hex[:6].upper()}"
    fleet_id, _, curve_id = _seed_motor_with_curve(motor, "5376")
    other_fleet_id, _, other_curve_id = _seed_motor_with_curve(other_motor, "3705")

    admin = _admin_client()
    resp = admin.get("/api/v1/vehicles/motor-curves", headers={"X-Fleet-Id": fleet_id})
    assert resp.status_code == 200, resp.text
    ids = {curve["id"] for curve in resp.json()["curvas"]}
    assert curve_id in ids
    # El catálogo de documentos es global: si el alcance no filtrara, la curva
    # de la otra flota aparecería acá.
    assert other_curve_id not in ids
    assert other_fleet_id != fleet_id


@pytest.mark.integration
def test_download_of_a_curve_outside_the_scope_is_404_not_403() -> None:
    """404 y no 403: un 403 le confirmaría al usuario que existe un documento de
    un motor que su flota no usa."""
    motor = f"CURV{uuid.uuid4().hex[:6].upper()}"
    _, _, curve_id = _seed_motor_with_curve(motor, "5376")
    other_motor = f"CURV{uuid.uuid4().hex[:6].upper()}"
    other_fleet_id, _, _ = _seed_motor_with_curve(other_motor, "3705")

    admin = _admin_client()
    resp = admin.get(
        f"/api/v1/vehicles/motor-curves/{curve_id}/file",
        headers={"X-Fleet-Id": other_fleet_id},
    )
    assert resp.status_code == 404


@pytest.mark.integration
def test_download_returns_503_when_the_provider_is_unavailable() -> None:
    """Un proveedor caído no puede producir un 500: el documento existe, lo que
    falta es el origen, y la pantalla tiene que poder decirlo y reintentar."""
    motor = f"CURV{uuid.uuid4().hex[:6].upper()}"
    fleet_id, _, curve_id = _seed_motor_with_curve(motor, "5376")

    admin = _admin_client()
    with mock.patch.object(
        motor_curve_service,
        "_fetch_from_provider",
        side_effect=motor_curve_service.MotorCurveUnavailableError("provider_unreachable"),
    ):
        resp = admin.get(
            f"/api/v1/vehicles/motor-curves/{curve_id}/file",
            headers={"X-Fleet-Id": fleet_id},
        )
    assert resp.status_code == 503
    assert "no respondió" in resp.json()["detail"]


@pytest.mark.integration
def test_download_serves_the_binary_and_caches_it() -> None:
    motor = f"CURV{uuid.uuid4().hex[:6].upper()}"
    fleet_id, _, curve_id = _seed_motor_with_curve(motor, "5376")
    payload = b"%PDF-1.4 curva de prueba"

    admin = _admin_client()
    with (
        mock.patch.object(
            motor_curve_service,
            "_fetch_from_provider",
            return_value=(payload, "application/pdf"),
        ),
        mock.patch.object(
            motor_curve_service.object_storage, "put_object", new_callable=mock.AsyncMock
        ) as put_object,
    ):
        resp = admin.get(
            f"/api/v1/vehicles/motor-curves/{curve_id}/file",
            headers={"X-Fleet-Id": fleet_id},
        )

    assert resp.status_code == 200
    assert resp.content == payload
    assert resp.headers["content-type"].startswith("application/pdf")
    assert "inline" in resp.headers["content-disposition"]
    put_object.assert_awaited_once()

    async def _state() -> Any:
        async with AsyncSessionLocal() as db:
            return (
                await db.execute(
                    text(
                        "SELECT fetch_status, object_key, content_sha256 "
                        "FROM motor_attachments WHERE id = :id"
                    ),
                    {"id": uuid.UUID(curve_id)},
                )
            ).one()

    row = asyncio.run(_state())
    assert row.fetch_status == "ready"
    assert row.object_key
    assert row.content_sha256 == hashlib.sha256(payload).hexdigest()


@pytest.mark.integration
def test_a_storage_failure_still_delivers_the_document() -> None:
    """Negarle la curva al usuario porque MinIO falló sería un fallo
    autoinfligido: el binario ya está en memoria."""
    motor = f"CURV{uuid.uuid4().hex[:6].upper()}"
    fleet_id, _, curve_id = _seed_motor_with_curve(motor, "5376")
    payload = b"%PDF-1.4 sin cache"

    admin = _admin_client()
    with (
        mock.patch.object(
            motor_curve_service,
            "_fetch_from_provider",
            return_value=(payload, "application/pdf"),
        ),
        mock.patch.object(
            motor_curve_service.object_storage,
            "put_object",
            new_callable=mock.AsyncMock,
            side_effect=motor_curve_service.object_storage.ObjectStorageError("sin bucket"),
        ),
    ):
        resp = admin.get(
            f"/api/v1/vehicles/motor-curves/{curve_id}/file",
            headers={"X-Fleet-Id": fleet_id},
        )

    assert resp.status_code == 200
    assert resp.content == payload
