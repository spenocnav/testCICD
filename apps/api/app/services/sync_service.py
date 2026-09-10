"""Sync de data maestra desde Navi Vehículos (contrato de integración).

Navi Vehículos es la fuente de verdad; Portal Clientes mantiene una réplica de
solo lectura. Este servicio consume `GET /api/v1/integration/snapshot` y hace
upsert idempotente por `source_id` (vehículos por `plate`).

Diseño en dos piezas para poder testear sin red:
- `fetch_snapshot(...)`  -> pega a Navi por HTTP (httpx).
- `apply_snapshot(db, data, full)` -> upsert puro sobre una sesión; lo que
  ejercitan los tests con un payload de ejemplo.
`run_sync(full)` orquesta: lee watermark, fetch, apply, commit, set watermark.

Ver contrato en Docs/contrato-intrgracion-portal-clientes.md.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.db.advisory_lock import OperationAlreadyRunningError, session_advisory_lock
from app.db.session import AsyncSessionLocal, engine
from app.models.fleet import FLEET_RANGE_MODES
from app.models.master_data import (
    GEOTAB_RULE_BANDS,
    GEOTAB_SAFE_HABIT_DESCRIPTIONS,
    RPM_RANGE_BANDS,
)
from app.services import sync_run_service
from app.services.navifault_dateplate_map_service import refresh_automatic_dateplate_mappings

log = logging.getLogger(__name__)

_SNAPSHOT_PATH = "/api/v1/integration/snapshot"
_WATERMARK_KEY = "snapshot"
# Password enmascarado cuando NO se pidió include_credentials: no re-cifrar.
_MASKED_PASSWORD = "********"


class InvalidMasterSnapshotError(ValueError):
    """El snapshot viola una invariante del contrato de integración."""


@dataclass
class SyncResult:
    """Resumen de un sync, para logs/tests."""

    full: bool
    generated_at: str | None = None
    fleets: int = 0
    databases: int = 0
    credentials: int = 0
    rules: int = 0
    vehicle_groups: int = 0
    vehicles: int = 0
    motor_rpm_bands: int = 0
    motor_speeds: int = 0
    motor_attachments: int = 0
    navifault_dateplate_map: dict[str, int] = field(default_factory=dict)
    deactivated: dict[str, int] = field(default_factory=dict)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# Fetch HTTP
# ---------------------------------------------------------------------------
async def fetch_snapshot(
    *,
    since: str | None = None,
    include_credentials: bool = True,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Pide el snapshot a Navi Vehículos. `client` inyectable para tests."""
    if not settings.navi_api_key:
        raise RuntimeError(
            "NAVI_API_KEY no configurada: el sync no puede autenticarse contra Navi."
        )

    params: dict[str, str] = {"include_credentials": str(include_credentials).lower()}
    if since:
        params["since"] = since
    headers = {"X-API-Key": settings.navi_api_key}
    url = settings.navi_base_url.rstrip("/") + _SNAPSHOT_PATH

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=settings.sync_http_timeout_seconds)
    try:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# Watermark
# ---------------------------------------------------------------------------
async def get_watermark(db: AsyncSession, key: str = _WATERMARK_KEY) -> str | None:
    row = (
        await db.execute(text("SELECT watermark FROM sync_state WHERE key = :k"), {"k": key})
    ).scalar_one_or_none()
    return row.isoformat() if isinstance(row, datetime) else None


async def _set_watermark(
    db: AsyncSession, value: datetime | None, now: datetime, key: str = _WATERMARK_KEY
) -> None:
    await db.execute(
        text(
            "INSERT INTO sync_state (key, watermark, created_at, updated_at) "
            "VALUES (:k, :wm, :now, :now) "
            "ON CONFLICT (key) DO UPDATE SET watermark = EXCLUDED.watermark, "
            "updated_at = :now"
        ),
        {"k": key, "wm": value, "now": now},
    )


# ---------------------------------------------------------------------------
# Upsert helpers
# ---------------------------------------------------------------------------
async def _ensure_motor_types(db: AsyncSession, types: set[str], now: datetime) -> None:
    """El FK vehicles/geotab_rules.motor_type exige fila en motor_catalog. El
    snapshot no trae el catálogo (config local), así que se auto-crea lo que
    falte (description NULL) para no romper por integridad referencial."""
    for mt in sorted(types):
        await db.execute(
            text(
                "INSERT INTO motor_catalog (motor_type, description, created_at, updated_at) "
                "VALUES (:mt, NULL, :now, :now) ON CONFLICT (motor_type) DO NOTHING"
            ),
            {"mt": mt, "now": now},
        )


def _normalize_range_mode(customer: dict[str, Any]) -> str:
    """Modo de rangos declarado por la fuente, degradando a 'reglas'.

    Un payload viejo (sin el campo) o un valor desconocido NO deben cambiar el
    comportamiento de una flota: 'reglas' es el modo histórico y el default del
    contrato.
    """
    raw = customer.get("range_mode")
    mode = str(raw).strip().lower() if raw is not None else ""
    if mode in FLEET_RANGE_MODES:
        return mode
    if mode:
        log.warning(
            "sync: range_mode desconocido %r en cliente %s; uso 'reglas'",
            raw,
            customer.get("id"),
        )
    return "reglas"


