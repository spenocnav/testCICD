from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import SelectedFleets, require_permission, require_platform_admin
from app.db.advisory_lock import OperationAlreadyRunningError
from app.db.session import get_db
from app.models.novedad import (
    Novedad,
    NovedadAttachment,
    NovedadOutbox,
)
from app.models.user import User
from app.schemas.novedad import (
    GroupNovedadesBucket,
    NovedadRead,
    NovedadStatusSyncResult,
    PaginatedNovedades,
)
from app.services import (
    cloudfleet_service,
    fleet_service,
    novedad_service,
    novedad_status_sync_service,
    object_storage,
)
from app.services.cloudfleet_service import CloudfleetError, CloudfleetVehicleNotFoundError
from app.services.fleet_service import user_can_access_fleet
from app.services.novedad_outbox_service import (
    ClaimedOutbox,
    claim_one,
    process_claimed,
)
from app.services.object_storage import ObjectStorageError
from app.services.rbac import user_is_admin
from app.services.vehicle_service import get_vehicle_by_id

log = logging.getLogger(__name__)

router = APIRouter(prefix="/novedades", tags=["novedades"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
ViewUser = Annotated[User, Depends(require_permission("novedades.view"))]
EditUser = Annotated[User, Depends(require_permission("novedades.edit"))]
# Borrar una novedad destruye evidencia (adjuntos) y rompe la trazabilidad
# con la issue de CloudFleet, que NO se puede borrar por API (405). Por eso
# no basta `novedades.edit`: es una operación global de administrador.
PlatformAdmin = Annotated[User, Depends(require_platform_admin)]

MAX_ATTACHMENTS = 5
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 100 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}
IDEMPOTENCY_KEY_MAX_LEN = 128


def _target_fleet_ids(selected_fleets: list[uuid.UUID], accessible: list) -> list[uuid.UUID]:
    accessible_ids = [f.id for f in accessible]
    if selected_fleets:
        return [fid for fid in selected_fleets if fid in accessible_ids]
    return accessible_ids


def _ensure_can_access_novedad(user: User, novedad: Novedad) -> None:
    if novedad.fleet_id is None or not user_can_access_fleet(user, novedad.fleet_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Novedad no encontrada",
        )


def _comment_with_reporter_footer(comment: str | None, user: User) -> str:
    reporter_name = user.full_name.strip() or user.email
    footer = f"Reportado por {reporter_name}"
    clean_comment = (comment or "").strip()
    if not clean_comment:
        return footer
    return f"{clean_comment}\n\n{footer}"


def _configured_reported_by_id() -> int:
    if settings.cloudfleet_id_reportedby is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="CLOUDFLEET_ID_REPORTEDBY no está configurado",
        )
    return settings.cloudfleet_id_reportedby


def _validate_idempotency_key(raw: str | None) -> str:
    """Normaliza/valida la clave del header.

    Acepta un UUID textual; en cualquier caso, no admitimos vacío ni más de
    128 chars. La longitud está tomada de la práctica común (Stripe, etc.).
    """
    if raw is None:
        return ""
    value = raw.strip()
    if not value:
        return ""
    if len(value) > IDEMPOTENCY_KEY_MAX_LEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Idempotency-Key demasiado largo (max {IDEMPOTENCY_KEY_MAX_LEN})"
            ),
        )
    return value


