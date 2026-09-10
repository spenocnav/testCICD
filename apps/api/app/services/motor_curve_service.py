"""Curvas de par y potencia del motor: resolución por flota y caché del binario.

Navi Vehículos guarda un PDF por motor y CPL. El sync replica los metadatos en
`public.motor_attachments`; acá se resuelve **qué documento le corresponde a
cada vehículo del alcance** y se sirve el binario, descargándolo del proveedor
la primera vez y cacheándolo en MinIO.

La regla de emparejamiento es la del negocio, no una heurística:

1. si el motor tiene un documento cuyo CPL coincide con el del vehículo, ese es
   el suyo;
2. si no coincide ninguno pero el motor tiene UN solo documento, ese es el suyo
   (el CPL declarado en el documento es informativo, no excluyente);
3. si no coincide ninguno y hay varios, se ofrecen todos marcados `ambiguo`. NO
   se elige uno por cercanía de CPL ni por fecha: sería inventar la respuesta, y
   mostrar la curva equivocada bajo el nombre correcto es peor que decir que no
   se puede decidir.

Todo el módulo es fail-soft: un proveedor caído, un documento borrado del
almacenamiento o una credencial ausente producen un estado legible en la
pantalla, nunca un 500 ni una pantalla vacía.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services import object_storage

log = logging.getLogger(__name__)

_ATTACHMENT_PATH = "/api/v1/integration/motor-attachments/{source_id}/file"

# Techo defensivo de la descarga. Las curvas observadas pesan ~200 KB; este
# límite es contra un origen que devuelva algo inesperadamente grande, no una
# restricción del negocio.
_MAX_BINARY_BYTES = 25 * 1024 * 1024

_FALLBACK_CONTENT_TYPE = "application/pdf"

# Motivos publicables. El texto crudo de httpx o MinIO no sale al cliente
# (SEC-023); acá sólo viaja la clase de fallo.
REASON_LABELS = {
    "provider_unreachable": "el servicio de Navi Vehículos no respondió",
    "provider_error": "Navi Vehículos rechazó la descarga",
    "provider_not_found": "el documento ya no existe en Navi Vehículos",
    "not_configured": "la integración con Navi Vehículos no está configurada",
    "too_large": "el documento excede el tamaño permitido",
    "storage_error": "no se pudo guardar el documento en el almacenamiento",
    "cooldown": "la descarga falló hace poco; se reintentará en unos minutos",
}


class MotorCurveUnavailableError(Exception):
    """El binario no está disponible ahora. `reason` es una clave de REASON_LABELS."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(REASON_LABELS.get(reason, "no disponible"))

    @property
    def label(self) -> str:
        return REASON_LABELS.get(self.reason, "no disponible")


# Cómo quedó emparejado un documento con un grupo de vehículos.
MATCH_CPL = "cpl"
MATCH_MOTOR = "motor"
MATCH_AMBIGUOUS = "ambiguo"

# Prioridad al fusionar: un mismo documento puede servir a varios grupos y el
# emparejamiento más fuerte es el que describe mejor por qué se ofrece.
_MATCH_RANK = {MATCH_CPL: 3, MATCH_MOTOR: 2, MATCH_AMBIGUOUS: 1}


@dataclass(frozen=True)
class AttachmentRow:
    """Fila de `motor_attachments` como la necesita la resolución."""

    id: uuid.UUID
    source_id: int
    motor_type: str
    cpl: str | None
    original_filename: str | None
    content_type: str | None
    file_size: int | None
    source_updated_at: datetime | None
    fetch_status: str
    cached: bool


@dataclass(frozen=True)
class VehicleGroup:
    """Vehículos del alcance agrupados por lo que define su curva."""

    motor_type: str
    cpl: str | None
    vehicle_count: int


@dataclass(frozen=True)
class CoveredGroup:
    """Un grupo de vehículos que este documento cubre, y CÓMO lo cubre.

    El `match` vive acá y no sólo a nivel del documento porque un mismo PDF
    puede ser la coincidencia exacta de CPL para unos vehículos y el único
    candidato del motor para otros. Publicar sólo el más fuerte diría "coincide
    el CPL" sobre vehículos cuyo CPL no coincide con nada.
    """

    cpl: str | None
    vehicle_count: int
    match: str