def _normalized_rpm_bands(motor: dict[str, Any]) -> list[dict[str, Any]]:
    """Valida la partición del eje de RPM de un motor; [] si no es utilizable.

    Fail-closed por motor, no por sync: una configuración incompleta, con huecos
    o con solapes se descarta entera (el motor queda "sin configurar") en vez de
    dejar que el ETL reparta tiempo con cortes inconsistentes. Quedarse sin
    rangos es visible en calidad de datos; unos rangos torcidos no lo son.
    """
    raw_bands = motor.get("rpm_bands") or []
    motor_type = str(motor.get("motor_type") or "").strip()
    by_band: dict[str, dict[str, Any]] = {}
    for entry in raw_bands:
        band = str(entry.get("band") or "").strip().lower()
        if band not in RPM_RANGE_BANDS:
            log.warning(
                "sync: banda de RPM desconocida %r en motor %s; descarto la configuración",
                entry.get("band"),
                motor_type,
            )
            return []
        if band in by_band:
            log.warning(
                "sync: banda de RPM %s repetida en motor %s; descarto la configuración",
                band,
                motor_type,
            )
            return []
        by_band[band] = entry

    if not by_band:
        return []
    missing = [band for band in RPM_RANGE_BANDS if band not in by_band]
    if missing:
        log.warning(
            "sync: motor %s no cubre el eje de RPM (faltan %s); lo dejo sin configurar",
            motor_type,
            ", ".join(missing),
        )
        return []

    normalized: list[dict[str, Any]] = []
    previous_max: int | None = None
    for position, band in enumerate(RPM_RANGE_BANDS):
        entry = by_band[band]
        try:
            rpm_min = int(entry["rpm_min"])
            rpm_max = None if entry.get("rpm_max") is None else int(entry["rpm_max"])
        except (KeyError, TypeError, ValueError):
            log.warning(
                "sync: rango de RPM no numérico en motor %s banda %s; lo dejo sin configurar",
                motor_type,
                band,
            )
            return []
        is_last = position == len(RPM_RANGE_BANDS) - 1
        if rpm_min < 0 or (rpm_max is not None and rpm_max <= rpm_min):
            log.warning(
                "sync: rango inválido en motor %s banda %s (%s-%s); lo dejo sin configurar",
                motor_type,
                band,
                rpm_min,
                rpm_max,
            )
            return []
        if rpm_max is None and not is_last:
            log.warning(
                "sync: banda intermedia %s sin límite superior en motor %s; lo dejo sin configurar",
                band,
                motor_type,
            )
            return []
        if previous_max is not None and previous_max != rpm_min:
            log.warning(
                "sync: rangos no contiguos en motor %s (%s empieza en %s, "
                "el tramo anterior termina en %s); lo dejo sin configurar",
                motor_type,
                band,
                rpm_min,
                previous_max,
            )
            return []
        previous_max = rpm_max
        normalized.append({"band": band, "rpm_min": rpm_min, "rpm_max": rpm_max})
    return normalized


async def _upsert_motor_rpm_bands(db: AsyncSession, motor: dict[str, Any], now: datetime) -> int:
    """Reemplaza los rangos de un motor. Devuelve cuántas bandas quedaron.

    La configuración es atómica por motor (DELETE + INSERT): media partición no
    es interpretable, así que no se hace merge fila a fila.
    """
    motor_type = str(motor.get("motor_type") or "").strip()
    if not motor_type:
        return 0
    bands = _normalized_rpm_bands(motor)
    await db.execute(text("DELETE FROM motor_rpm_bands WHERE motor_type = :mt"), {"mt": motor_type})
    for band in bands:
        await db.execute(
            text(
                "INSERT INTO motor_rpm_bands (id, motor_type, band, rpm_min, rpm_max, "
                "created_at, updated_at) "
                "VALUES (:id, :mt, :band, :rpm_min, :rpm_max, :now, :now)"
            ),
            {"id": uuid.uuid4(), "mt": motor_type, "now": now, **band},
        )
    return len(bands)


def _normalized_motor_speeds(motor: dict[str, Any]) -> tuple[int | None, int | None]:
    """Velocidades de placa del motor; (None, None) si el par no es utilizable.

    Fail-closed y por par, no por campo: si la sobrevelocidad quedara por debajo
    de la velocidad gobernada, ninguno de los dos merece confianza y el motor se
    queda "sin capturar". El check de la tabla rechazaría ese par de todos modos;
    descartarlo acá evita tumbar el sync entero por un motor mal cargado.
    """
    motor_type = str(motor.get("motor_type") or "").strip()
    values: list[int | None] = []
    for key in ("governed_speed_rpm", "max_overspeed_rpm"):
        raw = motor.get(key)
        if raw is None:
            values.append(None)
            continue
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            log.warning(
                "sync: %s no numérico (%r) en motor %s; lo dejo sin capturar",
                key,
                raw,
                motor_type,
            )
            return None, None
        if parsed <= 0:
            log.warning(
                "sync: %s no positivo (%s) en motor %s; lo dejo sin capturar",
                key,
                parsed,
                motor_type,
            )
            return None, None
        values.append(parsed)

    governed, overspeed = values
    if governed is not None and overspeed is not None and overspeed < governed:
        log.warning(
            "sync: motor %s con sobrevelocidad %s por debajo de la velocidad "
            "gobernada %s; lo dejo sin capturar",
            motor_type,
            overspeed,
            governed,
        )
        return None, None
    return governed, overspeed


async def _upsert_motor_speeds(db: AsyncSession, motor: dict[str, Any], now: datetime) -> int:
    """Escribe las velocidades de placa del motor. Devuelve 1 si quedó alguna.

    Se escribe siempre lo que llega, NULL incluido: la fuente es autoritativa y
    borrar allá tiene que borrar acá. `_ensure_motor_types` ya garantizó la fila
    del catálogo.
    """
    motor_type = str(motor.get("motor_type") or "").strip()
    if not motor_type:
        return 0
    governed, overspeed = _normalized_motor_speeds(motor)
    await db.execute(
        text(
            "UPDATE motor_catalog SET governed_speed_rpm = :governed, "
            "max_overspeed_rpm = :overspeed, updated_at = :now "
            "WHERE motor_type = :mt"
        ),
        {"mt": motor_type, "governed": governed, "overspeed": overspeed, "now": now},
    )
    return 1 if governed is not None or overspeed is not None else 0


def _normalized_attachment(raw: Any) -> dict[str, Any] | None:
    """Normaliza un adjunto del snapshot; None si no es utilizable.

    Fail-per-adjunto: un documento mal formado se descarta solo. Una curva es
    documentación, no un dato de cálculo, y no debe poder tumbar el sync del
    catálogo de motores ni de los vehículos.
    """
    if not isinstance(raw, dict):
        return None
    try:
        source_id = int(raw["id"])
    except (KeyError, TypeError, ValueError):
        return None
    if source_id <= 0:
        return None

    def _text(key: str) -> str | None:
        value = raw.get(key)
        if value is None:
            return None
        text_value = str(value).strip()
        return text_value or None

    file_size: int | None
    try:
        file_size = None if raw.get("file_size") is None else int(raw["file_size"])
    except (TypeError, ValueError):
        file_size = None
    if file_size is not None and file_size < 0:
        file_size = None

    return {
        "source_id": source_id,
        "cpl": _text("cpl"),
        "original_filename": _text("original_filename"),
        "content_type": _text("content_type"),
        "file_size": file_size,
        "stored": _text("stored_filename"),
        "src_updated": _parse_dt(_text("updated_at")),
    }


# La huella del binario en el origen. `stored_filename` es un uuid4 nuevo por
# cada carga en Navi Vehículos, así que basta para saber que el archivo cambió
# sin descargarlo; tamaño y fecha completan la señal.
_ATTACHMENT_BINARY_CHANGED = (
    "(motor_attachments.source_stored_filename IS DISTINCT FROM "
    "EXCLUDED.source_stored_filename "
    "OR motor_attachments.source_updated_at IS DISTINCT FROM EXCLUDED.source_updated_at "
    "OR motor_attachments.file_size IS DISTINCT FROM EXCLUDED.file_size)"
)