async def _upload_attachments(
    db: AsyncSession,
    *,
    novedad: Novedad,
    attachments: list[UploadFile] | None,
) -> list[NovedadAttachment]:
    """Sube adjuntos a MinIO. Devuelve la lista de attachments persistidos.

    Si el caller quiere compensar (commit local falló), debe llamar a
    `compensate_attachments` con esta misma lista.
    """
    persisted: list[NovedadAttachment] = []
    if not attachments:
        return persisted
    if len(attachments) > MAX_ATTACHMENTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Máximo {MAX_ATTACHMENTS} archivos por novedad",
        )

    try:
        for attachment in attachments:
            content_type = attachment.content_type or ""
            if content_type not in ALLOWED_CONTENT_TYPES:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Formato no permitido para {attachment.filename}",
                )
            max_bytes = (
                MAX_VIDEO_BYTES if content_type.startswith("video/") else MAX_IMAGE_BYTES
            )
            limit_mb = max_bytes // (1024 * 1024)
            data = await attachment.read()
            if len(data) > max_bytes:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"{attachment.filename} supera el límite de {limit_mb} MB",
                )
            suffix = (
                Path(attachment.filename or "").suffix.lower()
                or ALLOWED_CONTENT_TYPES[content_type]
            )
            if suffix == ".jpeg":
                suffix = ".jpg"
            object_key = f"novedades/{novedad.id}/{uuid.uuid4().hex}{suffix}"
            stored = await object_storage.put_object(object_key, data, content_type)
            try:
                att = await novedad_service.add_attachment(
                    db,
                    novedad=novedad,
                    bucket=stored.bucket,
                    object_key=stored.object_key,
                    filename=attachment.filename or object_key.rsplit("/", 1)[-1],
                    content_type=content_type,
                    size_bytes=stored.size_bytes,
                )
            except Exception:
                # put_object ya escribió el objeto en MinIO: si add_attachment
                # falla (p. ej. flush del FK, IntegrityError), el objeto queda
                # huérfano porque la compensación externa sólo conoce los
                # `att` ya añadidos a `persisted`. Borramos el actual.
                await object_storage.delete_object(stored.bucket, stored.object_key)
                raise
            persisted.append(att)
    except ObjectStorageError as exc:
        await _compensate_attachments(persisted)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    except Exception:
        await _compensate_attachments(persisted)
        raise
    return persisted


async def _compensate_attachments(attachments: list[NovedadAttachment]) -> None:
    """Borra de MinIO los objetos subidos antes de un commit fallido.

    Regla: una vez que el commit local fue exitoso, NO se borran objetos
    aunque falle Cloudfleet. La compensación corre sólo si la lista de
    adjuntos todavía no fue commiteada.
    """
    for att in attachments:
        await object_storage.delete_object(att.bucket, att.object_key)


async def _dispatch_if_claimable(novedad_id: uuid.UUID) -> ClaimedOutbox | None:
    """Intenta reclamar el outbox y procesarlo inmediatamente (best-effort).

    El worker durable es la fuente de verdad; esto es una optimización para
    reducir latencia percibida. Si falla (otro worker ya tomó el item, red
    caída, etc.), la Novedad queda `pending` y el worker la recogerá.
    """
    from app.db.session import AsyncSessionLocal

    stale = timedelta(seconds=settings.novedad_outbox_stale_lock_seconds)
    async with AsyncSessionLocal() as db:
        claimed = await claim_one(db, stale_after=stale, novedad_id=novedad_id)
    if claimed is None:
        return None
    try:
        await process_claimed(claimed)
    except Exception as exc:  # el worker durable lo recuperará
        log.warning("dispatch inmediato falló para %s: %s", novedad_id, exc)
    return claimed


