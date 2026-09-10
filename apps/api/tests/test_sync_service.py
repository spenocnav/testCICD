"""Tests del sync de data maestra (app/services/sync_service.py).

Estrategia: cada test corre `apply_snapshot` sobre una sesión y hace ROLLBACK al
final (apply_snapshot NO commitea). Todo queda dentro de la transacción —visible
para la misma sesión— y se descarta: no contamina la DB ni necesita cleanup.
Marcados @pytest.mark.integration porque requieren PostgreSQL.

`fetch_snapshot` se prueba con httpx.MockTransport (sin red).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.core.crypto import decrypt_secret, encrypt_secret
from app.db.session import AsyncSessionLocal
from app.services.sync_service import (
    InvalidMasterSnapshotError,
    SyncResult,
    _credential_password_enc,
    apply_snapshot,
    fetch_snapshot,
    get_watermark,
)

# source_ids/plate altos y propios para no chocar con seed/flotas reales.
_GENERATED_AT = "2026-06-19T12:00:00Z"


def _payload() -> dict[str, Any]:
    return {
        "generated_at": _GENERATED_AT,
        "since": None,
        "customers": [
            {
                "id": 990012,
                "name": "SyncTest Transportes",
                "updated_at": "2026-06-19T10:00:00Z",
                "databases": [
                    {
                        "id": 990031,
                        "database_name": "synctest_db",
                        "database_key": "synctest_db",
                        "connection_type": "geotab",
                        "provider_config": {"plate_prefix": "ST"},
                        "updated_at": "2026-06-19T10:00:00Z",
                        "credentials": [
                            {
                                "id": 990007,
                                "username": "sync@test.com",
                                "password": "secret-pw",
                                "label": "cuenta sync",
                                "is_active": True,
                                "updated_at": "2026-06-19T10:00:00Z",
                            }
                        ],
                        "rules": [
                            {
                                "id": 990101,
                                "rule_id": "R1",
                                "name": "RPM > 2200 (SYNCMOT)",
                                "category": "operacion",
                                "motor_type": "SYNCMOT",
                                "created_at": "2026-06-19T09:00:00Z",
                            },
                            {
                                "id": 990102,
                                "rule_id": "R2",
                                "name": "Frenada brusca",
                                "category": "habito_seguro",
                                "description": "Frenadas bruscas",
                                "motor_type": None,
                                "created_at": "2026-06-19T09:00:00Z",
                            },
                        ],
                    }
                ],
            }
        ],
        "vehicles": [
            {
                "plate": "SYNCT001",
                "vin": "SYNCVIN0001",
                "geotab_device_id": "d1",
                "geotab_device_synced_at": "2026-06-19T08:00:00Z",
                "customer_id": 990012,
                "customer_database_id": 990031,
                "geotab_customer_database_id": 990031,
                "geotab_customer_status": "found",
                "engine_number": "E1",
                "technical_number": "T1",
                "cpl": "1000",
                "marca": "TESTMARCA",
                "linea": "TESTLINEA",
                "marketing_model_name": "ProStar Comercial",
                "service_model_name": "ProStar Servicio",
                "ano_modelo": "2024",
                "tipo_combustible": "DIESEL",
                "nombre_vehiculo": "SYNCT001 - TESTLINEA",
                "motor_type": "SYNCMOT",
                "vocacional": True,
                "category": "Flota Administrada",
                "updated_at": "2026-06-19T08:00:00Z",
            }
        ],
    }


# ---------------------------------------------------------------------------
# Unit — sin DB ni red
# ---------------------------------------------------------------------------
def test_parse_dt() -> None:
    from app.services.sync_service import _parse_dt

    assert _parse_dt(None) is None
    dt = _parse_dt("2026-06-19T12:00:00Z")
    assert dt is not None
    assert dt.year == 2026 and dt.hour == 12
    assert dt.tzinfo is not None


def test_normalize_geotab_identity_is_conservative() -> None:
    from app.services.sync_service import _normalize_geotab_identity

    assert _normalize_geotab_identity(
        {"geotab_device_id": "  device-1  ", "geotab_customer_status": " FOUND "}
    ) == ("device-1", "found")
    assert _normalize_geotab_identity(
        {"geotab_device_id": "   ", "geotab_customer_status": "resolved"}
    ) == (None, "unknown")
    assert _normalize_geotab_identity(
        {"geotab_device_id": None, "geotab_customer_status": " NOT_APPLICABLE "}
    ) == (None, "not_applicable")
    assert _normalize_geotab_identity(
        {"geotab_device_id": "device-3", "geotab_customer_status": "disconnected"}
    ) == ("device-3", "found")


@pytest.mark.asyncio
async def test_fetch_snapshot_sends_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "navi_api_key", "test-key-123")
    monkeypatch.setattr(settings, "navi_base_url", "http://navi.test")

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("X-API-Key")
        return httpx.Response(200, json={"generated_at": _GENERATED_AT, "customers": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    data = await fetch_snapshot(since="2026-06-18T00:00:00Z", client=client)
    await client.aclose()

    assert data["generated_at"] == _GENERATED_AT
    assert captured["api_key"] == "test-key-123"
    assert "/api/v1/integration/snapshot" in captured["url"]
    assert "since=2026-06-18" in captured["url"]
    assert "include_credentials=true" in captured["url"]


@pytest.mark.asyncio
async def test_fetch_snapshot_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "navi_api_key", None)
    with pytest.raises(RuntimeError, match="NAVI_API_KEY"):
        await fetch_snapshot()


# ---------------------------------------------------------------------------
# Integration — apply_snapshot contra PostgreSQL (rollback al final)
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_apply_snapshot_upsert() -> None:
    async with AsyncSessionLocal() as s:
        try:
            res = await apply_snapshot(s, _payload(), full=False)
            assert isinstance(res, SyncResult)
            assert (res.fleets, res.databases, res.credentials, res.rules, res.vehicles) == (
                1,
                1,
                1,
                2,
                1,
            )

            # Flota con code derivado del source_id.
            code = (
                await s.execute(text("SELECT code FROM fleets WHERE source_id = 990012"))
            ).scalar_one()
            assert code == "navi-990012"

            # Vehículo: vocacional + categoría + resolución de fleet/db por source_id.
            row = (
                await s.execute(
                    text(
                        "SELECT marketing_model_name, service_model_name, vocacional, category, "
                        "cpl, is_active, geotab_customer_status, "
                        "fleet_id, geotab_database_id "
                        "FROM vehicles WHERE plate = 'SYNCT001'"
                    )
                )
            ).one()
            assert row.marketing_model_name == "ProStar Comercial"
            assert row.service_model_name == "ProStar Servicio"
            assert row.cpl == "1000"
            assert row.vocacional is True
            # Categoría gestionada -> activo.
            assert row.category == "Flota Administrada"
            assert row.is_active is True
            assert row.geotab_customer_status == "found"
            assert row.fleet_id is not None and row.geotab_database_id is not None

            # Credencial cifrada y descifrable.
            enc = (
                await s.execute(
                    text("SELECT password_enc FROM geotab_credentials WHERE source_id = 990007")
                )
            ).scalar_one()
            assert decrypt_secret(bytes(enc)) == "secret-pw"

            # Motor auto-creado en el catálogo (FK).
            assert (
                await s.execute(text("SELECT 1 FROM motor_catalog WHERE motor_type = 'SYNCMOT'"))
            ).scalar_one_or_none() == 1

            # Watermark = generated_at de la respuesta.
            wm = await get_watermark(s)
            assert wm is not None and wm.startswith("2026-06-19T12:00:00")
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_apply_snapshot_idempotent() -> None:
    async with AsyncSessionLocal() as s:
        try:
            await apply_snapshot(s, _payload(), full=False)
            await apply_snapshot(s, _payload(), full=False)  # segunda corrida

            for table, sid in [
                ("fleets", 990012),
                ("geotab_databases", 990031),
                ("geotab_credentials", 990007),
            ]:
                n = (
                    await s.execute(
                        text(f"SELECT count(*) FROM {table} WHERE source_id = :sid"),
                        {"sid": sid},
                    )
                ).scalar_one()
                assert n == 1, f"{table} duplicó en segunda corrida"

            n_veh = (
                await s.execute(text("SELECT count(*) FROM vehicles WHERE plate = 'SYNCT001'"))
            ).scalar_one()
            assert n_veh == 1
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_apply_snapshot_updates_changed_field() -> None:
    async with AsyncSessionLocal() as s:
        try:
            await apply_snapshot(s, _payload(), full=False)
            data = _payload()
            data["customers"][0]["name"] = "SyncTest RENAMED"
            await apply_snapshot(s, data, full=False)

            name = (
                await s.execute(text("SELECT name FROM fleets WHERE source_id = 990012"))
            ).scalar_one()
            assert name == "SyncTest RENAMED"
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_category_ninguna_marks_vehicle_inactive() -> None:
    # Un vehículo sacado de un cliente gestionado (categoría 'Ninguna') se trae
    # igual pero queda inactivo; volver a una categoría gestionada lo reactiva.
    async with AsyncSessionLocal() as s:
        try:
            data = _payload()
            data["vehicles"][0]["category"] = "Ninguna"
            await apply_snapshot(s, data, full=False)
            row = (
                await s.execute(
                    text("SELECT category, is_active FROM vehicles WHERE plate = 'SYNCT001'")
                )
            ).one()
            assert row.category == "Ninguna"
            assert row.is_active is False

            fleet_active = (
                await s.execute(text("SELECT is_active FROM fleets WHERE source_id = 990012"))
            ).scalar_one()
            assert fleet_active is False

            # Reasignado a una categoría gestionada -> vuelve a activo.
            data["vehicles"][0]["category"] = "Experiencia Superior"
            await apply_snapshot(s, data, full=False)
            row = (
                await s.execute(
                    text("SELECT category, is_active FROM vehicles WHERE plate = 'SYNCT001'")
                )
            ).one()
            assert row.category == "Experiencia Superior"
            assert row.is_active is True

            fleet_active = (
                await s.execute(text("SELECT is_active FROM fleets WHERE source_id = 990012"))
            ).scalar_one()
            assert fleet_active is True
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_same_rule_id_can_sync_as_operation_and_habito() -> None:
    async with AsyncSessionLocal() as s:
        try:
            data = _payload()
            data["customers"][0]["databases"][0]["rules"][0].update(
                {
                    "rule_source_id": 990100,
                    "application_id": 990101,
                    "event_type": None,
                    "band": "exceso_rpm",
                    "is_descenso": False,
                    "description": None,
                }
            )
            data["customers"][0]["databases"][0]["rules"].append(
                {
                    "id": 990100,
                    "rule_source_id": 990100,
                    "application_id": 990103,
                    "rule_id": "R1",
                    "name": "RPM > 2200 (SYNCMOT)",
                    "category": "habito_seguro",
                    "event_type": "exceso_rpm",
                    "motor_type": "SYNCMOT",
                    "band": None,
                    "is_descenso": False,
                    "description": "Excesos de RPM",
                    "created_at": "2026-06-19T09:00:00Z",
                }
            )

            await apply_snapshot(s, data, full=False)

            physical_rules = (
                await s.execute(text("SELECT count(*) FROM geotab_rules WHERE rule_id = 'R1'"))
            ).scalar_one()
            assert physical_rules == 1
            rule_source_id = (
                await s.execute(text("SELECT source_id FROM geotab_rules WHERE rule_id = 'R1'"))
            ).scalar_one()
            assert rule_source_id == 990100

            categories = (
                await s.execute(
                    text(
                        "SELECT app.source_id, app.category, app.event_type, app.motor_type, "
                        "app.band, app.is_descenso, app.description "
                        "FROM geotab_rule_applications app "
                        "JOIN geotab_rules r ON r.id = app.geotab_rule_id "
                        "WHERE r.rule_id = 'R1' "
                        "ORDER BY app.category"
                    )
                )
            ).all()
            assert [row.category for row in categories] == ["habito_seguro", "operacion"]
            assert {row.source_id for row in categories} == {990101, 990103}
            assert categories[0].event_type == "exceso_rpm"
            assert categories[0].motor_type == "SYNCMOT"
            assert categories[0].band is None
            assert categories[0].is_descenso is False
            assert categories[0].description == "Excesos de RPM"
            assert categories[1].event_type is None
            assert categories[1].motor_type == "SYNCMOT"
            assert categories[1].band == "exceso_rpm"
            assert categories[1].is_descenso is False
            assert categories[1].description is None
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_sync_deactivates_removed_derived_rpm_application() -> None:
    async with AsyncSessionLocal() as s:
        try:
            data = _payload()
            operation = data["customers"][0]["databases"][0]["rules"][0]
            operation.update(
                {
                    "rule_source_id": 990100,
                    "application_id": 990101,
                    "band": "exceso_rpm",
                    "is_descenso": False,
                }
            )
            data["customers"][0]["databases"][0]["rules"].append(
                {
                    "rule_source_id": 990100,
                    "application_id": 990103,
                    "rule_id": "R1",
                    "name": operation["name"],
                    "category": "habito_seguro",
                    "event_type": "exceso_rpm",
                    "motor_type": "SYNCMOT",
                    "band": None,
                    "is_descenso": False,
                    "description": "Excesos de RPM",
                }
            )
            await apply_snapshot(s, data, full=True)

            data["customers"][0]["databases"][0]["rules"] = [
                rule
                for rule in data["customers"][0]["databases"][0]["rules"]
                if rule.get("application_id") != 990103
            ]
            await apply_snapshot(s, data, full=True)

            states = dict(
                (
                    await s.execute(
                        text(
                            "SELECT source_id, is_active "
                            "FROM geotab_rule_applications "
                            "WHERE source_id IN (990101, 990103)"
                        )
                    )
                ).all()
            )
            assert states == {990101: True, 990103: False}
        finally:
            await s.rollback()


def test_rule_applications_prefers_stable_application_identity() -> None:
    from app.services.sync_service import _rule_applications

    flat = _rule_applications(
        {
            "id": 10,
            "rule_source_id": 20,
            "application_id": 30,
            "source_id": 40,
            "category": "habito_seguro",
        }
    )
    assert flat[0]["source_id"] == 30

    nested = _rule_applications(
        {
            "id": 20,
            "applications": [
                {"id": 31, "source_id": 32, "category": "operacion"},
                {"id": 33, "application_id": 34, "category": "habito_seguro"},
            ],
        }
    )
    assert [application["source_id"] for application in nested] == [32, 34]


def test_rule_applications_preserves_missing_motor_for_validation() -> None:
    """El parser no inventa un motor: la validación debe rechazar el NULL."""
    from app.services.sync_service import _rule_applications

    applications = _rule_applications(
        {
            "id": 407,
            "category": "operacion",
            "motor_type": None,
            "band": "rango_bajo",
        }
    )

    assert applications == [
        {
            "source_id": 407,
            "category": "operacion",
            "event_type": None,
            "motor_type": None,
            "band": "rango_bajo",
            "is_descenso": None,
            "description": None,
            "is_active": True,
        }
    ]


def test_snapshot_rejects_operation_without_motor_and_identifies_source() -> None:
    from app.services.sync_service import (
        InvalidMasterSnapshotError,
        _validate_rule_application_scopes,
    )

    data = _payload()
    operation = data["customers"][0]["databases"][0]["rules"][0]
    operation.update(
        {
            "id": 407,
            "name": "Rango Bajo Navitrans X13",
            "rule_id": "aMHdo5IqUFky2GwwM5gQMUQ",
            "motor_type": None,
            "band": "rango_bajo",
        }
    )

    with pytest.raises(
        InvalidMasterSnapshotError,
        match="application_id=407",
    ):
        _validate_rule_application_scopes(data)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_two_credentials_per_database_are_both_replicated() -> None:
    """Un pool de N credenciales por base llega entero; ninguna colapsa a otra.

    Correcto por construcción (`_upsert_credential` casa por source_id y luego
    por (base, usuario)), pero nada lo fijaba: toda fixture traía una sola.
    """
    async with AsyncSessionLocal() as s:
        try:
            data = _payload()
            creds = data["customers"][0]["databases"][0]["credentials"]
            creds.append(
                {
                    "id": 990008,
                    "username": "sync2@test.com",
                    "password": "secret-pw-2",
                    "label": "cuenta secundaria",
                    "is_active": True,
                    "updated_at": "2026-06-19T10:00:00Z",
                }
            )
            res = await apply_snapshot(s, data, full=True)
            assert res.credentials == 2

            rows = (
                await s.execute(
                    text(
                        "SELECT gc.source_id, gc.username, gc.password_enc, gc.is_active "
                        "FROM geotab_credentials gc "
                        "JOIN geotab_databases gd ON gd.id = gc.geotab_database_id "
                        "WHERE gd.source_id = 990031 ORDER BY gc.source_id"
                    )
                )
            ).all()
            assert [(r.source_id, r.username, r.is_active) for r in rows] == [
                (990007, "sync@test.com", True),
                (990008, "sync2@test.com", True),
            ]
            assert [decrypt_secret(bytes(r.password_enc)) for r in rows] == [
                "secret-pw",
                "secret-pw-2",
            ]

            # Incremental sin la clave `credentials`: la ausencia no es baja.
            data_inc = _payload()
            data_inc["customers"][0]["databases"][0].pop("credentials")
            await apply_snapshot(s, data_inc, full=False)
            active = (
                await s.execute(
                    text(
                        "SELECT count(*) FROM geotab_credentials "
                        "WHERE source_id IN (990007, 990008) AND is_active"
                    )
                )
            ).scalar_one()
            assert active == 2

            # Full sync donde la segunda dejó de venir: se desactiva, no se borra.
            await apply_snapshot(s, _payload(), full=True)
            rows = (
                await s.execute(
                    text(
                        "SELECT source_id, is_active FROM geotab_credentials "
                        "WHERE source_id IN (990007, 990008) ORDER BY source_id"
                    )
                )
            ).all()
            assert [(r.source_id, r.is_active) for r in rows] == [
                (990007, True),
                (990008, False),
            ]
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_new_credential_without_secret_is_not_counted() -> None:
    """Una credencial nueva sin `password_enc` no se puede crear y no se cuenta."""
    async with AsyncSessionLocal() as s:
        try:
            data = _payload()
            data["customers"][0]["databases"][0]["credentials"].append(
                {
                    "id": 990009,
                    "username": "nosecret@test.com",
                    "password": "********",
                    "label": None,
                    "is_active": True,
                    "updated_at": "2026-06-19T10:00:00Z",
                }
            )
            res = await apply_snapshot(s, data, full=False)
            assert res.credentials == 1
            missing = (
                await s.execute(
                    text("SELECT count(*) FROM geotab_credentials WHERE source_id = 990009")
                )
            ).scalar_one()
            assert missing == 0
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_sync_deactivates_missing() -> None:
    async with AsyncSessionLocal() as s:
        try:
            # Réplica que NO viene en el snapshot (debe quedar is_active=false).
            await s.execute(
                text(
                    "INSERT INTO fleets (id, source_id, code, name, is_active, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), 990099, 'navi-990099', 'Vieja', true, now(), now())"
                )
            )
            await apply_snapshot(s, _payload(), full=True)

            gone = (
                await s.execute(text("SELECT is_active FROM fleets WHERE source_id = 990099"))
            ).scalar_one()
            assert gone is False

            kept = (
                await s.execute(text("SELECT is_active FROM fleets WHERE source_id = 990012"))
            ).scalar_one()
            assert kept is True
        finally:
            await s.rollback()


# ---------------------------------------------------------------------------
# Bandas de RPM declaradas (band / is_descenso)
# ---------------------------------------------------------------------------
def test_normalize_band_defaults_when_absent() -> None:
    """Snapshot viejo (sin los campos) no debe fallar: NULL/false."""
    from app.services.sync_service import _normalize_band

    assert _normalize_band({"id": 1, "category": "operacion"}) == (None, False)


def test_normalize_band_normalizes_case_and_spaces() -> None:
    from app.services.sync_service import _normalize_band

    assert _normalize_band({"band": "  Rango_Economico ", "is_descenso": True}) == (
        "rango_economico",
        True,
    )


def test_normalize_band_rejects_unknown_value() -> None:
    """Un valor fuera del enum degrada a NULL en vez de abortar el sync entero."""
    from app.services.sync_service import _normalize_band

    assert _normalize_band({"band": "rango_inventado", "is_descenso": True}) == (None, False)


def test_normalize_band_forces_invalid_combinations() -> None:
    from app.services.sync_service import _normalize_band

    # Descenso sin banda no es interpretable (y la DB lo rechaza).
    assert _normalize_band({"is_descenso": True}) == (None, False)
    # Ralentí no se mide en descenso.
    assert _normalize_band({"band": "ralenti", "is_descenso": True}) == ("ralenti", False)


def test_normalize_safe_habit_description() -> None:
    from app.services.sync_service import _normalize_safe_habit_description

    assert (
        _normalize_safe_habit_description(
            {"category": "habito_seguro", "description": "  frenadas   BRUSCAS "}
        )
        == "Frenadas bruscas"
    )
    assert (
        _normalize_safe_habit_description(
            {"category": "operacion", "description": "Frenadas bruscas"}
        )
        is None
    )
    assert (
        _normalize_safe_habit_description(
            {"category": "habito_seguro", "description": "Categoría desconocida"}
        )
        is None
    )
    assert (
        _normalize_safe_habit_description(
            {
                "category": "habito_seguro",
                "event_type": "exceso_rpm",
                "description": None,
            }
        )
        == "Excesos de RPM"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_apply_snapshot_persists_band_and_descenso() -> None:
    async with AsyncSessionLocal() as s:
        try:
            data = _payload()
            data["customers"][0]["databases"][0]["rules"][0].update(
                {"band": "rango_economico", "is_descenso": True}
            )
            await apply_snapshot(s, data, full=False)

            row = (
                await s.execute(
                    text(
                        "SELECT app.band, app.is_descenso "
                        "FROM geotab_rule_applications app "
                        "JOIN geotab_rules r ON r.id = app.geotab_rule_id "
                        "WHERE r.rule_id = 'R1'"
                    )
                )
            ).one()
            assert row.band == "rango_economico"
            assert row.is_descenso is True
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_apply_snapshot_persists_safe_habit_description() -> None:
    async with AsyncSessionLocal() as s:
        try:
            await apply_snapshot(s, _payload(), full=False)

            row = (
                await s.execute(
                    text(
                        "SELECT app.description, app.band, app.is_descenso "
                        "FROM geotab_rule_applications app "
                        "JOIN geotab_rules r ON r.id = app.geotab_rule_id "
                        "WHERE r.rule_id = 'R2'"
                    )
                )
            ).one()
            assert row.description == "Frenadas bruscas"
            assert row.band is None
            assert row.is_descenso is False
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_apply_snapshot_tolerates_snapshot_without_band() -> None:
    """Navi Vehículos viejo (sin band/is_descenso): no falla, deja NULL/false."""
    async with AsyncSessionLocal() as s:
        try:
            data = _payload()
            assert "band" not in data["customers"][0]["databases"][0]["rules"][0]
            await apply_snapshot(s, data, full=False)

            row = (
                await s.execute(
                    text(
                        "SELECT app.band, app.is_descenso "
                        "FROM geotab_rule_applications app "
                        "JOIN geotab_rules r ON r.id = app.geotab_rule_id "
                        "WHERE r.rule_id = 'R1'"
                    )
                )
            ).one()
            assert row.band is None
            assert row.is_descenso is False
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_resync_updates_band_when_origin_changes() -> None:
    async with AsyncSessionLocal() as s:
        try:
            data = _payload()
            data["customers"][0]["databases"][0]["rules"][0].update(
                {"band": "rango_bajo", "is_descenso": False}
            )
            await apply_snapshot(s, data, full=False)

            data["customers"][0]["databases"][0]["rules"][0].update(
                {"band": "rango_potencia_ineficiente", "is_descenso": True}
            )
            await apply_snapshot(s, data, full=False)

            rows = (
                await s.execute(
                    text(
                        "SELECT app.band, app.is_descenso "
                        "FROM geotab_rule_applications app "
                        "JOIN geotab_rules r ON r.id = app.geotab_rule_id "
                        "WHERE r.rule_id = 'R1'"
                    )
                )
            ).all()
            assert len(rows) == 1, "el re-sync duplicó la aplicación"
            assert rows[0].band == "rango_potencia_ineficiente"
            assert rows[0].is_descenso is True
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_apply_snapshot_degrades_unknown_band_to_null() -> None:
    async with AsyncSessionLocal() as s:
        try:
            data = _payload()
            data["customers"][0]["databases"][0]["rules"][0].update(
                {"band": "rango_que_no_existe", "is_descenso": True}
            )
            await apply_snapshot(s, data, full=False)

            row = (
                await s.execute(
                    text(
                        "SELECT app.band, app.is_descenso "
                        "FROM geotab_rule_applications app "
                        "JOIN geotab_rules r ON r.id = app.geotab_rule_id "
                        "WHERE r.rule_id = 'R1'"
                    )
                )
            ).one()
            assert row.band is None
            assert row.is_descenso is False
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("band", "is_descenso", "constraint"),
    [
        ("rango_que_no_existe", False, "ck_geotab_rule_app_band"),
        ("ralenti", True, "ck_geotab_rule_app_ralenti_no_descenso"),
        (None, True, "ck_geotab_rule_app_descenso_needs_band"),
    ],
)
async def test_db_checks_reject_invalid_band_combinations(
    band: str | None, is_descenso: bool, constraint: str
) -> None:
    """Las guardas viven en la DB, no solo en el servicio."""
    from sqlalchemy.exc import IntegrityError

    async with AsyncSessionLocal() as s:
        try:
            await apply_snapshot(s, _payload(), full=False)
            rule_uuid = (
                await s.execute(text("SELECT id FROM geotab_rules WHERE rule_id = 'R1'"))
            ).scalar_one()
            # Motor propio para que falle el CHECK de banda y NO el índice único
            # de (regla, categoría, motor) que ya ocupa la aplicación del payload.
            await s.execute(
                text(
                    "INSERT INTO motor_catalog (motor_type, created_at, updated_at) "
                    "VALUES ('SYNCMOT_BAND', now(), now()) ON CONFLICT DO NOTHING"
                )
            )

            with pytest.raises(IntegrityError, match=constraint):
                await s.execute(
                    text(
                        "INSERT INTO geotab_rule_applications "
                        "(id, geotab_rule_id, category, motor_type, band, is_descenso, "
                        "is_active, created_at, updated_at) "
                        "VALUES (gen_random_uuid(), :rule, 'operacion', 'SYNCMOT_BAND', :band, "
                        ":descenso, true, now(), now())"
                    ),
                    {"rule": rule_uuid, "band": band, "descenso": is_descenso},
                )
        finally:
            await s.rollback()


# ---------------------------------------------------------------------------
# range_mode + rangos de RPM por motor (contrato §2.5/§2.6)
# ---------------------------------------------------------------------------
def _rpm_bands(*, exceso_max: int | None = None) -> list[dict[str, Any]]:
    return [
        {"band": "rango_bajo", "rpm_min": 600, "rpm_max": 1100},
        {"band": "rango_economico", "rpm_min": 1100, "rpm_max": 1450},
        {"band": "rango_balanceado", "rpm_min": 1450, "rpm_max": 1800},
        {"band": "rango_potencia", "rpm_min": 1800, "rpm_max": 2300},
        {"band": "rango_potencia_ineficiente", "rpm_min": 2300, "rpm_max": 2750},
        {"band": "exceso_rpm", "rpm_min": 2750, "rpm_max": exceso_max},
    ]


async def _stored_bands(session) -> list[tuple[str, int, int | None]]:
    rows = (
        await session.execute(
            text(
                "SELECT band, rpm_min, rpm_max FROM motor_rpm_bands "
                "WHERE motor_type = 'SYNCMOT' ORDER BY rpm_min"
            )
        )
    ).all()
    return [(row.band, row.rpm_min, row.rpm_max) for row in rows]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_apply_snapshot_defaults_range_mode_to_reglas() -> None:
    """Un payload sin `range_mode` no puede cambiar el comportamiento actual."""
    async with AsyncSessionLocal() as s:
        try:
            await apply_snapshot(s, _payload(), full=False)
            mode = (
                await s.execute(
                    text("SELECT range_mode FROM fleets WHERE source_id = 990012")
                )
            ).scalar_one()
            assert mode == "reglas"
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_apply_snapshot_persists_range_mode_rpm() -> None:
    payload = _payload()
    payload["customers"][0]["range_mode"] = "rpm"
    async with AsyncSessionLocal() as s:
        try:
            await apply_snapshot(s, payload, full=False)
            mode = (
                await s.execute(
                    text("SELECT range_mode FROM fleets WHERE source_id = 990012")
                )
            ).scalar_one()
            assert mode == "rpm"

            # Volver a 'reglas' debe revertirlo en el siguiente sync.
            payload["customers"][0]["range_mode"] = "reglas"
            await apply_snapshot(s, payload, full=False)
            mode = (
                await s.execute(
                    text("SELECT range_mode FROM fleets WHERE source_id = 990012")
                )
            ).scalar_one()
            assert mode == "reglas"
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_apply_snapshot_unknown_range_mode_falls_back() -> None:
    payload = _payload()
    payload["customers"][0]["range_mode"] = "otro"
    async with AsyncSessionLocal() as s:
        try:
            await apply_snapshot(s, payload, full=False)
            mode = (
                await s.execute(
                    text("SELECT range_mode FROM fleets WHERE source_id = 990012")
                )
            ).scalar_one()
            assert mode == "reglas"
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_apply_snapshot_persists_motor_rpm_bands() -> None:
    payload = _payload()
    payload["motors"] = [{"motor_type": "SYNCMOT", "rpm_bands": _rpm_bands()}]
    async with AsyncSessionLocal() as s:
        try:
            res = await apply_snapshot(s, payload, full=False)
            assert res.motor_rpm_bands == 6
            bands = await _stored_bands(s)
            assert bands[0] == ("rango_bajo", 600, 1100)
            assert bands[-1] == ("exceso_rpm", 2750, None)

            # La configuración se reemplaza entera, no se mergea fila a fila.
            payload["motors"] = [
                {"motor_type": "SYNCMOT", "rpm_bands": _rpm_bands(exceso_max=4000)}
            ]
            await apply_snapshot(s, payload, full=False)
            bands = await _stored_bands(s)
            assert len(bands) == 6
            assert bands[-1] == ("exceso_rpm", 2750, 4000)
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_apply_snapshot_clears_motor_rpm_bands() -> None:
    payload = _payload()
    payload["motors"] = [{"motor_type": "SYNCMOT", "rpm_bands": _rpm_bands()}]
    async with AsyncSessionLocal() as s:
        try:
            await apply_snapshot(s, payload, full=False)
            payload["motors"] = [{"motor_type": "SYNCMOT", "rpm_bands": []}]
            res = await apply_snapshot(s, payload, full=False)
            assert res.motor_rpm_bands == 0
            assert await _stored_bands(s) == []
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda bands: bands[:3], id="incompleta"),
        pytest.param(
            lambda bands: [
                {**band, "rpm_min": 1200} if band["band"] == "rango_economico" else band
                for band in bands
            ],
            id="con-hueco",
        ),
        pytest.param(
            lambda bands: [
                {**band, "rpm_max": 1200} if band["band"] == "rango_bajo" else band
                for band in bands
            ],
            id="con-solape",
        ),
        pytest.param(
            lambda bands: [
                {**band, "rpm_max": None} if band["band"] == "rango_balanceado" else band
                for band in bands
            ],
            id="banda-intermedia-abierta",
        ),
        pytest.param(
            lambda bands: [
                {**band, "band": "ralenti"} if band["band"] == "rango_bajo" else band
                for band in bands
            ],
            id="banda-desconocida",
        ),
    ],
)
async def test_apply_snapshot_rejects_broken_rpm_bands(mutate) -> None:
    """Una partición rota deja el motor SIN configurar; no se guarda a medias.

    Es preferible que el ETL se salte esos vehículos (y salga la alerta de
    calidad de datos) a que reparta tiempo con cortes inconsistentes.
    """
    payload = _payload()
    payload["motors"] = [{"motor_type": "SYNCMOT", "rpm_bands": mutate(_rpm_bands())}]
    async with AsyncSessionLocal() as s:
        try:
            res = await apply_snapshot(s, payload, full=False)
            assert res.motor_rpm_bands == 0
            assert await _stored_bands(s) == []
        finally:
            await s.rollback()


# ---------------------------------------------------------------------------
# Velocidades de placa por motor (contrato §2.4)
# ---------------------------------------------------------------------------
async def _stored_speeds(session) -> tuple[int | None, int | None]:
    row = (
        await session.execute(
            text(
                "SELECT governed_speed_rpm, max_overspeed_rpm FROM motor_catalog "
                "WHERE motor_type = 'SYNCMOT'"
            )
        )
    ).one()
    return row.governed_speed_rpm, row.max_overspeed_rpm


@pytest.mark.integration
@pytest.mark.asyncio
async def test_apply_snapshot_persists_motor_speeds() -> None:
    payload = _payload()
    payload["motors"] = [
        {
            "motor_type": "SYNCMOT",
            "governed_speed_rpm": 2100,
            "max_overspeed_rpm": 2250,
            "rpm_bands": [],
        }
    ]
    async with AsyncSessionLocal() as s:
        try:
            res = await apply_snapshot(s, payload, full=False)
            assert res.motor_speeds == 1
            assert await _stored_speeds(s) == (2100, 2250)

            # La fuente es autoritativa: borrar allá borra acá.
            payload["motors"] = [{"motor_type": "SYNCMOT", "rpm_bands": []}]
            res = await apply_snapshot(s, payload, full=False)
            assert res.motor_speeds == 0
            assert await _stored_speeds(s) == (None, None)
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_apply_snapshot_accepts_partial_motor_speeds() -> None:
    """Capturar una sola de las dos es válido: la otra sigue "sin capturar"."""
    payload = _payload()
    payload["motors"] = [
        {"motor_type": "SYNCMOT", "governed_speed_rpm": 2100, "rpm_bands": []}
    ]
    async with AsyncSessionLocal() as s:
        try:
            res = await apply_snapshot(s, payload, full=False)
            assert res.motor_speeds == 1
            assert await _stored_speeds(s) == (2100, None)
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "speeds",
    [
        pytest.param({"governed_speed_rpm": 2100, "max_overspeed_rpm": 1900}, id="invertidas"),
        pytest.param({"governed_speed_rpm": 0}, id="cero"),
        pytest.param({"max_overspeed_rpm": -5}, id="negativa"),
        pytest.param({"governed_speed_rpm": "dos mil"}, id="no-numerica"),
    ],
)
async def test_apply_snapshot_rejects_broken_motor_speeds(speeds) -> None:
    """Un par inconsistente deja el motor SIN capturar, y no tumba el sync."""
    payload = _payload()
    payload["motors"] = [{"motor_type": "SYNCMOT", "rpm_bands": [], **speeds}]
    async with AsyncSessionLocal() as s:
        try:
            res = await apply_snapshot(s, payload, full=False)
            assert res.motor_speeds == 0
            assert await _stored_speeds(s) == (None, None)
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_apply_snapshot_without_motor_speed_keys_leaves_them_null() -> None:
    """Payload viejo (sin las claves): el motor queda sin capturar, sin error."""
    payload = _payload()
    payload["motors"] = [{"motor_type": "SYNCMOT", "rpm_bands": _rpm_bands()}]
    async with AsyncSessionLocal() as s:
        try:
            res = await apply_snapshot(s, payload, full=False)
            assert res.motor_rpm_bands == 6
            assert res.motor_speeds == 0
            assert await _stored_speeds(s) == (None, None)
        finally:
            await s.rollback()


# ---------------------------------------------------------------------------
# Credenciales: el token viaja cifrado y se almacena tal cual
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_password_enc_se_almacena_tal_cual_sin_recifrar() -> None:
    """El token de Navi se guarda byte a byte, no se descifra y vuelve a cifrar.

    Es lo que permite que la contraseña en claro no exista en el tránsito. La
    comparación es contra los bytes exactos enviados: comprobar sólo que
    descifra al secreto correcto pasaría igual con un re-cifrado, que es
    justamente lo que este cambio elimina.
    """
    token = encrypt_secret("secret-pw")
    payload = _payload()
    credential = payload["customers"][0]["databases"][0]["credentials"][0]
    credential["password"] = "********"
    credential["password_enc"] = token.decode("ascii")

    async with AsyncSessionLocal() as s:
        try:
            await apply_snapshot(s, payload, full=True)
            stored = (
                await s.execute(
                    text("SELECT password_enc FROM geotab_credentials WHERE source_id = 990007")
                )
            ).scalar_one()
            assert bytes(stored) == token
            assert decrypt_secret(bytes(stored)) == "secret-pw"
        finally:
            await s.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_password_enc_indescifrable_aborta_el_sync() -> None:
    """Un token de otra clave no se guarda: se aborta nombrando la causa.

    Guardarlo dejaría al ETL sin poder autenticar contra geotab, y el síntoma
    aparecería en la extracción del día siguiente sin nada que lo ligue a este
    sync.
    """
    from cryptography.fernet import Fernet

    ajena = Fernet(Fernet.generate_key()).encrypt(b"secret-pw").decode("ascii")
    payload = _payload()
    credential = payload["customers"][0]["databases"][0]["credentials"][0]
    credential["password"] = "********"
    credential["password_enc"] = ajena

    async with AsyncSessionLocal() as s:
        try:
            with pytest.raises(InvalidMasterSnapshotError, match="MASTER_FERNET_KEY"):
                await apply_snapshot(s, payload, full=True)
        finally:
            await s.rollback()


def test_password_enc_gana_sobre_password() -> None:
    """Con los dos campos presentes manda el token, no el texto.

    El snapshot al día manda `password` enmascarado junto al token; si el orden
    se invirtiera, la máscara se guardaría cifrada como si fuera el secreto.
    """
    token = encrypt_secret("el-bueno")
    resuelto = _credential_password_enc(
        {"id": 1, "username": "u", "password": "el-malo", "password_enc": token.decode("ascii")}
    )
    assert resuelto == token
    assert decrypt_secret(resuelto) == "el-bueno"


def test_sin_token_ni_password_no_toca_el_secreto_guardado() -> None:
    """La máscara no es un secreto: devuelve None y el upsert deja lo que había."""
    assert _credential_password_enc({"id": 1, "username": "u", "password": "********"}) is None
    assert _credential_password_enc({"id": 1, "username": "u"}) is None


def test_password_en_claro_sigue_funcionando() -> None:
    """Camino de compatibilidad: un snapshot anterior a `password_enc` se cifra aquí."""
    resuelto = _credential_password_enc({"id": 1, "username": "u", "password": "legacy-pw"})
    assert resuelto is not None
    assert decrypt_secret(resuelto) == "legacy-pw"