async def _upsert_motor_attachments(
    db: AsyncSession, motor: dict[str, Any], now: datetime
) -> set[int]:
    """Replica los metadatos de los adjuntos del motor. Devuelve los source_id
    vistos.

    El binario no se replica acá: se descarga bajo demanda y se cachea (ver
    `motor_curve_service`). Si la huella del origen cambió, esta escritura
    invalida la caché para que el próximo acceso vuelva a bajarlo; si no cambió,
    conserva la caché intacta y no vuelve a transferir nada.

    Un `fetch_status = 'failed'` NO se toca cuando el binario no cambió: el
    reintento lo decide el servicio con su propio enfriamiento, no cada sync.
    """
    motor_type = str(motor.get("motor_type") or "").strip()
    if not motor_type:
        return set()

    raw_attachments = motor.get("attachments")
    if not isinstance(raw_attachments, list):
        # Payload viejo sin la clave: no hay nada que replicar y tampoco se
        # desactiva lo ya replicado (un incremental no prueba una baja).
        return set()

    seen: set[int] = set()
    for raw in raw_attachments:
        data = _normalized_attachment(raw)
        if data is None:
            log.warning(
                "sync: adjunto de motor %s ilegible; lo descarto",
                motor_type,
            )
            continue
        await db.execute(
            text(
                "INSERT INTO motor_attachments (id, source_id, motor_type, cpl, "
                "original_filename, content_type, file_size, source_stored_filename, "
                "source_updated_at, fetch_status, is_active, synced_at, created_at, "
                "updated_at) "
                "VALUES (:id, :source_id, :motor_type, :cpl, :original_filename, "
                ":content_type, :file_size, :stored, :src_updated, 'pending', true, "
                ":now, :now, :now) "
                "ON CONFLICT (source_id) DO UPDATE SET "
                "motor_type = EXCLUDED.motor_type, cpl = EXCLUDED.cpl, "
                "original_filename = EXCLUDED.original_filename, "
                "content_type = EXCLUDED.content_type, file_size = EXCLUDED.file_size, "
                "source_stored_filename = EXCLUDED.source_stored_filename, "
                "source_updated_at = EXCLUDED.source_updated_at, "
                "is_active = true, synced_at = :now, updated_at = :now, "
                f"object_key = CASE WHEN {_ATTACHMENT_BINARY_CHANGED} THEN NULL "
                "ELSE motor_attachments.object_key END, "
                f"content_sha256 = CASE WHEN {_ATTACHMENT_BINARY_CHANGED} THEN NULL "
                "ELSE motor_attachments.content_sha256 END, "
                f"fetch_status = CASE WHEN {_ATTACHMENT_BINARY_CHANGED} THEN 'pending' "
                "ELSE motor_attachments.fetch_status END, "
                f"fetch_error = CASE WHEN {_ATTACHMENT_BINARY_CHANGED} THEN NULL "
                "ELSE motor_attachments.fetch_error END, "
                f"fetched_at = CASE WHEN {_ATTACHMENT_BINARY_CHANGED} THEN NULL "
                "ELSE motor_attachments.fetched_at END"
            ),
            {"id": uuid.uuid4(), "motor_type": motor_type, "now": now, **data},
        )
        seen.add(data["source_id"])
    return seen


async def _deactivate_missing_motor_attachments(
    db: AsyncSession, seen_source_ids: set[int], now: datetime
) -> int:
    """Full sync: un adjunto borrado en Navi deja de ofrecerse.

    No se borra la fila: la caché en MinIO queda referenciada por `object_key` y
    un borrado accidental en el origen no debe destruir el documento acá. Basta
    con dejar de listarlo.
    """
    if not seen_source_ids:
        return 0
    res = await db.execute(
        text(
            "UPDATE motor_attachments SET is_active = false, updated_at = :now "
            "WHERE NOT (source_id = ANY(:seen)) AND is_active"
        ),
        {"seen": list(seen_source_ids), "now": now},
    )
    return int(getattr(res, "rowcount", 0) or 0)


async def _upsert_fleet(db: AsyncSession, c: dict[str, Any], now: datetime) -> uuid.UUID:
    # El snapshot (customers) no trae `code`; se deriva determinístico del id de
    # origen. ON CONFLICT por source_id (no por code) para no chocar con flotas
    # creadas a mano en el portal.
    fleet_id: uuid.UUID = (
        await db.execute(
            text(
                "INSERT INTO fleets (id, source_id, code, name, is_active, range_mode, "
                "synced_at, created_at, updated_at) "
                "VALUES (:id, :sid, :code, :name, true, :range_mode, :now, :now, :now) "
                "ON CONFLICT (source_id) DO UPDATE SET name = EXCLUDED.name, "
                "is_active = true, range_mode = EXCLUDED.range_mode, "
                "synced_at = :now, updated_at = :now "
                "RETURNING id"
            ),
            {
                "id": uuid.uuid4(),
                "sid": c["id"],
                "code": f"navi-{c['id']}",
                "name": c["name"],
                "range_mode": _normalize_range_mode(c),
                "now": now,
            },
        )
    ).scalar_one()
    return fleet_id


