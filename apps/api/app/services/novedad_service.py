from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.master_data import Vehicle
from app.models.novedad import Novedad, NovedadAttachment
from app.models.user import User
from app.schemas.novedad import NovedadAttachmentRead, NovedadListItem, NovedadRead


def attachment_read(novedad_id: uuid.UUID, attachment: NovedadAttachment) -> NovedadAttachmentRead:
    return NovedadAttachmentRead(
        id=attachment.id,
        filename=attachment.filename,
        content_type=attachment.content_type,
        size_bytes=attachment.size_bytes,
        download_url=f"/api/v1/novedades/{novedad_id}/attachments/{attachment.id}",
    )


def novedad_read(novedad: Novedad, *, include_cloudfleet_details: bool = False) -> NovedadRead:
    return NovedadRead(
        id=novedad.id,
        vehicle_id=novedad.vehicle_id,
        fleet_id=novedad.fleet_id,
        vehicle_code=novedad.vehicle_code,
        reported_at=novedad.reported_at,
        priority=novedad.priority,  # type: ignore[arg-type]
        odometer=novedad.odometer,
        comment=novedad.comment,
        send_mail=novedad.send_mail,
        cloudfleet_status=novedad.cloudfleet_status,  # type: ignore[arg-type]
        cloudfleet_issue_number=novedad.cloudfleet_issue_number,
        cloudfleet_error=novedad.cloudfleet_error if include_cloudfleet_details else None,
        cloudfleet_response=novedad.cloudfleet_response if include_cloudfleet_details else None,
        external_is_done=novedad.external_is_done,
        external_done_at=novedad.external_done_at,
        external_work_order_number=novedad.external_work_order_number,
        external_synced_at=novedad.external_synced_at,
        associated_labor_id=novedad.associated_labor_id,
        associated_labor_name=novedad.associated_labor_name,
        external_deleted_at=novedad.external_deleted_at,
        external_work_order_status=novedad.external_work_order_status,
        navifault_reference=novedad.navifault_reference,
        created_by=novedad.created_by.full_name if novedad.created_by else None,
        created_at=novedad.created_at,
        updated_at=novedad.updated_at,
        attachments=[attachment_read(novedad.id, a) for a in novedad.attachments],
    )


async def list_novedades(
    db: AsyncSession,
    *,
    fleet_ids: Sequence[uuid.UUID],
    include_cloudfleet_details: bool = False,
    plate: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    external_done: bool | None = None,
    group_ids: Sequence[uuid.UUID] | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[NovedadListItem], int]:
    # Conteo correlacionado: PostgreSQL usa ix_novedad_attachments_novedad_id
    # solo para las novedades de la página. El GROUP BY global anterior
    # recorría todos los adjuntos antes de aplicar LIMIT.
    attachment_count = (
        select(func.count(NovedadAttachment.id))
        .where(NovedadAttachment.novedad_id == Novedad.id)
        .correlate(Novedad)
        .scalar_subquery()
    )
    stmt = (
        select(Novedad, attachment_count.label("attachment_count"))
        .options(selectinload(Novedad.created_by))
    )
    count_stmt = select(func.count(Novedad.id))

    if fleet_ids:
        stmt = stmt.where(Novedad.fleet_id.in_(fleet_ids))
        count_stmt = count_stmt.where(Novedad.fleet_id.in_(fleet_ids))
    if plate:
        condition = func.lower(Novedad.vehicle_code).like(f"%{plate.lower()}%")
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    if group_ids:
        # Grupo interno del cliente: la novedad guarda la placa, así que el
        # filtro cruza contra la réplica de vehículos. El llamador manda los
        # nodos ya expandidos (nodo + descendientes).
        group_condition = (
            select(Vehicle.id)
            .where(
                Vehicle.plate == Novedad.vehicle_code,
                Vehicle.vehicle_group_id.in_(group_ids),
            )
            .correlate(Novedad)
            .exists()
        )
        stmt = stmt.where(group_condition)
        count_stmt = count_stmt.where(group_condition)
    if date_from:
        stmt = stmt.where(Novedad.reported_at >= date_from)
        count_stmt = count_stmt.where(Novedad.reported_at >= date_from)
    if date_to:
        stmt = stmt.where(Novedad.reported_at < date_to)
        count_stmt = count_stmt.where(Novedad.reported_at < date_to)
    if external_done is True:
        condition = Novedad.external_is_done.is_(True)
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    elif external_done is False:
        # "Abierta" incluye lo aún no verificado (NULL): para quien reporta,
        # una novedad sin confirmación de cierre sigue pendiente.
        condition = Novedad.external_is_done.is_not(True)
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    stmt = (
        stmt.order_by(Novedad.reported_at.desc(), Novedad.id.desc())
        .limit(limit)
        .offset(offset)
    )

    rows = (await db.execute(stmt)).all()
    total = await db.scalar(count_stmt)
    items = [
        NovedadListItem(
            id=n.id,
            vehicle_id=n.vehicle_id,
            fleet_id=n.fleet_id,
            vehicle_code=n.vehicle_code,
            reported_at=n.reported_at,
            priority=n.priority,  # type: ignore[arg-type]
            comment=n.comment,
            cloudfleet_status=n.cloudfleet_status,  # type: ignore[arg-type]
            cloudfleet_issue_number=n.cloudfleet_issue_number,
            cloudfleet_error=n.cloudfleet_error if include_cloudfleet_details else None,
            external_is_done=n.external_is_done,
            external_done_at=n.external_done_at,
            external_work_order_number=n.external_work_order_number,
            external_synced_at=n.external_synced_at,
            associated_labor_id=n.associated_labor_id,
            associated_labor_name=n.associated_labor_name,
            external_deleted_at=n.external_deleted_at,
            external_work_order_status=n.external_work_order_status,
            navifault_reference=n.navifault_reference,
            attachment_count=int(count or 0),
            created_by=n.created_by.full_name if n.created_by else None,
            created_at=n.created_at,
            updated_at=n.updated_at,
        )
        for n, count in rows
    ]
    return items, int(total or 0)