@router.get("", response_model=PaginatedNovedades)
async def list_novedades(
    user: ViewUser,
    db: DbSession,
    selected_fleets: SelectedFleets,
    plate: Annotated[str | None, Query(max_length=32)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    external_done: Annotated[
        bool | None,
        Query(
            description=(
                "true = sólo resueltas en CloudFleet; false = abiertas o aún no "
                "verificadas; omitido = todas."
            )
        ),
    ] = None,
    group_id: Annotated[
        list[uuid.UUID] | None,
        Query(
            description=(
                "Grupos internos del cliente (fleet_vehicle_groups.id, ya "
                "expandidos a nodo + descendientes); filtra por la placa del "
                "vehículo asignado a esos grupos."
            )
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginatedNovedades:
    accessible = list(await fleet_service.accessible_fleets(db, user))
    fleet_ids = _target_fleet_ids(selected_fleets, accessible)
    if not fleet_ids:
        return PaginatedNovedades(items=[], total=0, limit=limit, offset=offset)

    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La fecha inicial no puede ser posterior a la fecha final",
        )

    start = datetime.combine(date_from, time.min, tzinfo=UTC) if date_from else None
    end = (
        datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=UTC)
        if date_to
        else None
    )

    items, total = await novedad_service.list_novedades(
        db,
        fleet_ids=fleet_ids,
        include_cloudfleet_details=user_is_admin(user),
        plate=(plate or "").strip() or None,
        date_from=start,
        date_to=end,
        external_done=external_done,
        group_ids=group_id,
        limit=limit,
        offset=offset,
    )
    return PaginatedNovedades(items=items, total=total, limit=limit, offset=offset)


# Registrada ANTES de las rutas con path param (`/{novedad_id}`): "por-grupo"
# no es un UUID y caería en un 422 del path converter.
@router.get("/por-grupo", response_model=list[GroupNovedadesBucket])
async def novedades_por_grupo(
    user: ViewUser,
    db: DbSession,
    selected_fleets: SelectedFleets,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict]:
    """Conteos de novedades por grupo interno HOJA del vehículo reportado.

    Aditivo: no toca el listado. `group_id = null` agrupa novedades sin grupo;
    el rollup por niveles del árbol lo hace el frontend. "Resuelta" =
    confirmada en CloudFleet (`external_is_done`); abierta incluye lo aún no
    verificado, misma semántica que el filtro `external_done` del listado.
    """
    accessible = list(await fleet_service.accessible_fleets(db, user))
    fleet_ids = _target_fleet_ids(selected_fleets, accessible)
    if not fleet_ids:
        return []

    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La fecha inicial no puede ser posterior a la fecha final",
        )

    start = datetime.combine(date_from, time.min, tzinfo=UTC) if date_from else None
    end = (
        datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=UTC)
        if date_to
        else None
    )

    return await novedad_service.novedades_por_grupo(
        db,
        fleet_ids=fleet_ids,
        date_from=start,
        date_to=end,
    )


@router.post("/sync-status", response_model=NovedadStatusSyncResult)
async def sync_novedad_status(user: PlatformAdmin) -> NovedadStatusSyncResult:
    """Fuerza la réplica del estado real (`isDone`) desde CloudFleet.

    Mismo trabajo que el paso 4 del `daily-sync-worker`, disparado a mano.
    Queda en `sync_run` con kind=novedades, trigger=manual.
    """
    try:
        result = await novedad_status_sync_service.run_novedad_status_sync(
            trigger="manual",
            actor_user_id=user.id,
            actor_email=user.email,
        )
    except OperationAlreadyRunningError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya hay una sincronización de estado de novedades en curso.",
        ) from exc
    except CloudfleetError as exc:
        # El texto crudo del proveedor no sale al cliente (SEC-023).
        log.warning("novedad_status_sync_failed error=%s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo consultar el estado de las novedades en CloudFleet.",
        ) from exc
    return NovedadStatusSyncResult(**result)