async def _upsert_fleet_groups(
    db: AsyncSession, c: dict[str, Any], fleet_id: uuid.UUID, now: datetime
) -> dict[int, uuid.UUID]:
    """Réplica de los grupos internos de vehículos del cliente.

    El snapshot trae SIEMPRE el árbol completo del cliente cuando el cliente
    entra al payload (también en un incremental), así que la reconciliación de
    bajas es por flota y no necesita full sync. Dos pasadas porque `parent_id`
    referencia UUIDs locales y un grupo puede haberse movido bajo uno más nuevo:
    primero todas las filas sin padre, después el cableado de padres.

    Un payload viejo sin la clave `groups` no toca nada: la ausencia de la
    lista no es evidencia de una baja.
    """
    groups = c.get("groups")
    if groups is None:
        return {}

    group_by_source: dict[int, uuid.UUID] = {}
    for g in groups:
        local_id: uuid.UUID = (
            await db.execute(
                text(
                    "INSERT INTO fleet_vehicle_groups (id, source_id, fleet_id, name, "
                    "is_active, synced_at, created_at, updated_at) "
                    "VALUES (:id, :sid, :fleet, :name, :active, :now, :now, :now) "
                    "ON CONFLICT (source_id) DO UPDATE SET fleet_id = EXCLUDED.fleet_id, "
                    "name = EXCLUDED.name, is_active = EXCLUDED.is_active, "
                    "synced_at = :now, updated_at = :now "
                    "RETURNING id"
                ),
                {
                    "id": uuid.uuid4(),
                    "sid": g["id"],
                    "fleet": fleet_id,
                    "name": g["name"],
                    "active": bool(g.get("is_active", True)),
                    "now": now,
                },
            )
        ).scalar_one()
        group_by_source[int(g["id"])] = local_id

    for g in groups:
        parent_source = g.get("parent_id")
        await db.execute(
            text("UPDATE fleet_vehicle_groups SET parent_id = :parent WHERE id = :id"),
            {
                "parent": group_by_source.get(int(parent_source))
                if parent_source is not None
                else None,
                "id": group_by_source[int(g["id"])],
            },
        )

    # Baja por flota: lo que ya no viene se desactiva, nunca se borra.
    if group_by_source:
        await db.execute(
            text(
                "UPDATE fleet_vehicle_groups SET is_active = false, updated_at = :now "
                "WHERE fleet_id = :fleet AND is_active "
                "AND NOT (source_id = ANY(:seen))"
            ),
            {"fleet": fleet_id, "now": now, "seen": list(group_by_source.keys())},
        )
    else:
        await db.execute(
            text(
                "UPDATE fleet_vehicle_groups SET is_active = false, updated_at = :now "
                "WHERE fleet_id = :fleet AND is_active"
            ),
            {"fleet": fleet_id, "now": now},
        )
    return group_by_source


async def _upsert_database(
    db: AsyncSession, d: dict[str, Any], fleet_id: uuid.UUID, now: datetime
) -> uuid.UUID:
    provider = d.get("provider_config") or {}
    db_id: uuid.UUID = (
        await db.execute(
            text(
                "INSERT INTO geotab_databases (id, source_id, fleet_id, database_name, "
                "database_key, connection_type, plate_prefix, is_active, synced_at, "
                "created_at, updated_at) "
                "VALUES (:id, :sid, :fleet, :name, :key, :conn, :prefix, true, :now, :now, :now) "
                "ON CONFLICT (source_id) DO UPDATE SET fleet_id = EXCLUDED.fleet_id, "
                "database_name = EXCLUDED.database_name, database_key = EXCLUDED.database_key, "
                "connection_type = EXCLUDED.connection_type, plate_prefix = EXCLUDED.plate_prefix, "
                "is_active = true, synced_at = :now, updated_at = :now "
                "RETURNING id"
            ),
            {
                "id": uuid.uuid4(),
                "sid": d["id"],
                "fleet": fleet_id,
                "name": d["database_name"],
                "key": d["database_key"],
                "conn": d.get("connection_type") or "geotab",
                "prefix": provider.get("plate_prefix"),
                "now": now,
            },
        )
    ).scalar_one()
    return db_id


def _credential_password_enc(cr: dict[str, Any]) -> bytes | None:
    """Token Fernet a guardar, o None si el snapshot no trae secreto.

    Navi Vehículos publica `password_enc` con el token tal cual, cifrado con la
    misma `MASTER_FERNET_KEY` que usan este portal y el ETL, así que se
    almacena sin descifrar ni volver a cifrar: la contraseña en claro no llega
    a existir en el tránsito, que atraviesa un CDN que termina TLS.

    `password` sobrevive como camino de compatibilidad para un snapshot previo
    a ese campo; de un origen al día llega enmascarado y no aporta secreto.
    """
    token = cr.get("password_enc")
    if token:
        raw = token.encode("ascii") if isinstance(token, str) else bytes(token)
        try:
            decrypt_secret(raw)
        except Exception as exc:
            # Fail-closed: guardar un token que este portal no sabe descifrar
            # deja al ETL sin poder autenticar contra geotab, y el síntoma
            # aparecería en la extracción del día siguiente sin nada que lo
            # ligue a este sync. Abortar aquí nombra la causa: las claves de
            # las dos aplicaciones dejaron de coincidir.
            raise InvalidMasterSnapshotError(
                "Snapshot inválido de Navi Vehículos: `password_enc` no se puede "
                "descifrar con MASTER_FERNET_KEY; las dos aplicaciones deben "
                "compartir la misma clave Fernet "
                f"(credential source_id={cr.get('id')!r}, username={cr.get('username')!r})."
            ) from exc
        return raw

    password = cr.get("password")
    if not password or password == _MASKED_PASSWORD:
        return None
    return encrypt_secret(password)


async def _upsert_credential(
    db: AsyncSession, cr: dict[str, Any], db_id: uuid.UUID, now: datetime
) -> bool:
    """Replica una credencial del snapshot. Devuelve False si no escribió nada."""
    password_enc = _credential_password_enc(cr)
    if password_enc is None:
        # Sin password real (incremental sin include_credentials): upsert de
        # metadatos sin tocar password_enc existente. Si la fila es nueva no se
        # puede crear (password_enc NOT NULL) -> se omite.
        params = {
            "label": cr.get("label"),
            "active": cr.get("is_active", True),
            "sid": cr["id"],
            "db": db_id,
            "user": cr["username"],
            "now": now,
        }
        res = await db.execute(
            text(
                "UPDATE geotab_credentials SET label = :label, is_active = :active, "
                "synced_at = :now, updated_at = :now WHERE source_id = :sid"
            ),
            params,
        )
        if int(getattr(res, "rowcount", 0) or 0) > 0:
            return True
        res = await db.execute(
            text(
                "UPDATE geotab_credentials SET source_id = :sid, label = :label, "
                "is_active = :active, synced_at = :now, updated_at = :now "
                "WHERE geotab_database_id = :db AND username = :user"
            ),
            params,
        )
        if int(getattr(res, "rowcount", 0) or 0) > 0:
            return True
        log.warning(
            "sync_credential_skipped_without_secret source_id=%s username=%s: "
            "credencial nueva sin password_enc; no se puede crear",
            cr["id"],
            cr["username"],
        )
        return False

    params = {
        "id": uuid.uuid4(),
        "sid": cr["id"],
        "db": db_id,
        "user": cr["username"],
        "pwd": password_enc,
        "label": cr.get("label"),
        "active": cr.get("is_active", True),
        "now": now,
    }
    res = await db.execute(
        text(
            "UPDATE geotab_credentials SET geotab_database_id = :db, username = :user, "
            "password_enc = :pwd, label = :label, is_active = :active, "
            "synced_at = :now, updated_at = :now WHERE source_id = :sid"
        ),
        params,
    )
    if int(getattr(res, "rowcount", 0) or 0) > 0:
        return True

    res = await db.execute(
        text(
            "UPDATE geotab_credentials SET source_id = :sid, password_enc = :pwd, "
            "label = :label, is_active = :active, synced_at = :now, updated_at = :now "
            "WHERE geotab_database_id = :db AND username = :user"
        ),
        params,
    )
    if int(getattr(res, "rowcount", 0) or 0) > 0:
        return True

    await db.execute(
        text(
            "INSERT INTO geotab_credentials (id, source_id, geotab_database_id, username, "
            "password_enc, label, is_active, synced_at, created_at, updated_at) "
            "VALUES (:id, :sid, :db, :user, :pwd, :label, :active, :now, :now, :now) "
            # last_used_at NO se toca: es rotación local del pool.
        ),
        params,
    )
    return True


