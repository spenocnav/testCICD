"""Consulta y resolución auditable de anomalías ECM-GPS."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import FactCombustibleDaily
from app.models.distance_quality import DistanceQualityDecision
from app.models.user import User
from app.services.analytics_service import (
    _apply_vehicle_scope,
    _date_to_key,
    _effective_distance_expressions,
)


def _review_state(f: Any, action: Any, source: Any) -> Any:
    """Estado de revisión de una anomalía de distancia.

    `no_data` es un estado terminal, no una tarea. Cuando el día no tiene
    distancia en NINGUNA de las dos fuentes no hay nada que una persona pueda
    decidir: no se trata de elegir entre ECM y GPS, es que no hubo dato. Antes
    caía en `pending` y engrosaba el contador de pendientes de la tarjeta de
    calidad de datos con trabajo que no existe; son 1.619 filas del hecho.

    El orden del `case` importa: una decisión humana gana sobre `no_data`,
    porque alguien pudo excluir explícitamente un día que además no tenía dato
    y ese registro debe conservarse como lo que es.
    """
    sin_dato = and_(
        func.coalesce(f.kms_ecm, 0) == 0,
        func.coalesce(f.kms_gps, 0) == 0,
    )
    return case(
        (action.in_(("use_ecm", "use_gps")), "resolved"),
        (action == "exclude", "excluded"),
        (sin_dato, "no_data"),
        (source == "gps_auto", "auto_corrected"),
        else_="pending",
    )


async def list_distance_anomalies(
    db: AsyncSession,
    *,
    fleet_ids: Sequence[uuid.UUID],
    review_status: str | None = None,
    severity: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    f = FactCombustibleDaily
    effective = _effective_distance_expressions(f)
    review_state = _review_state(f, effective["action"], effective["source"])
    fields = (
        f.fact_row_id,
        f.vehicle_id,
        f.fecha,
        f.placa,
        f.motor_type,
        f.kms_ecm,
        f.kms_gps,
        effective["kms"].label("kms_effective"),
        f.distance_diff_km,
        f.distance_diff_pct,
        effective["source"].label("distance_source"),
        effective["status"].label("distance_quality_status"),
        f.distance_quality_reason,
        f.distance_quality_fingerprint,
        func.coalesce(f.gps_quality_valid, False).label("gps_quality_valid"),
        effective["action"].label("resolution_action"),
        review_state.label("review_status"),
    )
    stmt = select(*fields).where(f.distance_quality_status != "ok")
    count_stmt = select(func.count()).select_from(f).where(
        f.distance_quality_status != "ok"
    )
    stmt, empty = _apply_vehicle_scope(stmt, f.vehicle_id, fleet_ids)
    count_stmt, count_empty = _apply_vehicle_scope(count_stmt, f.vehicle_id, fleet_ids)
    if empty or count_empty:
        return [], 0
    if review_status:
        stmt = stmt.where(review_state == review_status)
        count_stmt = count_stmt.where(review_state == review_status)
    if severity:
        stmt = stmt.where(f.distance_quality_status == severity)
        count_stmt = count_stmt.where(f.distance_quality_status == severity)
    if date_from:
        stmt = stmt.where(f.date_key >= _date_to_key(date_from))
        count_stmt = count_stmt.where(f.date_key >= _date_to_key(date_from))
    if date_to:
        stmt = stmt.where(f.date_key <= _date_to_key(date_to))
        count_stmt = count_stmt.where(f.date_key <= _date_to_key(date_to))
    stmt = stmt.order_by(f.date_key.desc(), f.fact_row_id).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).mappings().all()
    total = int((await db.execute(count_stmt)).scalar_one())
    return [dict(row) for row in rows], total


async def create_manual_decision(
    db: AsyncSession,
    *,
    fact_row_id: str,
    action: str,
    justification: str,
    expected_fingerprint: str,
    actor: User,
    fleet_ids: Sequence[uuid.UUID],
) -> DistanceQualityDecision:
    stmt = select(FactCombustibleDaily).where(
        FactCombustibleDaily.fact_row_id == fact_row_id
    )
    stmt, empty = _apply_vehicle_scope(stmt, FactCombustibleDaily.vehicle_id, fleet_ids)
    fact = None if empty else (await db.execute(stmt)).scalar_one_or_none()
    if fact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registro no encontrado")
    if fact.distance_quality_fingerprint != expected_fingerprint:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La observación cambió; actualiza los datos antes de decidir.",
        )
    if action == "use_gps" and (
        not fact.gps_quality_valid or fact.kms_gps is None
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Los kilómetros GPS de esta observación no son utilizables.",
        )
    # Simétrico al anterior: no se puede publicar una distancia que no existe.
    # Sin esta guarda, `use_ecm` sobre una fila sin lectura de ECM publicaría
    # NULL y dejaría el día igual de vacío, pero marcado como resuelto.
    if action == "use_ecm" and fact.kms_ecm is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Esta observación no tiene kilómetros de ECM.",
        )

    decision = DistanceQualityDecision(
        fact_row_id=fact.fact_row_id,
        analytics_vehicle_id=fact.vehicle_id or "",
        date_key=int(fact.date_key or 0),
        observation_fingerprint=expected_fingerprint,
        origin="manual",
        action=action,
        reason_code=f"manual_{action}",
        justification=justification.strip(),
        observed_kms_ecm=fact.kms_ecm,
        observed_kms_gps=fact.kms_gps,
        threshold_version=fact.distance_threshold_version,
        actor_user_id=actor.id,
        actor_email=actor.email,
    )
    db.add(decision)
    await db.commit()
    await db.refresh(decision)
    return decision
