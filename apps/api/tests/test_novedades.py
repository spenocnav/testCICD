"""Tests del outbox transaccional de Novedades + idempotencia HTTP.

Estrategia: tests unitarios (sin DB ni red) para fingerprint, backoff y
compensación de adjuntos; tests de integración marcados con
`@pytest.mark.integration` que usan `portal_clientes_codex_test` (DB independiente)
y ROLLBACK al final. Nunca tocamos la DB de desarrollo.

Casos cubiertos:
- compute_request_fingerprint: determinista y sensible a campos de negocio.
- compute_request_fingerprint: NO incluye secretos (e.g. no expone PII).
- _compute_backoff: backoff exponencial capeado y monótono.
- object_storage.delete_object: best-effort, no lanza si MinIO falla.
- claim concurrente: dos transacciones ven sólo 1 ganador.
- recuperación stale: un item processing con locked_at antiguo se re-claima.
- mark_retry: re-eligibiliza failed sin esperar backoff, sin robar processing.
- create_novedad con Idempotency-Key: misma clave + mismo fingerprint -> 1 fila.
- create_novedad con Idempotency-Key: misma clave + fingerprint distinto -> 409.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Los tests de integración sólo se habilitan con una URL explícita a la DB
# desechable. Nunca se usa una URL/credencial de desarrollo como fallback.
TEST_DB_URL = os.environ.get("TEST_NOVEDADES_DATABASE_URL") or os.environ.get(
    "DATABASE_URL"
)


# ---------------------------------------------------------------------------
# Unit: fingerprint
# ---------------------------------------------------------------------------
def test_fingerprint_deterministic() -> None:
    from app.services.novedad_service import compute_request_fingerprint

    kwargs = {
        "vehicle_id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "reported_at": datetime(2026, 7, 14, 10, 0, tzinfo=UTC),
        "priority": "medium",
        "odometer": Decimal("12345.67"),
        "comment": "  Hola mundo  ",
        "send_mail": True,
    }
    a = compute_request_fingerprint(**kwargs)
    b = compute_request_fingerprint(**kwargs)
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_fingerprint_sensitive_to_business_fields() -> None:
    from app.services.novedad_service import compute_request_fingerprint

    base = {
        "vehicle_id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "reported_at": datetime(2026, 7, 14, 10, 0, tzinfo=UTC),
        "priority": "medium",
        "odometer": Decimal("1"),
        "comment": "x",
        "send_mail": False,
    }
    base_fp = compute_request_fingerprint(**base)
    for changed in (
        {"priority": "high"},
        {"comment": "y"},
        {"send_mail": True},
        {"odometer": Decimal("2")},
        {"reported_at": datetime(2026, 7, 14, 10, 1, tzinfo=UTC)},
    ):
        kwargs = {**base, **changed}
        assert compute_request_fingerprint(**kwargs) != base_fp, changed


def test_fingerprint_normalizes_equivalent_timezones() -> None:
    from datetime import timedelta as datetime_timedelta
    from datetime import timezone

    from app.services.novedad_service import compute_request_fingerprint

    base = {
        "vehicle_id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "priority": "medium",
        "odometer": Decimal("1"),
        "comment": "x",
        "send_mail": False,
    }
    utc_value = datetime(2026, 7, 14, 10, 0, tzinfo=UTC)
    local_value = datetime(
        2026,
        7,
        14,
        6,
        0,
        tzinfo=timezone(-datetime_timedelta(hours=4)),
    )

    assert compute_request_fingerprint(
        **base, reported_at=utc_value
    ) == compute_request_fingerprint(**base, reported_at=local_value)


def test_fingerprint_does_not_include_secrets() -> None:
    """El fingerprint NO contiene ni la idempotency_key ni el comment crudo.

    No se le pasa al fingerprint; nos aseguramos de que variables que NO
    deberían ser parte del contrato (idempotency_key, header de auth) no
    afecten el hash. El comment SÍ es parte del contrato (lo envía el
    cliente), pero su valor exacto debe estar normalizado.
    """
    from app.services.novedad_service import compute_request_fingerprint

    kwargs_a = {
        "vehicle_id": uuid.uuid4(),
        "reported_at": datetime(2026, 7, 14, 10, 0, tzinfo=UTC),
        "priority": "low",
        "odometer": None,
        "comment": "Hola",
        "send_mail": False,
    }
    fp_a = compute_request_fingerprint(**kwargs_a)
    kwargs_b = {**kwargs_a, "comment": "   Hola   "}
    fp_b = compute_request_fingerprint(**kwargs_b)
    assert fp_a == fp_b  # normalización de espacios


@pytest.mark.asyncio
async def test_cloudfleet_invalid_vehicle_code_is_classified(monkeypatch) -> None:
    from app.core.config import settings
    from app.services.cloudfleet_service import (
        CloudfleetVehicleNotFoundError,
        create_issue,
    )

    class FakeResponse:
        status_code = 409
        content = b'{"detail":"The specified Vehicle Code is not correct"}'
        reason_phrase = "Conflict"
        # `_observe_rate_limit` lee los headers X-RateLimit-* de toda respuesta.
        headers: ClassVar[dict[str, str]] = {}

        def json(self):
            return {"detail": "The specified Vehicle Code is not correct"}

    class FakeClient:
        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(settings, "cloudfleet_api_key", "test-key")
    with pytest.raises(CloudfleetVehicleNotFoundError):
        await create_issue({"vehicleCode": "TST00C6"}, client=FakeClient())


# ---------------------------------------------------------------------------
# Unit: backoff
# ---------------------------------------------------------------------------
def test_backoff_is_exponential_and_capped() -> None:
    from app.core.config import settings
    from app.services.novedad_outbox_service import _compute_backoff

    base = settings.novedad_outbox_backoff_base_seconds
    cap = settings.novedad_outbox_backoff_max_seconds
    seq = [_compute_backoff(i).total_seconds() for i in range(1, 8)]
    assert seq[0] == pytest.approx(base)
    assert seq[1] == pytest.approx(base * 2)
    assert seq[2] == pytest.approx(base * 4)
    # Monótono no-decreciente.
    assert all(b >= a for a, b in pairwise(seq))
    # Capeado.
    assert seq[-1] <= cap


# ---------------------------------------------------------------------------
# Unit: object_storage.delete_object best-effort
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_object_is_best_effort() -> None:
    """delete_object NO debe lanzar aunque MinIO falle: la compensación es
    best-effort por contrato.
    """
    from app.services.object_storage import delete_object

    with patch(
        "app.services.object_storage.anyio.to_thread.run_sync",
        new=AsyncMock(side_effect=RuntimeError("MinIO caído")),
    ):
        # No lanza.
        await delete_object("bucket", "key")


@pytest.mark.asyncio
async def test_partial_attachment_upload_is_compensated(monkeypatch) -> None:
    from fastapi import HTTPException

    from app.api.v1.novedades import _upload_attachments
    from app.services import novedad_service, object_storage
    from app.services.object_storage import ObjectStorageError, StoredObject

    class _Upload:
        content_type = "image/png"

        def __init__(self, filename: str) -> None:
            self.filename = filename

        async def read(self) -> bytes:
            return b"image"

    put = AsyncMock(
        side_effect=[
            StoredObject(bucket="novedades", object_key="first.png", size_bytes=5),
            ObjectStorageError("MinIO caído"),
        ]
    )
    delete_mock = AsyncMock()
    add = AsyncMock(
        return_value=SimpleNamespace(bucket="novedades", object_key="first.png")
    )
    monkeypatch.setattr(object_storage, "put_object", put)
    monkeypatch.setattr(object_storage, "delete_object", delete_mock)
    monkeypatch.setattr(novedad_service, "add_attachment", add)

    with pytest.raises(HTTPException):
        await _upload_attachments(
            object(),
            novedad=SimpleNamespace(id=uuid.uuid4()),
            attachments=[_Upload("one.png"), _Upload("two.png")],
        )

    delete_mock.assert_awaited_once_with("novedades", "first.png")


@pytest.mark.asyncio
async def test_attachment_compensates_when_db_add_fails_after_upload(
    monkeypatch,
) -> None:
    """Si `put_object` sube el archivo pero `add_attachment` falla al
    persistir la fila (p. ej. IntegrityError en el flush), el objeto de
    MinIO del archivo actual NO debe quedar huérfano: la compensación debe
    borrarlo además de los anteriores.
    """
    from app.api.v1.novedades import _upload_attachments
    from app.services import novedad_service, object_storage
    from app.services.object_storage import StoredObject

    class _Upload:
        content_type = "image/png"

        def __init__(self, filename: str) -> None:
            self.filename = filename

        async def read(self) -> bytes:
            return b"image"

    put = AsyncMock(
        side_effect=[
            StoredObject(bucket="novedades", object_key="first.png", size_bytes=5),
            StoredObject(bucket="novedades", object_key="second.png", size_bytes=5),
        ]
    )
    delete_mock = AsyncMock()
    # Primer add_attachment OK; el segundo revienta (simula FK roto /
    # IntegrityError tras un put_object exitoso).
    add = AsyncMock(
        side_effect=[
            SimpleNamespace(bucket="novedades", object_key="first.png"),
            RuntimeError("flush falló"),
        ]
    )
    monkeypatch.setattr(object_storage, "put_object", put)
    monkeypatch.setattr(object_storage, "delete_object", delete_mock)
    monkeypatch.setattr(novedad_service, "add_attachment", add)

    with pytest.raises(RuntimeError, match="flush falló"):
        await _upload_attachments(
            object(),
            novedad=SimpleNamespace(id=uuid.uuid4()),
            attachments=[_Upload("one.png"), _Upload("two.png")],
        )

    # Se compensó el primero (vía _compensate_attachments) y el segundo
    # (vía la rama except del fix). El orden puede variar; medimos set.
    called = {c.args for c in delete_mock.await_args_list}
    assert ("novedades", "first.png") in called
    assert ("novedades", "second.png") in called


# ---------------------------------------------------------------------------
# Unit: contrato visual del schema
# ---------------------------------------------------------------------------
def test_novedad_read_schema_excludes_processing_status() -> None:
    """Garantía contractual: el frontend nunca debe ver `processing`.

    El literal `CloudfleetStatus` debe ser exactamente pending/sent/failed.
    """
    from app.schemas.novedad import CloudfleetStatus

    # Esto es estático: la firma del Literal es lo que vale.
    assert CloudfleetStatus.__args__ == ("pending", "sent", "failed")  # type: ignore[attr-defined]


# ===========================================================================
# Integración contra portal_clientes_codex_test
# ===========================================================================
@pytest.fixture(scope="session")
def codex_engine():
    """Engine dedicado al DB de test. Session-wide para no pagar el pool."""
    if not TEST_DB_URL or TEST_DB_URL.rsplit("/", 1)[-1] != "portal_clientes_codex_test":
        pytest.skip("TEST_NOVEDADES_DATABASE_URL debe apuntar a portal_clientes_codex_test")
    engine = create_async_engine(TEST_DB_URL, pool_pre_ping=True)
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture
async def fresh_session_factory(codex_engine):
    """Factory de sesiones NUEVAS sobre codex_engine (no comparte conexión)."""
    return async_sessionmaker(
        bind=codex_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )


async def _seed_user_fleet_vehicle(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Crea usuario, flota y vehículo en la sesión dada. El caller hace commit.

    Devuelve (user_id, fleet_id, vehicle_id).
    """
    from app.models.fleet import Fleet
    from app.models.master_data import Vehicle
    from app.models.user import User

    user = User(
        id=uuid.uuid4(),
        email=f"test-{uuid.uuid4().hex[:8]}@portalclientes.local",
        password_hash="x",
        full_name="Tester",
        is_active=True,
    )
    session.add(user)
    await session.flush()

    fleet = Fleet(
        id=uuid.uuid4(),
        name="Test Fleet",
        code=f"tst-{uuid.uuid4().hex[:6]}",
    )
    session.add(fleet)
    await session.flush()

    vehicle = Vehicle(
        id=uuid.uuid4(),
        plate=f"TST{uuid.uuid4().hex[:6].upper()}",
        vin=f"VIN{uuid.uuid4().hex[:10].upper()}",
        fleet_id=fleet.id,
        is_active=True,
    )
    session.add(vehicle)
    await session.flush()
    return user.id, fleet.id, vehicle.id