async def _upsert_rule(
    db: AsyncSession, r: dict[str, Any], db_id: uuid.UUID, now: datetime
) -> tuple[int | None, list[int]]:
    applications = _rule_applications(r)
    physical_source_id = r.get("rule_source_id")
    if physical_source_id is None and r.get("applications"):
        physical_source_id = r.get("id")
    rule_uuid: uuid.UUID = (
        await db.execute(
            text(
                "INSERT INTO geotab_rules (id, source_id, geotab_database_id, rule_id, "
                "name, is_active, synced_at, created_at, updated_at) "
                "VALUES (:id, :sid, :db, :rid, :name, true, :now, :now, :now) "
                "ON CONFLICT (geotab_database_id, rule_id) DO UPDATE SET "
                "source_id = COALESCE(geotab_rules.source_id, EXCLUDED.source_id), "
                "name = EXCLUDED.name, is_active = true, synced_at = :now, updated_at = :now "
                "RETURNING id"
            ),
            {
                "id": uuid.uuid4(),
                "sid": physical_source_id,
                "db": db_id,
                "rid": r["rule_id"],
                "name": r["name"],
                "now": now,
            },
        )
    ).scalar_one()

    seen_app_ids: list[int] = []
    for app in applications:
        await _upsert_rule_application(db, app, rule_uuid, now)
        if app.get("source_id") is not None:
            seen_app_ids.append(app["source_id"])
    return physical_source_id, seen_app_ids


def _rule_applications(r: dict[str, Any]) -> list[dict[str, Any]]:
    applications = r.get("applications")
    if applications is not None:
        return [
            {
                **app,
                "source_id": app.get("application_id")
                if app.get("application_id") is not None
                else (app.get("source_id") if app.get("source_id") is not None else app.get("id")),
            }
            for app in applications
        ]
    if r.get("category") is None:
        return []
    application_source_id = r.get("application_id")
    if application_source_id is None:
        application_source_id = r.get("source_id")
    if (
        application_source_id is None
        and r.get("id") is not None
        and r.get("rule_source_id") is None
    ):
        # Snapshot plano histórico: `id` identificaba la aplicación.
        application_source_id = r.get("id")
    return [
        {
            "source_id": application_source_id,
            "category": r["category"],
            "event_type": r.get("event_type"),
            "motor_type": r.get("motor_type"),
            "band": r.get("band"),
            "is_descenso": r.get("is_descenso"),
            "description": r.get("description"),
            "is_active": r.get("is_active", True),
        }
    ]


def _validate_rule_application_scopes(data: dict[str, Any]) -> None:
    """Toda aplicación de operación debe declarar el motor que gobierna.

    Se valida antes del primer upsert para que un error de la fuente falle
    cerrado, mantenga la réplica intacta y señale la fila exacta sin depender
    del mensaje interno de PostgreSQL.
    """

    for customer in data.get("customers", []):
        for database in customer.get("databases", []):
            for rule in database.get("rules", []):
                for application in _rule_applications(rule):
                    motor_type = application.get("motor_type")
                    if application.get("category") != "operacion":
                        continue
                    if motor_type is not None and str(motor_type).strip():
                        continue
                    raise InvalidMasterSnapshotError(
                        "Snapshot inválido de Navi Vehículos: una regla de operación "
                        "no tiene motor asociado "
                        f"(cliente={customer.get('name')!r}, "
                        f"base={database.get('database_name')!r}, "
                        f"regla={rule.get('name')!r}, "
                        f"rule_id={rule.get('rule_id')!r}, "
                        f"application_id={application.get('source_id')!r})."
                    )


def _normalize_band(app: dict[str, Any]) -> tuple[str | None, bool]:
    """Normaliza `band`/`is_descenso` del snapshot a valores aceptados por la DB.

    Tolerante a snapshots viejos (campos ausentes -> NULL/false) y fail-safe ante
    valores desconocidos: se degradan a NULL con warning en vez de abortar el
    sync entero por un CHECK. El ETL ya tiene fallback por palabra clave.
    """
    raw_band = app.get("band")
    band = str(raw_band).strip().lower() or None if raw_band is not None else None
    if band is not None and band not in GEOTAB_RULE_BANDS:
        log.warning(
            "sync: banda desconocida %r en aplicación de regla (source_id=%s); la ignoro",
            band,
            app.get("source_id"),
        )
        band = None

    is_descenso = bool(app.get("is_descenso") or False)
    if is_descenso and band is None:
        # Un descenso sin banda no es interpretable; la DB además lo rechaza.
        log.warning(
            "sync: is_descenso sin banda en aplicación de regla (source_id=%s); lo bajo a false",
            app.get("source_id"),
        )
        is_descenso = False
    if is_descenso and band == "ralenti":
        log.warning(
            "sync: ralentí no se mide en descenso (source_id=%s); bajo is_descenso a false",
            app.get("source_id"),
        )
        is_descenso = False
    return band, is_descenso


def _normalize_safe_habit_description(app: dict[str, Any]) -> str | None:
    """Normaliza la clasificación declarada para una regla de hábito seguro."""
    if app.get("category") != "habito_seguro":
        return None

    raw_description = app.get("description")
    if raw_description is None:
        # Compatibilidad explícita: snapshots antiguos ya declaraban la
        # semántica en event_type aunque aún no enviaran description.
        if app.get("event_type") == "exceso_rpm":
            return "Excesos de RPM"
        return None

    description = " ".join(str(raw_description).split())
    canonical_by_casefold = {value.casefold(): value for value in GEOTAB_SAFE_HABIT_DESCRIPTIONS}
    canonical = canonical_by_casefold.get(description.casefold())
    if canonical is None:
        log.warning(
            "sync: descripción de hábito seguro desconocida %r (source_id=%s); la ignoro",
            description,
            app.get("source_id"),
        )
    return canonical