@dataclass(frozen=True)
class ResolvedCurve:
    attachment: AttachmentRow
    covered: tuple[CoveredGroup, ...]

    @property
    def match(self) -> str:
        """El emparejamiento más fuerte con el que se ofrece este documento.

        Es un resumen para ordenar y etiquetar; el detalle honesto está en
        `covered`, un `match` por grupo.
        """
        return max(
            (group.match for group in self.covered),
            key=lambda match: _MATCH_RANK[match],
            default=MATCH_AMBIGUOUS,
        )

    @property
    def vehicle_count(self) -> int:
        return sum(group.vehicle_count for group in self.covered)


@dataclass(frozen=True)
class CurveResolution:
    curves: tuple[ResolvedCurve, ...]
    without_curve: tuple[VehicleGroup, ...]


def _norm(value: str | None) -> str:
    return (value or "").strip().casefold()


def resolve_curves(
    groups: Iterable[VehicleGroup],
    attachments: Iterable[AttachmentRow],
) -> CurveResolution:
    """Empareja documentos con grupos de vehículos. Función pura y testeable.

    El orden de salida es estable: motor, luego CPL, luego id del origen. La
    pantalla lista documentos, y una lista que se reordena entre peticiones
    idénticas es un defecto de interfaz.
    """
    by_motor: dict[str, list[AttachmentRow]] = defaultdict(list)
    for attachment in attachments:
        by_motor[attachment.motor_type].append(attachment)

    found: dict[uuid.UUID, AttachmentRow] = {}
    covered: dict[uuid.UUID, list[CoveredGroup]] = defaultdict(list)
    without: list[VehicleGroup] = []

    for group in sorted(groups, key=lambda g: (g.motor_type, _norm(g.cpl))):
        candidates = by_motor.get(group.motor_type, [])
        if not candidates:
            without.append(group)
            continue

        exact = [c for c in candidates if group.cpl and _norm(c.cpl) == _norm(group.cpl)]
        if exact:
            selected, match = exact, MATCH_CPL
        elif len(candidates) == 1:
            selected, match = candidates, MATCH_MOTOR
        else:
            selected, match = candidates, MATCH_AMBIGUOUS

        for attachment in selected:
            found[attachment.id] = attachment
            covered[attachment.id].append(
                CoveredGroup(
                    cpl=group.cpl,
                    vehicle_count=group.vehicle_count,
                    match=match,
                )
            )

    curves = tuple(
        ResolvedCurve(
            attachment=attachment,
            covered=tuple(sorted(covered[attachment.id], key=lambda c: _norm(c.cpl))),
        )
        for attachment in sorted(
            found.values(),
            key=lambda a: (a.motor_type, _norm(a.cpl), a.source_id),
        )
    )
    return CurveResolution(curves=curves, without_curve=tuple(without))


async def _load_vehicle_groups(
    db: AsyncSession, fleet_ids: Sequence[uuid.UUID]
) -> list[VehicleGroup]:
    """Grupos (motor, CPL) del alcance.

    Incluye vehículos inactivos a propósito: Vehículos es un catálogo histórico
    y la curva de un vehículo dado de baja sigue explicando su historia.
    """
    rows = (
        await db.execute(
            text(
                "SELECT v.motor_type AS motor_type, "
                "NULLIF(TRIM(v.cpl), '') AS cpl, COUNT(*) AS vehicle_count "
                "FROM vehicles v "
                "WHERE v.fleet_id = ANY(:fleet_ids) AND v.motor_type IS NOT NULL "
                "GROUP BY 1, 2"
            ),
            {"fleet_ids": list(fleet_ids)},
        )
    ).mappings()
    return [
        VehicleGroup(
            motor_type=row["motor_type"],
            cpl=row["cpl"],
            vehicle_count=int(row["vehicle_count"]),
        )
        for row in rows
    ]


