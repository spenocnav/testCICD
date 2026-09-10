from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import ReportFleetIds, require_permission, require_platform_admin
from app.db.advisory_lock import OperationAlreadyRunningError
from app.db.session import get_db
from app.models.user import User
from app.schemas.mantenimiento import (
    CloudfleetMeterSyncList,
    CloudfleetMeterSyncStatus,
    CloudfleetSyncResult,
    ConfiabilidadSummary,
    ConfiabilidadTimePoint,
    DisponibilidadResponse,
    DisponibilidadTimePoint,
    GroupDisponibilidadBucket,
    OrdenDetalle,
    OrdenesResponse,
    OrdenesTypeTimePoint,
    PreventivoSummary,
    PreventivoTimePoint,
    ProgramacionRutina,
    RankingsResponse,
    ScopePlaca,
    TiemposTallerResponse,
)
from app.services import (
    cloudfleet_meter_sync_service,
    mantenimiento_service,
    tiempos_taller_service,
)
from app.services.cloudfleet_sync_service import run_cloudfleet_sync

router = APIRouter(prefix="/mantenimiento", tags=["mantenimiento"])

_VIEW = Depends(require_permission("mantenimiento.view"))


@router.post(
    "/sync",
    response_model=CloudfleetSyncResult,
)
async def sync_cloudfleet(
    user: Annotated[User, Depends(require_platform_admin)],
    full: bool = Query(
        default=False,
        description="true = full sync (ignora watermark de OTs); false = incremental.",
    ),
) -> CloudfleetSyncResult:
    """Fuerza una pasada de la réplica CloudFleet (vehículos, OTs, cronogramas).

    Mismo trabajo que hace el worker `cloudfleet-sync-worker`, disparado a mano.
    `run_cloudfleet_sync` maneja su propia sesión/commit (upsert idempotente).
    Queda registrado en `sync_run` con trigger=manual."""
    try:
        result = await run_cloudfleet_sync(
            full=full,
            trigger="manual",
            actor_user_id=user.id,
            actor_email=user.email,
        )
    except OperationAlreadyRunningError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya hay una sincronización de CloudFleet en curso.",
        ) from exc
    except Exception as exc:  # CloudFleet caído, API key faltante, etc.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"No se pudo sincronizar con CloudFleet: {exc}",
        ) from exc
    return CloudfleetSyncResult(
        vehiclesFetched=result["vehicles_fetched"],
        vehiclesUpserted=result["vehicles_upserted"],
        vehiclesMarkedAbsent=result["vehicles_marked_absent"],
        workOrdersFetched=result["work_orders_fetched"],
        workOrdersUpserted=result["work_orders_upserted"],
        schedulesFetched=result["schedules_fetched"],
        schedulesInserted=result["schedules_inserted"],
        metersTargets=result["meters_targets"],
        metersReadings=result["meters_readings"],
        metersSent=result["meters_sent"],
        metersSkipped=result["meters_skipped"],
        metersFailed=result["meters_failed"],
        metersUncertain=result["meters_uncertain"],
        metersReconciled=result["meters_reconciled"],
    )