async def _upsert_rule_application(
    db: AsyncSession, app: dict[str, Any], rule_id: uuid.UUID, now: datetime
) -> None:
    # Dos matches secuenciales (no en un solo WHERE, para no enganchar la fila
    # equivocada cuando el id de aplicación de Navi coincide con otro source_id):
    #   1. por source_id estable (application_id de Navi).
    #   2. por identidad lógica (clave única real de la tabla: rule+category+motor).
    # event_type es atributo, no parte de la clave; se actualiza, no discrimina.
    band, is_descenso = _normalize_band(app)
    description = _normalize_safe_habit_description(app)
    params = {
        "sid": app.get("source_id"),
        "rule": rule_id,
        "cat": app["category"],
        "event": app.get("event_type"),
        "motor": app.get("motor_type"),
        "band": band,
        "descenso": is_descenso,
        "description": description,
        "active": app.get("is_active", True),
        "now": now,
    }
    set_clause = (
        "SET source_id = COALESCE(:sid, geotab_rule_applications.source_id), "
        "geotab_rule_id = :rule, category = :cat, event_type = :event, "
        "motor_type = :motor, band = :band, is_descenso = :descenso, "
        "description = :description, "
        "is_active = :active, synced_at = :now, updated_at = :now "
    )

    if params["sid"] is not None:
        res = await db.execute(
            text("UPDATE geotab_rule_applications " + set_clause + "WHERE source_id = :sid"),
            params,
        )
        if int(getattr(res, "rowcount", 0) or 0) > 0:
            return

    res = await db.execute(
        text(
            "UPDATE geotab_rule_applications " + set_clause + "WHERE geotab_rule_id = :rule "
            "AND category = :cat AND motor_type IS NOT DISTINCT FROM :motor"
        ),
        params,
    )
    if int(getattr(res, "rowcount", 0) or 0) > 0:
        return

    await db.execute(
        text(
            "INSERT INTO geotab_rule_applications "
            "(id, source_id, geotab_rule_id, category, event_type, motor_type, band, "
            "is_descenso, description, is_active, synced_at, created_at, updated_at) "
            "VALUES (:id, :sid, :rule, :cat, :event, :motor, :band, :descenso, "
            ":description, :active, :now, :now, :now)"
        ),
        {"id": uuid.uuid4(), **params},
    )


def _normalize_geotab_identity(vehicle: dict[str, Any]) -> tuple[str | None, str]:
    """Normaliza la identidad Geotab sin mezclar resolución y conectividad.

    Algunos snapshots antiguos enviaron ``connected``/``disconnected`` en el
    campo de resolución del dispositivo. Si existe un device ID, ambos estados
    prueban que el dispositivo fue localizado; la conectividad se audita aparte.
    """
    raw_device_id = vehicle.get("geotab_device_id")
    device_id = str(raw_device_id).strip() if raw_device_id is not None else None
    device_id = device_id or None
    raw_status = str(vehicle.get("geotab_customer_status") or "unknown").strip().casefold()
    if raw_status in {"connected", "disconnected"}:
        status = "found" if device_id else "unknown"
    else:
        status = (
            raw_status
            if raw_status in {"found", "not_found", "unknown", "not_applicable"}
            else "unknown"
        )
    return device_id, status


async def _upsert_vehicle(
    db: AsyncSession,
    v: dict[str, Any],
    fleet_id: uuid.UUID | None,
    db_id: uuid.UUID | None,
    now: datetime,
    group_id: uuid.UUID | None = None,
) -> None:
    # Clave natural: plate. group_key/rpm_class/tank_volume son extensiones
    # locales del ETL y NO se tocan en el update.
    # is_active se deriva de la categoría efectiva del snapshot: las flotas
    # gestionadas (Flota Administrada / Experiencia Superior) quedan activas;
    # 'Ninguna' se trae igual pero como inactivo (se sacó de un cliente gestionado).
    category = (v.get("category") or "Ninguna").strip()
    is_active = category.casefold() != "ninguna"
    device_id, customer_status = _normalize_geotab_identity(v)
    # vehicle_group_id solo se toca si el snapshot trae la clave: un payload
    # viejo sin `customer_group_id` no es evidencia de una desasignación.
    has_group_key = "customer_group_id" in v
    await db.execute(
        text(
            "INSERT INTO vehicles (id, plate, vin, geotab_device_id, geotab_device_synced_at, "
            "fleet_id, geotab_database_id, geotab_customer_status, engine_number, "
            "technical_number, cpl, marca, linea, marketing_model_name, service_model_name, "
            "ano_modelo, tipo_combustible, nombre_vehiculo, vocacional, category, motor_type, "
            "vehicle_group_id, is_active, synced_at, created_at, updated_at) "
            "VALUES (:id, :plate, :vin, :dev, :dev_sync, :fleet, :db, :status, :engine, :tech, "
            ":cpl, :marca, :linea, :marketing_model, :service_model, :ano, :comb, :nombre, "
            ":vocacional, :category, :motor, :vgroup, :active, :now, :now, :now) "
            "ON CONFLICT (plate) DO UPDATE SET vin = EXCLUDED.vin, "
            "geotab_device_id = EXCLUDED.geotab_device_id, "
            "geotab_device_synced_at = EXCLUDED.geotab_device_synced_at, "
            "fleet_id = EXCLUDED.fleet_id, geotab_database_id = EXCLUDED.geotab_database_id, "
            "geotab_customer_status = EXCLUDED.geotab_customer_status, "
            "engine_number = EXCLUDED.engine_number, technical_number = EXCLUDED.technical_number, "
            "cpl = EXCLUDED.cpl, marca = EXCLUDED.marca, linea = EXCLUDED.linea, "
            "marketing_model_name = EXCLUDED.marketing_model_name, "
            "service_model_name = EXCLUDED.service_model_name, "
            "ano_modelo = EXCLUDED.ano_modelo, tipo_combustible = EXCLUDED.tipo_combustible, "
            "nombre_vehiculo = EXCLUDED.nombre_vehiculo, vocacional = EXCLUDED.vocacional, "
            "category = EXCLUDED.category, "
            "motor_type = EXCLUDED.motor_type, "
            "vehicle_group_id = CASE WHEN :has_group THEN EXCLUDED.vehicle_group_id "
            "ELSE vehicles.vehicle_group_id END, "
            "is_active = EXCLUDED.is_active, "
            "synced_at = :now, updated_at = :now"
        ),
        {
            "id": uuid.uuid4(),
            "plate": v["plate"],
            "vin": v.get("vin"),
            "dev": device_id,
            "dev_sync": _parse_dt(v.get("geotab_device_synced_at")),
            "fleet": fleet_id,
            "db": db_id,
            "status": customer_status,
            "engine": v.get("engine_number"),
            "tech": v.get("technical_number"),
            "cpl": v.get("cpl"),
            "marca": v.get("marca"),
            "linea": v.get("linea"),
            "marketing_model": v.get("marketing_model_name"),
            "service_model": v.get("service_model_name"),
            "ano": v.get("ano_modelo"),
            "comb": v.get("tipo_combustible"),
            "nombre": v.get("nombre_vehiculo"),
            "vocacional": bool(v.get("vocacional", False)),
            "category": category,
            "active": is_active,
            "motor": v.get("motor_type"),
            "vgroup": group_id,
            "has_group": has_group_key,
            "now": now,
        },
    )