async def _load_attachments(db: AsyncSession, motor_types: Sequence[str]) -> list[AttachmentRow]:
    if not motor_types:
        return []
    rows = (
        await db.execute(
            text(
                "SELECT id, source_id, motor_type, cpl, original_filename, content_type, "
                "file_size, source_updated_at, fetch_status, "
                "(object_key IS NOT NULL AND fetch_status = 'ready') AS cached "
                "FROM motor_attachments "
                "WHERE is_active AND motor_type = ANY(:motor_types)"
            ),
            {"motor_types": list(motor_types)},
        )
    ).mappings()
    return [
        AttachmentRow(
            id=row["id"],
            source_id=int(row["source_id"]),
            motor_type=row["motor_type"],
            cpl=row["cpl"],
            original_filename=row["original_filename"],
            content_type=row["content_type"],
            file_size=row["file_size"],
            source_updated_at=row["source_updated_at"],
            fetch_status=row["fetch_status"],
            cached=bool(row["cached"]),
        )
        for row in rows
    ]


async def list_curves_for_fleets(
    db: AsyncSession, fleet_ids: Sequence[uuid.UUID]
) -> CurveResolution:
    """Curvas que aplican al alcance de flotas, y los motores que no tienen."""
    if not fleet_ids:
        return CurveResolution(curves=(), without_curve=())
    groups = await _load_vehicle_groups(db, fleet_ids)
    if not groups:
        return CurveResolution(curves=(), without_curve=())
    attachments = await _load_attachments(db, sorted({g.motor_type for g in groups}))
    return resolve_curves(groups, attachments)


# ---------------------------------------------------------------------------
# Binario: descarga perezosa y caché
# ---------------------------------------------------------------------------
async def _fetch_from_provider(source_id: int) -> tuple[bytes, str | None]:
    if not settings.navi_api_key:
        raise MotorCurveUnavailableError("not_configured")

    url = settings.navi_base_url.rstrip("/") + _ATTACHMENT_PATH.format(source_id=source_id)
    headers = {"X-API-Key": settings.navi_api_key}
    try:
        async with httpx.AsyncClient(timeout=settings.motor_curve_http_timeout_seconds) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        log.warning("curva de motor %s: fallo de red al descargar: %s", source_id, exc)
        raise MotorCurveUnavailableError("provider_unreachable") from exc

    if response.status_code == 404:
        raise MotorCurveUnavailableError("provider_not_found")
    if response.status_code >= 400:
        log.warning(
            "curva de motor %s: el proveedor respondió %s",
            source_id,
            response.status_code,
        )
        raise MotorCurveUnavailableError("provider_error")

    data = response.content
    if not data:
        raise MotorCurveUnavailableError("provider_error")
    if len(data) > _MAX_BINARY_BYTES:
        raise MotorCurveUnavailableError("too_large")
    return data, response.headers.get("content-type")


def _object_key(source_id: int, digest: str) -> str:
    """La huella entra en la llave: un documento reemplazado no sobrescribe al
    anterior, así que un rollback en el origen vuelve a servir el archivo viejo
    sin volver a bajarlo."""
    return f"{source_id}/{digest}.bin"


async def _mark_failed(db: AsyncSession, curve_id: uuid.UUID, reason: str) -> None:
    """Registra el fallo en su propia transacción: el enfriamiento sólo sirve si
    sobrevive a la petición que falló."""
    try:
        await db.execute(
            text(
                "UPDATE motor_attachments SET fetch_status = 'failed', fetch_error = :reason, "
                "fetched_at = :now, updated_at = :now WHERE id = :id"
            ),
            {"id": curve_id, "reason": reason, "now": datetime.now(UTC)},
        )
        await db.commit()
    except Exception:
        log.warning("curva de motor %s: no se pudo registrar el fallo", curve_id)
        await db.rollback()