async def novedades_por_grupo(
    db: AsyncSession,
    *,
    fleet_ids: Sequence[uuid.UUID],
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[dict]:
    """Conteos por grupo interno HOJA del vehículo de la novedad.

    Una sola sentencia con agregación condicional: LEFT JOIN a la réplica de
    vehículos por placa (`vehicles.plate` es única global) y GROUP BY del
    `vehicle_group_id` exacto. `group_id` NULL agrupa novedades sin grupo o
    cuya placa no existe en la réplica. "Resuelta" = `external_is_done IS
    TRUE`; abierta = lo demás (incluye no verificadas), misma semántica que
    el filtro `external_done` del listado.
    """
    if not fleet_ids:
        return []
    stmt = (
        select(
            Vehicle.vehicle_group_id.label("group_id"),
            func.count(Novedad.id).label("total"),
            func.count(Novedad.id)
            .filter(Novedad.external_is_done.is_not(True))
            .label("abiertas"),
            func.count(Novedad.id)
            .filter(Novedad.external_is_done.is_(True))
            .label("resueltas"),
        )
        .select_from(Novedad)
        .outerjoin(Vehicle, Vehicle.plate == Novedad.vehicle_code)
        .where(Novedad.fleet_id.in_(fleet_ids))
        .group_by(Vehicle.vehicle_group_id)
    )
    if date_from:
        stmt = stmt.where(Novedad.reported_at >= date_from)
    if date_to:
        stmt = stmt.where(Novedad.reported_at < date_to)
    rows = (await db.execute(stmt)).mappings().all()
    buckets = [
        {
            "group_id": row["group_id"],
            "total": int(row["total"]),
            "abiertas": int(row["abiertas"]),
            "resueltas": int(row["resueltas"]),
        }
        for row in rows
    ]
    # Orden estable: total DESC, group_id como desempate (None primero).
    buckets.sort(
        key=lambda b: (
            -b["total"],
            b["group_id"] is not None,
            str(b["group_id"] or ""),
        )
    )
    return buckets


async def get_novedad(db: AsyncSession, novedad_id: uuid.UUID) -> Novedad | None:
    stmt = (
        select(Novedad)
        .where(Novedad.id == novedad_id)
        .options(
            selectinload(Novedad.attachments),
            selectinload(Novedad.created_by),
            selectinload(Novedad.vehicle),
        )
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def compute_request_fingerprint(
    *,
    vehicle_id: uuid.UUID,
    reported_at: datetime,
    priority: str,
    odometer: Decimal | None,
    comment: str | None,
    send_mail: bool,
) -> str:
    """Hash determinista de los campos de negocio.

    NO incluye el `idempotency_key` ni adjuntos ni PII sensible. El objetivo
    es detectar reintentos con la misma clave pero payload distinto. Cambios
    cosméticos (espacios al final del comment) no alteran el fingerprint
    porque normalizamos.
    """
    normalized_reported_at = (
        reported_at.replace(tzinfo=UTC)
        if reported_at.tzinfo is None
        else reported_at.astimezone(UTC)
    )
    payload = {
        "vehicle_id": str(vehicle_id),
        "reported_at": normalized_reported_at.isoformat(),
        "priority": priority,
        "odometer": str(odometer) if odometer is not None else None,
        "comment": (comment or "").strip(),
        "send_mail": bool(send_mail),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def find_by_idempotency_key(
    db: AsyncSession,
    *,
    created_by_id: uuid.UUID,
    idempotency_key: str,
) -> Novedad | None:
    """Busca una Novedad existente por (created_by_id, idempotency_key).

    Devuelve la primera fila; el caller debe verificar el fingerprint para
    distinguir "mismo payload" de "conflicto 409".
    """
    stmt = (
        select(Novedad)
        .where(
            Novedad.created_by_id == created_by_id,
            Novedad.idempotency_key == idempotency_key,
        )
        .options(
            selectinload(Novedad.attachments),
            selectinload(Novedad.created_by),
        )
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def create_local_novedad(
    db: AsyncSession,
    *,
    vehicle_id: uuid.UUID,
    fleet_id: uuid.UUID | None,
    created_by: User,
    vehicle_code: str,
    reported_at: datetime,
    reported_by_id: int,
    priority: str,
    odometer: Decimal | None,
    comment: str | None,
    send_mail: bool,
    idempotency_key: str | None = None,
    request_fingerprint: str | None = None,
    navifault_reference: str | None = None,
) -> Novedad:
    """Inserta la copia local de una Novedad ya aceptada por Cloudfleet."""
    novedad = Novedad(
        vehicle_id=vehicle_id,
        fleet_id=fleet_id,
        created_by_id=created_by.id,
        vehicle_code=vehicle_code,
        reported_at=reported_at,
        reported_by_id=reported_by_id,
        priority=priority,
        odometer=odometer,
        responsible_id=None,
        associated_labor_id=None,
        comment=comment,
        send_mail=send_mail,
        cloudfleet_status="pending",
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        navifault_reference=navifault_reference,
    )
    db.add(novedad)
    await db.flush()
    return novedad


async def enqueue_outbox(db: AsyncSession, *, novedad: Novedad) -> None:
    """Helper local: encola el outbox dentro de la misma transacción.

    Import diferido para evitar ciclo: novedad_service ↔ novedad_outbox_service.
    """
    from app.services.novedad_outbox_service import enqueue

    await enqueue(db, novedad=novedad)


async def add_attachment(
    db: AsyncSession,
    *,
    novedad: Novedad,
    bucket: str,
    object_key: str,
    filename: str,
    content_type: str,
    size_bytes: int,
) -> NovedadAttachment:
    attachment = NovedadAttachment(
        novedad_id=novedad.id,
        bucket=bucket,
        object_key=object_key,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
    )
    db.add(attachment)
    await db.flush()
    return attachment


def mark_sent(novedad: Novedad, *, issue_number: int | None, response: dict[str, Any]) -> None:
    novedad.cloudfleet_status = "sent"
    novedad.cloudfleet_issue_number = issue_number
    novedad.cloudfleet_response = response
    novedad.cloudfleet_error = None


def mark_failed(novedad: Novedad, *, error: str) -> None:
    novedad.cloudfleet_status = "failed"
    novedad.cloudfleet_error = error


async def delete_novedad(db: AsyncSession, novedad: Novedad) -> int:
    """Borra la copia local de una novedad y sus evidencias en MinIO.

    CloudFleet no ofrece DELETE de issues (responde 405), así que la issue
    remota sigue existiendo: quien borra debe marcarla hecha desde la UI de
    CloudFleet. Se registra el número para que ese rastro no se pierda.

    Los objetos se borran best-effort ANTES de la fila: `delete_object` nunca
    lanza por contrato, y un MinIO caído no debe impedir el borrado local.
    Un objeto huérfano es basura recuperable; una fila sin objetos no. La
    fila arrastra en cascada `novedad_attachments` y `novedad_outbox`.

    Devuelve cuántos adjuntos se intentaron borrar. El commit es del caller.
    """
    from app.services import object_storage

    attachments = list(novedad.attachments)
    for attachment in attachments:
        await object_storage.delete_object(attachment.bucket, attachment.object_key)
    await db.delete(novedad)
    await db.flush()
    return len(attachments)