@router.post("", response_model=NovedadRead, status_code=status.HTTP_201_CREATED)
async def create_novedad(
    user: EditUser,
    db: DbSession,
    vehicle_id: Annotated[uuid.UUID, Form()],
    reported_at: Annotated[datetime, Form()],
    priority: Annotated[str, Form()],
    odometer: Annotated[Decimal | None, Form()] = None,
    comment: Annotated[str | None, Form()] = None,
    send_mail: Annotated[bool, Form()] = False,
    images: Annotated[list[UploadFile] | None, File()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> NovedadRead:
    if priority not in {"low", "medium", "high"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La prioridad debe ser low, medium o high",
        )
    reported_by_id = _configured_reported_by_id()
    cloudfleet_comment = _comment_with_reporter_footer(comment, user)
    idem_key = _validate_idempotency_key(idempotency_key)

    vehicle = await get_vehicle_by_id(db, vehicle_id)
    if not vehicle or not vehicle.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehículo no encontrado")
    if vehicle.fleet_id is None or not user_can_access_fleet(user, vehicle.fleet_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sin acceso a la flota del vehículo",
        )

    fingerprint = novedad_service.compute_request_fingerprint(
        vehicle_id=vehicle.id,
        reported_at=reported_at,
        priority=priority,
        odometer=odometer,
        comment=cloudfleet_comment,
        send_mail=send_mail,
    )

    # Camino de idempotencia: si la misma clave + usuario ya tiene una
    # Novedad, comparamos fingerprint. Mismo fingerprint -> devolvemos la
    # existente (mismo recurso). Distinto -> 409 explícito.
    if idem_key:
        existing = await novedad_service.find_by_idempotency_key(
            db, created_by_id=user.id, idempotency_key=idem_key
        )
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Idempotency-Key reutilizada con un payload distinto"
                    ),
                )
            return novedad_service.novedad_read(
                existing, include_cloudfleet_details=user_is_admin(user)
            )

    # Cloudfleet es la fuente de verdad: no persistimos la novedad local si
    # no fue aceptada allí. Esto evita que una placa ausente en Cloudfleet (u
    # otro rechazo externo) genere registros desfasados en este portal.
    payload = cloudfleet_service.build_issue_payload(
        vehicle_code=vehicle.plate,
        reported_at=reported_at.isoformat(),
        reported_by_id=reported_by_id,
        priority=priority,
        odometer=odometer,
        comment=cloudfleet_comment,
        send_mail=send_mail,
    )
    try:
        cloudfleet_result = await cloudfleet_service.create_issue(payload)
    except CloudfleetVehicleNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "cloudfleet_vehicle_not_found"},
        ) from exc
    except CloudfleetError as exc:
        log.warning("Cloudfleet rechazó la novedad para %s: %s", vehicle.plate, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "cloudfleet_error"},
        ) from exc

    # La copia local y los metadatos de adjuntos se confirman juntos. Los
    # objetos ya subidos se eliminan si cualquier parte de la transacción falla.
    pending_attachments: list[NovedadAttachment] = []
    try:
        novedad = await novedad_service.create_local_novedad(
            db,
            vehicle_id=vehicle.id,
            fleet_id=vehicle.fleet_id,
            created_by=user,
            vehicle_code=vehicle.plate,
            reported_at=reported_at,
            reported_by_id=reported_by_id,
            priority=priority,
            odometer=odometer,
            comment=cloudfleet_comment,
            send_mail=send_mail,
            idempotency_key=idem_key or None,
            request_fingerprint=fingerprint,
        )
        novedad_service.mark_sent(
            novedad,
            issue_number=cloudfleet_result.issue_number,
            response=cloudfleet_result.response,
        )
        pending_attachments = await _upload_attachments(
            db, novedad=novedad, attachments=images
        )
        await db.commit()
    except IntegrityError:
        # Carrera en el índice único (created_by_id, idempotency_key): otro
        # request creó la misma clave mientras nosotros procesábamos.
        await db.rollback()
        await _compensate_attachments(pending_attachments)
        if idem_key:
            existing = await novedad_service.find_by_idempotency_key(
                db, created_by_id=user.id, idempotency_key=idem_key
            )
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "Idempotency-Key reutilizada con un payload"
                            " distinto"
                        ),
                    ) from None
                return novedad_service.novedad_read(
                    existing,
                    include_cloudfleet_details=user_is_admin(user),
                )
        # Sin clave de idempotencia o sin fila recuperable -> 409 genérico.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conflicto al crear la novedad (clave duplicada)",
        ) from None
    except Exception:
        await db.rollback()
        await _compensate_attachments(pending_attachments)
        raise

    stored = await novedad_service.get_novedad(db, novedad.id)
    if stored is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    return novedad_service.novedad_read(stored, include_cloudfleet_details=user_is_admin(user))


@router.get("/{novedad_id}", response_model=NovedadRead)
async def get_novedad(
    novedad_id: uuid.UUID,
    user: ViewUser,
    db: DbSession,
) -> NovedadRead:
    novedad = await novedad_service.get_novedad(db, novedad_id)
    if not novedad:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Novedad no encontrada")
    _ensure_can_access_novedad(user, novedad)
    return novedad_service.novedad_read(novedad, include_cloudfleet_details=user_is_admin(user))