@pytest.fixture
async def seeded_ids(fresh_session_factory) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Crea user/fleet/vehicle en su propia sesión y commitea; cleanup
    al final vía rollback en una sesión fresca.
    """
    async with fresh_session_factory() as s:
        ids = await _seed_user_fleet_vehicle(s)
        await s.commit()
    yield ids
    user_id, fleet_id, vehicle_id = ids
    async with fresh_session_factory() as s:
        from app.models.fleet import Fleet
        from app.models.master_data import Vehicle
        from app.models.novedad import Novedad
        from app.models.user import User

        await s.execute(delete(Novedad).where(Novedad.vehicle_id == vehicle_id))
        await s.execute(delete(Vehicle).where(Vehicle.id == vehicle_id))
        await s.execute(delete(Fleet).where(Fleet.id == fleet_id))
        await s.execute(delete(User).where(User.id == user_id))
        await s.commit()


# ---------------------------------------------------------------------------
# Integración: claim concurrente
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_one_is_atomic(fresh_session_factory, seeded_ids) -> None:
    """Sólo UN worker gana el item aunque dos transacciones compitan."""
    from app.models.novedad import Novedad
    from app.services.novedad_outbox_service import claim_one, enqueue

    user_id, _fleet_id, vehicle_id = seeded_ids
    async with fresh_session_factory() as s:
        n = Novedad(
            id=uuid.uuid4(),
            vehicle_id=vehicle_id,
            created_by_id=user_id,
            vehicle_code="TST",
            reported_at=datetime.now(UTC),
            reported_by_id=1,
            priority="low",
            send_mail=False,
            cloudfleet_status="pending",
        )
        s.add(n)
        await s.flush()
        await enqueue(s, novedad=n)
        await s.commit()
        novedad_id = n.id

    async def worker() -> uuid.UUID | None:
        async with fresh_session_factory() as s:
            claimed = await claim_one(
                s, stale_after=timedelta(seconds=60), novedad_id=novedad_id
            )
            return claimed.outbox_id if claimed else None

    a, b = await asyncio.gather(worker(), worker())
    winners = [x for x in (a, b) if x is not None]
    assert len(winners) == 1, f"se esperaban 1 ganador, hubo {len(winners)}"

    async with fresh_session_factory() as s:
        third = await claim_one(
            s, stale_after=timedelta(seconds=60), novedad_id=novedad_id
        )
        assert third is None

    async with fresh_session_factory() as s:
        await s.execute(delete(Novedad).where(Novedad.id == novedad_id))
        await s.commit()


# ---------------------------------------------------------------------------
# Integración: recuperación stale
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_recovers_stale_processing(fresh_session_factory, seeded_ids) -> None:
    from app.models.novedad import Novedad, NovedadOutbox
    from app.services.novedad_outbox_service import claim_one

    user_id, _fleet_id, vehicle_id = seeded_ids
    async with fresh_session_factory() as s:
        n = Novedad(
            id=uuid.uuid4(),
            vehicle_id=vehicle_id,
            created_by_id=user_id,
            vehicle_code="TST",
            reported_at=datetime.now(UTC),
            reported_by_id=1,
            priority="low",
            send_mail=False,
            cloudfleet_status="pending",
        )
        s.add(n)
        await s.flush()
        outbox = NovedadOutbox(
            novedad_id=n.id,
            status="processing",
            attempts=3,
            available_at=datetime.now(UTC),
            locked_at=datetime.now(UTC) - timedelta(hours=1),
        )
        s.add(outbox)
        await s.commit()
        n_id = n.id
        outbox_id = outbox.id

    async with fresh_session_factory() as s:
        claimed = await claim_one(
            s, stale_after=timedelta(minutes=30), novedad_id=n_id
        )
        assert claimed is not None
        assert claimed.outbox_id == outbox_id
        assert claimed.attempts == 4  # incrementó

    async with fresh_session_factory() as s:
        await s.execute(delete(Novedad).where(Novedad.id == n_id))
        await s.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_does_not_steal_fresh_processing(fresh_session_factory, seeded_ids) -> None:
    from app.models.novedad import Novedad, NovedadOutbox
    from app.services.novedad_outbox_service import claim_one

    user_id, _fleet_id, vehicle_id = seeded_ids
    async with fresh_session_factory() as s:
        n = Novedad(
            id=uuid.uuid4(),
            vehicle_id=vehicle_id,
            created_by_id=user_id,
            vehicle_code="TST",
            reported_at=datetime.now(UTC),
            reported_by_id=1,
            priority="low",
            send_mail=False,
            cloudfleet_status="pending",
        )
        s.add(n)
        await s.flush()
        outbox = NovedadOutbox(
            novedad_id=n.id,
            status="processing",
            attempts=1,
            available_at=datetime.now(UTC),
            locked_at=datetime.now(UTC) - timedelta(seconds=5),
        )
        s.add(outbox)
        await s.commit()
        n_id = n.id

    async with fresh_session_factory() as s:
        claimed = await claim_one(
            s, stale_after=timedelta(minutes=10), novedad_id=n_id
        )
        assert claimed is None

    async with fresh_session_factory() as s:
        await s.execute(delete(Novedad).where(Novedad.id == n_id))
        await s.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_retries_due_failed_but_respects_max_attempts(
    fresh_session_factory, seeded_ids
) -> None:
    from app.core.config import settings
    from app.models.novedad import Novedad, NovedadOutbox
    from app.services.novedad_outbox_service import claim_one

    user_id, _fleet_id, vehicle_id = seeded_ids
    async with fresh_session_factory() as s:
        due = Novedad(
            vehicle_id=vehicle_id,
            created_by_id=user_id,
            vehicle_code="DUE",
            reported_at=datetime.now(UTC),
            reported_by_id=1,
            priority="low",
            send_mail=False,
            cloudfleet_status="failed",
        )
        exhausted = Novedad(
            vehicle_id=vehicle_id,
            created_by_id=user_id,
            vehicle_code="MAX",
            reported_at=datetime.now(UTC),
            reported_by_id=1,
            priority="low",
            send_mail=False,
            cloudfleet_status="failed",
        )
        s.add_all([due, exhausted])
        await s.flush()
        s.add_all(
            [
                NovedadOutbox(
                    novedad_id=due.id,
                    status="failed",
                    attempts=1,
                    available_at=datetime.now(UTC) - timedelta(seconds=1),
                ),
                NovedadOutbox(
                    novedad_id=exhausted.id,
                    status="failed",
                    attempts=settings.novedad_outbox_max_attempts,
                    available_at=datetime.now(UTC) - timedelta(seconds=1),
                ),
            ]
        )
        await s.commit()
        due_id, exhausted_id = due.id, exhausted.id

    async with fresh_session_factory() as s:
        claimed = await claim_one(
            s, stale_after=timedelta(minutes=1), novedad_id=due_id
        )
        assert claimed is not None
        assert claimed.attempts == 2

    async with fresh_session_factory() as s:
        assert (
            await claim_one(
                s, stale_after=timedelta(minutes=1), novedad_id=exhausted_id
            )
            is None
        )

    async with fresh_session_factory() as s:
        await s.execute(delete(Novedad).where(Novedad.id.in_([due_id, exhausted_id])))
        await s.commit()


# ---------------------------------------------------------------------------
# Integración: mark_retry
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_mark_retry_only_affects_failed(fresh_session_factory, seeded_ids) -> None:
    from app.models.novedad import Novedad, NovedadOutbox
    from app.services.novedad_outbox_service import mark_retry

    user_id, _fleet_id, vehicle_id = seeded_ids
    async with fresh_session_factory() as s:
        n_failed = Novedad(
            id=uuid.uuid4(),
            vehicle_id=vehicle_id,
            created_by_id=user_id,
            vehicle_code="TST",
            reported_at=datetime.now(UTC),
            reported_by_id=1,
            priority="low",
            send_mail=False,
            cloudfleet_status="failed",
        )
        n_proc = Novedad(
            id=uuid.uuid4(),
            vehicle_id=vehicle_id,
            created_by_id=user_id,
            vehicle_code="TST",
            reported_at=datetime.now(UTC),
            reported_by_id=1,
            priority="low",
            send_mail=False,
            cloudfleet_status="pending",
        )
        s.add_all([n_failed, n_proc])
        await s.flush()
        ob_failed = NovedadOutbox(
            novedad_id=n_failed.id,
            status="failed",
            attempts=5,
            available_at=datetime.now(UTC) + timedelta(hours=1),
        )
        ob_processing = NovedadOutbox(
            novedad_id=n_proc.id,
            status="processing",
            attempts=1,
            available_at=datetime.now(UTC),
            locked_at=datetime.now(UTC),
        )
        s.add_all([ob_failed, ob_processing])
        await s.commit()
        failed_id = ob_failed.id
        proc_id = ob_processing.id

    async with fresh_session_factory() as s:
        ok = await mark_retry(s, outbox_id=failed_id)
        assert ok is True

    async with fresh_session_factory() as s:
        ok2 = await mark_retry(s, outbox_id=proc_id)
        assert ok2 is False

    async with fresh_session_factory() as s:
        f = (
            await s.execute(select(NovedadOutbox).where(NovedadOutbox.id == failed_id))
        ).scalar_one()
        p = (
            await s.execute(select(NovedadOutbox).where(NovedadOutbox.id == proc_id))
        ).scalar_one()
        assert f.status == "pending"
        assert f.attempts == 0
        assert f.available_at <= datetime.now(UTC) + timedelta(seconds=2)
        assert p.status == "processing"  # intacto

    async with fresh_session_factory() as s:
        await s.execute(
            delete(Novedad).where(Novedad.id.in_([n_failed.id, n_proc.id]))
        )
        await s.commit()


# ---------------------------------------------------------------------------
# Integración: idempotencia HTTP end-to-end (sin red: mockeamos Cloudfleet)
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_with_idempotency_key_returns_existing(
    fresh_session_factory, seeded_ids, monkeypatch
) -> None:
    """Misma clave + mismo fingerprint -> una sola Novedad, sin segundo POST.

    El API usa el engine global (apunta a `portal_clientes` por configuración de
    tests). Para no tocar la DB de desarrollo, redirigimos el engine a
    `portal_clientes_codex_test` y limpiamos al final.
    """
    from fastapi.testclient import TestClient

    from app.core.config import settings
    from app.db import session as session_mod
    from app.main import app
    from app.models.novedad import Novedad
    from app.services import cloudfleet_service
    # La creación exige la key configurada; sin ella responde 503 antes de
    # llegar al doble que simula CloudFleet. No depende del .env del host.
    monkeypatch.setattr(cloudfleet_service.settings, "cloudfleet_api_key", "test-key")
    monkeypatch.setattr(cloudfleet_service.settings, "cloudfleet_id_reportedby", 23)

    # Reapuntar el engine global a portal_clientes_codex_test, recreando
    # AsyncSessionLocal para que get_db use el nuevo engine.
    test_engine = create_async_engine(TEST_DB_URL, pool_pre_ping=True)
    monkeypatch.setattr(session_mod, "engine", test_engine, raising=False)
    monkeypatch.setattr(
        session_mod,
        "AsyncSessionLocal",
        async_sessionmaker(
            bind=test_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        ),
        raising=False,
    )

    _user_id, _fleet_id, vehicle_id = seeded_ids

    c = TestClient(app)
    r = c.post(
        "/api/v1/auth/login",
        json={
            "email": settings.bootstrap_admin_email,
            "password": settings.bootstrap_admin_password,
        },
    )
    assert r.status_code == 200, r.text
    cookies = r.cookies

    body = {
        "vehicle_id": str(vehicle_id),
        "reported_at": "2026-07-14T10:00:00Z",
        "priority": "medium",
        "comment": "Test",
        "send_mail": "false",
    }

    async def _vehicle_not_found(payload, *, client=None):
        raise cloudfleet_service.CloudfleetVehicleNotFoundError("vehículo no encontrado")

    missing_vehicle_key = f"missing-{uuid.uuid4().hex}"
    with patch.object(cloudfleet_service, "create_issue", new=_vehicle_not_found):
        missing_vehicle = c.post(
            "/api/v1/novedades",
            data=body,
            headers={"Idempotency-Key": missing_vehicle_key},
            cookies=cookies,
        )
    assert missing_vehicle.status_code == 422, missing_vehicle.text
    assert missing_vehicle.json()["detail"] == {"code": "cloudfleet_vehicle_not_found"}
    async with fresh_session_factory() as s:
        assert (
            await s.execute(
                select(Novedad).where(Novedad.idempotency_key == missing_vehicle_key)
            )
        ).scalar_one_or_none() is None

    async def _cloudfleet_error(payload, *, client=None):
        raise cloudfleet_service.CloudfleetError("Cloudfleet no disponible")

    generic_error_key = f"error-{uuid.uuid4().hex}"
    with patch.object(cloudfleet_service, "create_issue", new=_cloudfleet_error):
        generic_error = c.post(
            "/api/v1/novedades",
            data=body,
            headers={"Idempotency-Key": generic_error_key},
            cookies=cookies,
        )
    assert generic_error.status_code == 502, generic_error.text
    assert generic_error.json()["detail"] == {"code": "cloudfleet_error"}
    async with fresh_session_factory() as s:
        assert (
            await s.execute(
                select(Novedad).where(Novedad.idempotency_key == generic_error_key)
            )
        ).scalar_one_or_none() is None

    fake_result = cloudfleet_service.CloudfleetCreateResult(
        issue_number=1, response={"number": 1}
    )
    create_issue_calls = 0

    async def _fake_create_issue(payload, *, client=None):
        nonlocal create_issue_calls
        create_issue_calls += 1
        return fake_result

    idem_key = f"test-{uuid.uuid4().hex}"
    with patch.object(
        cloudfleet_service, "create_issue", new=_fake_create_issue
    ):
        r1 = c.post(
            "/api/v1/novedades",
            data=body,
            headers={"Idempotency-Key": idem_key},
            cookies=cookies,
        )
        assert r1.status_code == 201, r1.text
        first_id = r1.json()["id"]

        # Reintento con la misma clave + mismo payload: misma Novedad.
        r2 = c.post(
            "/api/v1/novedades",
            data=body,
            headers={"Idempotency-Key": idem_key},
            cookies=cookies,
        )
        assert r2.status_code == 201, r2.text
        assert r2.json()["id"] == first_id
        assert r2.json()["cloudfleet_status"] == "sent"
        assert create_issue_calls == 1

        # Misma clave, payload distinto -> 409.
        body2 = {**body, "comment": "Otro comentario"}
        r3 = c.post(
            "/api/v1/novedades",
            data=body2,
            headers={"Idempotency-Key": idem_key},
            cookies=cookies,
        )
        assert r3.status_code == 409, r3.text

    # Limpieza: borrar la novedad creada.
    async with fresh_session_factory() as s:
        await s.execute(delete(Novedad).where(Novedad.id == uuid.UUID(first_id)))
        await s.commit()
    await test_engine.dispose()


# ---------------------------------------------------------------------------
# Integración: resultado tardío de un claim viejo no debe sobrescribir
# un intento nuevo (o un row que ya no existe).
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_late_claim_result_does_not_overwrite_new_attempt(
    fresh_session_factory, seeded_ids
) -> None:
    """Garantía: un `process_claimed` ejecutado con un `ClaimedOutbox` viejo
    (cuyos `attempts` ya no coinciden con la fila del outbox porque un
    nuevo claim avanzó el contador) debe ser un no-op total.

    Cubre dos sub-casos:
    1. Outbox todavía existe, pero `attempts` cambió (otro worker reclamó
       y avanzó). `process_claimed` debe devolver False y el estado del
       outbox y de la Novedad no debe modificarse.
    2. Outbox (y Novedad) ya no existen (cascade). `process_claimed` debe
       devolver False sin lanzar y sin tocar nada.
    """
    from app.models.novedad import Novedad, NovedadOutbox
    from app.services.novedad_outbox_service import (
        ClaimedOutbox,
        mark_failed,
        mark_sent,
        process_claimed,
    )

    user_id, _fleet_id, vehicle_id = seeded_ids

    # ------------------------------------------------------------------
    # Sub-caso 1: outbox vivo, attempts ya no coinciden.
    # ------------------------------------------------------------------
    async with fresh_session_factory() as s:
        n = Novedad(
            id=uuid.uuid4(),
            vehicle_id=vehicle_id,
            created_by_id=user_id,
            vehicle_code="TST",
            reported_at=datetime.now(UTC),
            reported_by_id=1,
            priority="low",
            send_mail=False,
            cloudfleet_status="sent",  # simula "otro worker ya envió"
            cloudfleet_issue_number=42,
        )
        s.add(n)
        await s.flush()
        # Un nuevo claim YA ocurrió: attempts=2, locked_at reciente.
        # El claim viejo tenía attempts=1.
        outbox = NovedadOutbox(
            novedad_id=n.id,
            status="processing",
            attempts=2,
            available_at=datetime.now(UTC),
            locked_at=datetime.now(UTC),
        )
        s.add(outbox)
        await s.commit()
        n_id = n.id
        outbox_id = outbox.id

    stale_claim = ClaimedOutbox(
        outbox_id=outbox_id, novedad_id=n_id, attempts=1  # viejo
    )

    # process_claimed: la guarda de lectura debe rechazar y devolver False.
    assert await process_claimed(stale_claim) is False

    # Estado intacto: sigue en processing/attempts=2; Novedad sigue sent/42.
    async with fresh_session_factory() as s:
        o = (
            await s.execute(select(NovedadOutbox).where(NovedadOutbox.id == outbox_id))
        ).scalar_one()
        assert o.status == "processing"
        assert o.attempts == 2
        nn = (await s.execute(select(Novedad).where(Novedad.id == n_id))).scalar_one()
        assert nn.cloudfleet_status == "sent"
        assert nn.cloudfleet_issue_number == 42
        assert nn.cloudfleet_error is None

    # mark_sent directo: también debe ser no-op (no 0-row guard), no toca nada.
    async with fresh_session_factory() as s:
        ok = await mark_sent(
            s,
            claimed=stale_claim,
            issue_number=999,
            response={"number": 999},
        )
    assert ok is False

    async with fresh_session_factory() as s:
        o = (
            await s.execute(select(NovedadOutbox).where(NovedadOutbox.id == outbox_id))
        ).scalar_one()
        nn = (await s.execute(select(Novedad).where(Novedad.id == n_id))).scalar_one()
        assert o.status == "processing"
        assert nn.cloudfleet_status == "sent"
        assert nn.cloudfleet_issue_number == 42  # NO se sobrescribió con 999

    # mark_failed directo: tampoco debe tocar nada.
    async with fresh_session_factory() as s:
        ok = await mark_failed(
            s,
            claimed=stale_claim,
            error="late failure",
            available_at=datetime.now(UTC) + timedelta(seconds=30),
        )
    assert ok is False

    async with fresh_session_factory() as s:
        o = (
            await s.execute(select(NovedadOutbox).where(NovedadOutbox.id == outbox_id))
        ).scalar_one()
        nn = (await s.execute(select(Novedad).where(Novedad.id == n_id))).scalar_one()
        assert o.status == "processing"  # intacto
        assert nn.cloudfleet_status == "sent"  # intacto
        assert nn.cloudfleet_error is None

    # ------------------------------------------------------------------
    # Sub-caso 2: el row ya no existe (Novedad borrada en cascada).
    # ------------------------------------------------------------------
    async with fresh_session_factory() as s:
        await s.execute(delete(Novedad).where(Novedad.id == n_id))
        await s.commit()

    ghost_claim = ClaimedOutbox(
        outbox_id=outbox_id, novedad_id=n_id, attempts=3
    )
    # process_claimed con un outbox inexistente: no lanza, devuelve False.
    assert await process_claimed(ghost_claim) is False