async def _deactivate_missing(
    db: AsyncSession,
    table: str,
    seen_source_ids: set[int],
    now: datetime,
) -> int:
    """Full sync: marca is_active=false en réplicas (source_id NOT NULL) que no
    vinieron. No toca filas creadas a mano en el portal (source_id NULL)."""
    if not seen_source_ids:
        return 0
    res = await db.execute(
        text(
            f"UPDATE {table} SET is_active = false, updated_at = :now "
            "WHERE source_id IS NOT NULL AND NOT (source_id = ANY(:seen)) AND is_active"
        ),
        {"seen": list(seen_source_ids), "now": now},
    )
    return int(getattr(res, "rowcount", 0) or 0)


async def _deactivate_missing_vehicles(
    db: AsyncSession, seen_plates: set[str], now: datetime
) -> int:
    if not seen_plates:
        return 0
    res = await db.execute(
        text(
            "UPDATE vehicles SET is_active = false, updated_at = :now "
            "WHERE NOT (plate = ANY(:seen)) AND is_active"
        ),
        {"seen": list(seen_plates), "now": now},
    )
    return int(getattr(res, "rowcount", 0) or 0)


async def _refresh_seen_fleet_activity(
    db: AsyncSession, seen_source_ids: set[int], now: datetime
) -> int:
    """Activa una flota replicada solo si tiene al menos un vehículo activo."""
    if not seen_source_ids:
        return 0
    res = await db.execute(
        text(
            "UPDATE fleets f SET is_active = EXISTS ("
            "SELECT 1 FROM vehicles v WHERE v.fleet_id = f.id AND v.is_active"
            "), updated_at = :now "
            "WHERE f.source_id IS NOT NULL AND f.source_id = ANY(:seen)"
        ),
        {"seen": list(seen_source_ids), "now": now},
    )
    return int(getattr(res, "rowcount", 0) or 0)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------