async def get_curve_binary(
    db: AsyncSession, curve_id: uuid.UUID, fleet_ids: Sequence[uuid.UUID]
) -> tuple[str, str, bytes]:
    """Devuelve (nombre, content_type, bytes) de la curva.

    Lanza `LookupError` si la curva no existe o su motor no está en el alcance
    —404 y no 403: no se le confirma a nadie que existe un documento de un motor
    que su flota no usa— y `MotorCurveUnavailableError` si el binario no se puede
    obtener ahora.
    """
    row = (
        (
            await db.execute(
                text(
                    "SELECT a.id, a.source_id, a.motor_type, a.original_filename, "
                    "a.content_type, a.object_key, a.content_sha256, a.fetch_status, "
                    "a.fetch_error, a.fetched_at, EXISTS (SELECT 1 FROM vehicles v "
                    "WHERE v.fleet_id = ANY(:fleet_ids) AND v.motor_type = a.motor_type) "
                    "AS in_scope "
                    "FROM motor_attachments a WHERE a.id = :id AND a.is_active"
                ),
                {"id": curve_id, "fleet_ids": list(fleet_ids)},
            )
        )
        .mappings()
        .one_or_none()
    )

    if row is None or not row["in_scope"]:
        raise LookupError("curva no encontrada")

    filename = row["original_filename"] or f"curva-{row['motor_type']}.pdf"
    content_type = row["content_type"] or _FALLBACK_CONTENT_TYPE

    # 1. Caché. Si el objeto se perdió del almacenamiento se vuelve a bajar en
    #    vez de fallar: la caché es una optimización, no la fuente.
    if row["object_key"] and row["fetch_status"] == "ready":
        try:
            data = await object_storage.get_object(
                settings.motor_curves_minio_bucket, row["object_key"]
            )
            if data:
                return filename, content_type, data
            log.warning("curva de motor %s: objeto cacheado vacío", curve_id)
        except object_storage.ObjectStorageError as exc:
            log.warning("curva de motor %s: caché ilegible (%s); re-descargo", curve_id, exc)

    # 2. Enfriamiento: un origen caído no se martilla en cada render.
    if row["fetch_status"] == "failed" and row["fetched_at"] is not None:
        cooldown = timedelta(seconds=settings.motor_curve_retry_cooldown_seconds)
        if datetime.now(UTC) - row["fetched_at"] < cooldown:
            raise MotorCurveUnavailableError(row["fetch_error"] or "cooldown")

    # 3. Descarga y caché.
    try:
        data, provider_content_type = await _fetch_from_provider(int(row["source_id"]))
    except MotorCurveUnavailableError as exc:
        await _mark_failed(db, curve_id, exc.reason)
        raise

    digest = hashlib.sha256(data).hexdigest()
    object_key = _object_key(int(row["source_id"]), digest)
    effective_content_type = provider_content_type or content_type
    try:
        await object_storage.put_object(
            object_key,
            data,
            effective_content_type,
            bucket=settings.motor_curves_minio_bucket,
        )
    except object_storage.ObjectStorageError as exc:
        # El documento ya está en memoria: se entrega igual y se deja el estado
        # en 'failed' para que el próximo acceso reintente el cacheo. Negarle la
        # curva al usuario porque MinIO falló sería un fallo autoinfligido.
        log.warning("curva de motor %s: no se pudo cachear (%s)", curve_id, exc)
        await _mark_failed(db, curve_id, "storage_error")
        return filename, effective_content_type, data

    try:
        await db.execute(
            text(
                "UPDATE motor_attachments SET object_key = :key, content_sha256 = :digest, "
                "content_type = COALESCE(:content_type, content_type), "
                "fetch_status = 'ready', fetch_error = NULL, fetched_at = :now, "
                "updated_at = :now WHERE id = :id"
            ),
            {
                "id": curve_id,
                "key": object_key,
                "digest": digest,
                "content_type": provider_content_type,
                "now": datetime.now(UTC),
            },
        )
        await db.commit()
    except Exception:
        log.warning("curva de motor %s: no se pudo registrar la caché", curve_id)
        await db.rollback()

    return filename, effective_content_type, data