@router.get(
    "/sync/meters",
    response_model=CloudfleetMeterSyncList,
    dependencies=[_VIEW],
)
async def list_cloudfleet_meter_sync(
    fleet_ids: ReportFleetIds,
    db: AsyncSession = Depends(get_db),
    status_filter: list[CloudfleetMeterSyncStatus] | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> CloudfleetMeterSyncList:
    """Expone el estado de odómetros/horómetros por flota y vehículo."""
    items, total = await cloudfleet_meter_sync_service.list_meter_sync_states(
        db,
        fleet_ids=fleet_ids,
        statuses=status_filter,
        limit=limit,
        offset=offset,
    )
    return CloudfleetMeterSyncList(items=items, total=total, limit=limit, offset=offset)


def _default_range(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    """Rango por defecto: tres meses calendario, desde el día primero."""
    end = date_to or date.today()
    if date_from:
        start = date_from
    else:
        # Incluye el mes de ``end``: con end=2026-07-17, el rango inicia
        # 2026-05-01 y no 90 días antes.
        month_index = end.year * 12 + end.month - 1 - 2
        start = date(month_index // 12, month_index % 12 + 1, 1)
    if start > end:
        raise HTTPException(status_code=422, detail="date_from no puede ser mayor que date_to")
    return start, end


@router.get("/placas", response_model=list[ScopePlaca], dependencies=[_VIEW])
async def list_placas(
    fleet_ids: ReportFleetIds,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    return await mantenimiento_service.list_scope_placas(db, fleet_ids)


# ---------------------------------------------------------------------------
# Disponibilidad
# ---------------------------------------------------------------------------
@router.get(
    "/disponibilidad/summary",
    response_model=DisponibilidadResponse,
    dependencies=[_VIEW],
)
async def disponibilidad_summary(
    fleet_ids: ReportFleetIds,
    placas: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    top: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    start, end = _default_range(date_from, date_to)
    result = await mantenimiento_service.get_disponibilidad(db, fleet_ids, placas, start, end)
    result["topPlacas"] = result["topPlacas"][:top]
    return result


@router.get(
    "/disponibilidad/timeseries",
    response_model=list[DisponibilidadTimePoint],
    dependencies=[_VIEW],
)
async def disponibilidad_timeseries(
    fleet_ids: ReportFleetIds,
    placas: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    start, end = _default_range(date_from, date_to)
    return await mantenimiento_service.get_disponibilidad_timeseries(
        db, fleet_ids, placas, start, end
    )


@router.get(
    "/disponibilidad/por-grupo",
    response_model=list[GroupDisponibilidadBucket],
    dependencies=[_VIEW],
)
async def disponibilidad_por_grupo(
    fleet_ids: ReportFleetIds,
    placas: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Should/downtime por grupo interno HOJA (`vehicle_group_id` exacto).

    Aditivo: no toca el summary. `group_id = null` agrupa placas sin grupo;
    los porcentajes y el rollup por niveles del árbol los hace el frontend.
    """
    start, end = _default_range(date_from, date_to)
    return await mantenimiento_service.get_disponibilidad_por_grupo(
        db, fleet_ids, placas, start, end
    )


# ---------------------------------------------------------------------------
# Programacion: preventivo, confiabilidad, ordenes, rankings
# ---------------------------------------------------------------------------
@router.get(
    "/preventivo/summary",
    response_model=PreventivoSummary,
    dependencies=[_VIEW],
)
async def preventivo_summary(
    fleet_ids: ReportFleetIds,
    placas: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    start, end = _default_range(date_from, date_to)
    return await mantenimiento_service.get_preventivo(db, fleet_ids, placas, start, end)


@router.get(
    "/preventivo/timeseries",
    response_model=list[PreventivoTimePoint],
    dependencies=[_VIEW],
)
async def preventivo_timeseries(
    fleet_ids: ReportFleetIds,
    placas: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    start, end = _default_range(date_from, date_to)
    return await mantenimiento_service.get_preventivo_timeseries(db, fleet_ids, placas, start, end)


@router.get(
    "/programacion/proximas",
    response_model=list[ProgramacionRutina],
    dependencies=[_VIEW],
)
async def proximas_programaciones(
    fleet_ids: ReportFleetIds,
    placas: list[str] | None = Query(default=None),
    horizon_days: int = Query(default=90, ge=1, le=365),
    limit: int = Query(default=200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    return await mantenimiento_service.get_proximas_programaciones(
        db, fleet_ids, placas, horizon_days=horizon_days, limit=limit
    )


@router.get(
    "/programacion/historico",
    response_model=list[ProgramacionRutina],
    dependencies=[_VIEW],
)
async def historico_programaciones(
    fleet_ids: ReportFleetIds,
    placas: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    start, end = _default_range(date_from, date_to)
    return await mantenimiento_service.get_historico_programaciones(
        db, fleet_ids, placas, start, end, limit=limit
    )


@router.get(
    "/confiabilidad/summary",
    response_model=ConfiabilidadSummary,
    dependencies=[_VIEW],
)
async def confiabilidad_summary(
    fleet_ids: ReportFleetIds,
    placas: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    start, end = _default_range(date_from, date_to)
    return await mantenimiento_service.get_confiabilidad(db, fleet_ids, placas, start, end)


@router.get(
    "/confiabilidad/timeseries",
    response_model=list[ConfiabilidadTimePoint],
    dependencies=[_VIEW],
)
async def confiabilidad_timeseries(
    fleet_ids: ReportFleetIds,
    placas: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    start, end = _default_range(date_from, date_to)
    return await mantenimiento_service.get_confiabilidad_timeseries(
        db, fleet_ids, placas, start, end
    )


@router.get("/ordenes", response_model=OrdenesResponse, dependencies=[_VIEW])
async def ordenes(
    fleet_ids: ReportFleetIds,
    placas: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    start, end = _default_range(date_from, date_to)
    return await mantenimiento_service.get_ordenes(db, fleet_ids, placas, start, end)


@router.get(
    "/ordenes/timeseries",
    response_model=list[OrdenesTypeTimePoint],
    dependencies=[_VIEW],
)
async def ordenes_timeseries(
    fleet_ids: ReportFleetIds,
    placas: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    start, end = _default_range(date_from, date_to)
    return await mantenimiento_service.get_ordenes_timeseries(db, fleet_ids, placas, start, end)


@router.get("/ordenes/list", response_model=list[OrdenDetalle], dependencies=[_VIEW])
async def ordenes_list(
    fleet_ids: ReportFleetIds,
    placas: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    start, end = _default_range(date_from, date_to)
    return await mantenimiento_service.get_ordenes_list(
        db, fleet_ids, placas, start, end, limit=limit
    )


@router.get(
    "/tiempos-taller",
    response_model=TiemposTallerResponse,
    dependencies=[_VIEW],
)
async def tiempos_taller(
    fleet_ids: ReportFleetIds,
    placas: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    order_number: int | None = Query(default=None, ge=1),
    limit: int = Query(default=500, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> TiemposTallerResponse:
    start, end = _default_range(date_from, date_to)
    projection, total = await tiempos_taller_service.get_tiempos_taller(
        db,
        fleet_ids,
        placas,
        start,
        end,
        order_number=order_number,
        limit=limit,
        offset=offset,
    )
    summary = {key: value for key, value in projection.items() if key != "items"}
    return TiemposTallerResponse(
        labelCatalogVersion="v1",
        stageDefinitions=list(tiempos_taller_service.STAGE_DEFINITIONS),
        summary=summary,
        items=projection["items"],
        total=total,
        limit=limit,
        offset=offset,
        truncated=total > offset + len(projection["items"]),
    )


@router.get("/rankings", response_model=RankingsResponse, dependencies=[_VIEW])
async def rankings(
    fleet_ids: ReportFleetIds,
    placas: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    start, end = _default_range(date_from, date_to)
    return await mantenimiento_service.get_rankings(db, fleet_ids, placas, start, end)