async def apply_snapshot(
    db: AsyncSession, data: dict[str, Any], *, full: bool = False
) -> SyncResult:
    """Upsert idempotente del snapshot. No hace commit (lo hace el caller)."""
    _validate_rule_application_scopes(data)
    now = datetime.now(UTC)
    result = SyncResult(full=full, generated_at=data.get("generated_at"))

    fleet_by_source: dict[int, uuid.UUID] = {}
    db_by_source: dict[int, uuid.UUID] = {}
    group_by_source: dict[int, uuid.UUID] = {}
    motor_types: set[str] = set()
    seen_fleets: set[int] = set()
    seen_dbs: set[int] = set()
    seen_creds: set[int] = set()
    seen_rules: set[int] = set()
    seen_rule_apps: set[int] = set()
    seen_plates: set[str] = set()

    # Pasada 1: recolectar motores (rules + vehicles) y crear catálogo faltante
    # antes de insertar filas con ese FK.
    for c in data.get("customers", []):
        for d in c.get("databases", []):
            for r in d.get("rules", []):
                for app in _rule_applications(r):
                    if app.get("motor_type"):
                        motor_types.add(app["motor_type"])
    for v in data.get("vehicles", []):
        if v.get("motor_type"):
            motor_types.add(v["motor_type"])
    snapshot_motors = data.get("motors") or []
    for motor in snapshot_motors:
        if motor.get("motor_type"):
            motor_types.add(str(motor["motor_type"]).strip())
    await _ensure_motor_types(db, motor_types, now)

    # Rangos de RPM por motor. Viajan siempre completos (contrato §2.6), también
    # en un incremental, así que se reemplazan tal cual llegan. Un snapshot sin
    # `motors` (payload viejo) no toca nada.
    seen_motor_attachments: set[int] = set()
    for motor in snapshot_motors:
        result.motor_rpm_bands += await _upsert_motor_rpm_bands(db, motor, now)
        # Aditivas y nullable: un payload viejo no trae las claves, y el motor
        # queda con las velocidades en NULL, que es justamente "sin capturar".
        result.motor_speeds += await _upsert_motor_speeds(db, motor, now)
        seen_motor_attachments |= await _upsert_motor_attachments(db, motor, now)
    result.motor_attachments = len(seen_motor_attachments)

    # Pasada 2: clientes -> dbs -> credenciales/reglas.
    for c in data.get("customers", []):
        fleet_id = await _upsert_fleet(db, c, now)
        fleet_by_source[c["id"]] = fleet_id
        seen_fleets.add(c["id"])
        result.fleets += 1

        # Grupos internos de vehículos del cliente (viajan completos por
        # cliente, también en incrementales; la baja se reconcilia por flota).
        customer_groups = await _upsert_fleet_groups(db, c, fleet_id, now)
        group_by_source.update(customer_groups)
        result.vehicle_groups += len(customer_groups)

        for d in c.get("databases", []):
            db_id = await _upsert_database(db, d, fleet_id, now)
            db_by_source[d["id"]] = db_id
            seen_dbs.add(d["id"])
            result.databases += 1

            for cr in d.get("credentials", []):
                written = await _upsert_credential(db, cr, db_id, now)
                seen_creds.add(cr["id"])
                if written:
                    result.credentials += 1

            for r in d.get("rules", []):
                rule_source_id, app_source_ids = await _upsert_rule(db, r, db_id, now)
                if rule_source_id is not None:
                    seen_rules.add(rule_source_id)
                seen_rule_apps.update(app_source_ids)
                result.rules += (
                    len(app_source_ids) if app_source_ids else len(_rule_applications(r))
                )

    # Grupos referenciados por vehículos cuyo cliente NO vino en este payload
    # (incremental: cambió el vehículo pero no el cliente). Se resuelven contra
    # la réplica local; sin esto, el upsert limpiaría la asignación.
    referenced_groups = {
        int(v["customer_group_id"])
        for v in data.get("vehicles", [])
        if v.get("customer_group_id") is not None
    } - set(group_by_source)
    if referenced_groups:
        rows = await db.execute(
            text("SELECT source_id, id FROM fleet_vehicle_groups WHERE source_id = ANY(:sids)"),
            {"sids": list(referenced_groups)},
        )
        for source_id, local_id in rows.all():
            group_by_source[int(source_id)] = local_id

    # Flotas y bases referenciadas por vehículos cuyo cliente NO vino en este
    # payload. Un incremental trae al vehículo cuando cambia él o su binding
    # Geotab —que se refresca a menudo— sin traer al cliente, que no cambió.
    # Sin esta resolución el upsert escribía fleet_id y geotab_database_id en
    # NULL: la flota "perdía" sus vehículos hasta el siguiente full sync, y el
    # siguiente incremental se los quitaba otra vez. Misma regla que los grupos.
    referenced_fleets = {
        int(v["customer_id"]) for v in data.get("vehicles", []) if v.get("customer_id") is not None
    } - set(fleet_by_source)
    if referenced_fleets:
        rows = await db.execute(
            text("SELECT source_id, id FROM fleets WHERE source_id = ANY(:sids)"),
            {"sids": list(referenced_fleets)},
        )
        for source_id, local_id in rows.all():
            fleet_by_source[int(source_id)] = local_id
    referenced_dbs = {
        int(sid)
        for v in data.get("vehicles", [])
        for sid in (v.get("geotab_customer_database_id") or v.get("customer_database_id"),)
        if sid is not None
    } - set(db_by_source)
    if referenced_dbs:
        rows = await db.execute(
            text("SELECT source_id, id FROM geotab_databases WHERE source_id = ANY(:sids)"),
            {"sids": list(referenced_dbs)},
        )
        for source_id, local_id in rows.all():
            db_by_source[int(source_id)] = local_id

    # Pasada 3: vehículos (resuelven fleet/db/grupo por source_id).
    for v in data.get("vehicles", []):
        v_fleet_id = fleet_by_source.get(v.get("customer_id"))
        v_db_id = db_by_source.get(
            v.get("geotab_customer_database_id") or v.get("customer_database_id")
        )
        v_group_source = v.get("customer_group_id")
        v_group_id = (
            group_by_source.get(int(v_group_source)) if v_group_source is not None else None
        )
        await _upsert_vehicle(db, v, v_fleet_id, v_db_id, now, group_id=v_group_id)
        seen_plates.add(v["plate"])
        result.vehicles += 1

    # Detección de borrados: solo en full sync (el incremental no trae todo).
    if full:
        result.deactivated = {
            "geotab_credentials": await _deactivate_missing(
                db, "geotab_credentials", seen_creds, now
            ),
            "geotab_rules": await _deactivate_missing(db, "geotab_rules", seen_rules, now),
            "geotab_rule_applications": await _deactivate_missing(
                db, "geotab_rule_applications", seen_rule_apps, now
            ),
            "geotab_databases": await _deactivate_missing(db, "geotab_databases", seen_dbs, now),
            "fleets": await _deactivate_missing(db, "fleets", seen_fleets, now),
            "vehicles": await _deactivate_missing_vehicles(db, seen_plates, now),
            "motor_attachments": await _deactivate_missing_motor_attachments(
                db, seen_motor_attachments, now
            ),
        }

    # El dateplate es la llave runtime de Navifault. Esta actualización queda
    # en la misma transacción del snapshot: un vehículo recién ingresado sólo
    # se ve después de que su asociación automática (si la evidencia es
    # unívoca) también esté lista. Las decisiones manuales son intocables.
    dateplate_map = await refresh_automatic_dateplate_mappings(db, execute=True)
    result.navifault_dateplate_map = dateplate_map.audit

    await _refresh_seen_fleet_activity(db, seen_fleets, now)

    # Watermark = generated_at de la respuesta (no la hora local). Solo avanza si
    # el apply llegó hasta aquí; el commit es del caller (todo o nada).
    await _set_watermark(db, _parse_dt(data.get("generated_at")), now)
    return result


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------
async def _run_sync_core_unlocked(
    *, full: bool = False, client: httpx.AsyncClient | None = None
) -> SyncResult:
    """Sync completo end-to-end: watermark -> fetch -> apply -> commit."""
    async with AsyncSessionLocal() as session:
        since = None if full else await get_watermark(session)

    data = await fetch_snapshot(since=since, client=client)

    async with AsyncSessionLocal() as session:
        result = await apply_snapshot(session, data, full=full)
        await session.commit()
    return result


async def _run_sync_core(
    *, full: bool = False, client: httpx.AsyncClient | None = None
) -> SyncResult:
    """Serializa el sync completo entre API, CLI y workers."""
    async with session_advisory_lock(engine, "navi-portal:master-sync"):
        return await _run_sync_core_unlocked(full=full, client=client)


def _sync_result_counts(result: SyncResult) -> dict[str, Any]:
    return {
        "generated_at": result.generated_at,
        "fleets": result.fleets,
        "databases": result.databases,
        "credentials": result.credentials,
        "rules": result.rules,
        "vehicles": result.vehicles,
        "motor_rpm_bands": result.motor_rpm_bands,
        "motor_attachments": result.motor_attachments,
        "navifault_dateplate_map": result.navifault_dateplate_map,
        "deactivated": result.deactivated,
    }


async def run_sync(
    *,
    full: bool = False,
    client: httpx.AsyncClient | None = None,
    trigger: str = "cli",
    actor_user_id: uuid.UUID | None = None,
    actor_email: str | None = None,
) -> SyncResult:
    """Ejecuta el sync del snapshot maestro y lo registra en `sync_run`."""
    started_at = datetime.now(UTC)
    mode = "full" if full else "incremental"
    try:
        result = await _run_sync_core(full=full, client=client)
    except OperationAlreadyRunningError:
        # Un intento rechazado por exclusión no es una falla del proveedor ni
        # una corrida: no contaminar el historial/SLO de sincronizaciones.
        raise
    except Exception as exc:
        await sync_run_service.record(
            kind="master",
            trigger=trigger,
            mode=mode,
            status="error",
            started_at=started_at,
            finished_at=datetime.now(UTC),
            error=str(exc),
            actor_user_id=actor_user_id,
            actor_email=actor_email,
        )
        raise
    await sync_run_service.record(
        kind="master",
        trigger=trigger,
        mode=mode,
        status="success",
        started_at=started_at,
        finished_at=datetime.now(UTC),
        result=_sync_result_counts(result),
        actor_user_id=actor_user_id,
        actor_email=actor_email,
    )
    return result