@router.get("/{novedad_id}/attachments/{attachment_id}")
async def get_attachment(
    novedad_id: uuid.UUID,
    attachment_id: uuid.UUID,
    user: ViewUser,
    db: DbSession,
) -> Response:
    novedad = await novedad_service.get_novedad(db, novedad_id)
    if not novedad:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Novedad no encontrada")
    _ensure_can_access_novedad(user, novedad)
    attachment = next((a for a in novedad.attachments if a.id == attachment_id), None)
    if attachment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Adjunto no encontrado")
    try:
        data = await object_storage.get_object(attachment.bucket, attachment.object_key)
    except ObjectStorageError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return Response(
        content=data,
        media_type=attachment.content_type,
        headers={"Content-Disposition": f'inline; filename="{attachment.filename}"'},
    )


@router.delete("/{novedad_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_novedad(
    novedad_id: uuid.UUID,
    user: PlatformAdmin,
    db: DbSession,
) -> Response:
    """Borra la copia local de una novedad (fila + evidencias en MinIO).

    Sólo administrador de plataforma. La issue en CloudFleet NO se borra —el
    proveedor no lo permite— y queda anotada en el log para marcarla hecha
    desde su UI. Pensado para retirar novedades de prueba, no para operación.
    """
    novedad = await novedad_service.get_novedad(db, novedad_id)
    if not novedad:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Novedad no encontrada")
    _ensure_can_access_novedad(user, novedad)

    issue_number = novedad.cloudfleet_issue_number
    attachments_removed = await novedad_service.delete_novedad(db, novedad)
    await db.commit()
    log.warning(
        "novedad_deleted novedad_id=%s cloudfleet_issue_number=%s attachments=%s actor=%s",
        novedad_id,
        issue_number,
        attachments_removed,
        user.id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{novedad_id}/retry", response_model=NovedadRead)
async def retry_novedad(
    novedad_id: uuid.UUID,
    user: EditUser,
    db: DbSession,
) -> NovedadRead:
    """Reintenta el envío a Cloudfleet de una Novedad fallida.

    Re-eligibiliza el outbox AHORA (sin esperar el backoff programado) y
    dispara un intento inmediato. El worker durable también la verá.
    """
    from app.services.novedad_outbox_service import mark_retry

    novedad = await novedad_service.get_novedad(db, novedad_id)
    if not novedad:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Novedad no encontrada")
    _ensure_can_access_novedad(user, novedad)

    if novedad.cloudfleet_status == "sent":
        return novedad_service.novedad_read(
            novedad, include_cloudfleet_details=user_is_admin(user)
        )

    outbox = await _get_or_ensure_outbox(db, novedad.id)
    if not await mark_retry(db, outbox_id=outbox.id):
        # Si el outbox está en processing (otro worker), el retry manual no
        # lo roba: esperamos al worker. La Novedad ya quedó pending para
        # reflejar la intención del usuario.
        log.info("retry: outbox %s no en failed, esperando worker", outbox.id)

    novedad.cloudfleet_status = "pending"
    novedad.cloudfleet_error = None
    await db.commit()

    await _dispatch_if_claimable(novedad.id)

    stored = await novedad_service.get_novedad(db, novedad.id)
    if stored is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    return novedad_service.novedad_read(stored, include_cloudfleet_details=user_is_admin(user))


async def _get_or_ensure_outbox(db: AsyncSession, novedad_id: uuid.UUID):
    """Devuelve el outbox; si no existe (Novedad muy antigua), lo crea.

    Caso del backfill: si la migración corrió antes de que existiera el
    outbox para alguna Novedad legacy, garantizamos una fila aquí.
    """
    outbox = (
        await db.execute(
            select(NovedadOutbox).where(NovedadOutbox.novedad_id == novedad_id)
        )
    ).scalar_one_or_none()
    if outbox is not None:
        return outbox
    outbox = NovedadOutbox(
        novedad_id=novedad_id,
        status="pending",
        attempts=0,
        available_at=datetime.now(UTC),
    )
    db.add(outbox)
    await db.flush()
    return outbox
