"""Endpoints del módulo de reportes "Análisis Ralentí" (`/reportes/ralenti/*`).

Sub-router aparte de `reportes.py` para no seguir engordándolo; comparte tag,
permiso (`reportes.view`), alcance de flota (`ReportFleetIds`) y la validación
del rango de fechas. La lógica vive en `ralenti_service`.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.reportes import validate_date_range
from app.core.deps import ReportFleetIds, require_permission
from app.db.session import get_db
from app.schemas.ralenti import (
    DurationBucketKey,
    PaginatedRalentiEvents,
    RalentiEventRead,
    RalentiHeatCell,
    RalentiPlacaRow,
    RalentiSortField,
    RalentiSummary,
    RalentiTimePoint,
    RpmBucketKey,
)
from app.services import ralenti_service
from app.services.ralenti_service import Granularity, RalentiFilters

router = APIRouter(
    prefix="/reportes/ralenti",
    tags=["reportes"],
    dependencies=[Depends(require_permission("reportes.view"))],
)


def ralenti_filters(
    vehicle_id: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    duration_bucket: DurationBucketKey | None = Query(default=None),
    rpm_bucket: RpmBucketKey | None = Query(default=None),
    _: None = Depends(validate_date_range),
) -> RalentiFilters:
    """Filtros comunes del módulo. El rango de fechas es inclusivo sobre `fecha`."""
    return RalentiFilters(
        vehicle_id=vehicle_id,
        motor_type=motor_type,
        date_from=date_from,
        date_to=date_to,
        duration_bucket=duration_bucket,
        rpm_bucket=rpm_bucket,
    )


@router.get("/summary", response_model=RalentiSummary)
async def ralenti_summary(
    fleet_ids: ReportFleetIds,
    filters: RalentiFilters = Depends(ralenti_filters),
    db: AsyncSession = Depends(get_db),
) -> RalentiSummary:
    data = await ralenti_service.get_summary(db, filters=filters, fleet_ids=fleet_ids)
    return RalentiSummary.model_validate(data)


@router.get("/por-placa", response_model=list[RalentiPlacaRow])
async def ralenti_por_placa(
    fleet_ids: ReportFleetIds,
    filters: RalentiFilters = Depends(ralenti_filters),
    db: AsyncSession = Depends(get_db),
) -> list[RalentiPlacaRow]:
    rows = await ralenti_service.list_por_placa(db, filters=filters, fleet_ids=fleet_ids)
    return [RalentiPlacaRow.model_validate(r) for r in rows]


@router.get("/heatmap", response_model=list[RalentiHeatCell])
async def ralenti_heatmap(
    fleet_ids: ReportFleetIds,
    precision: int = Query(default=3, ge=2, le=4),
    filters: RalentiFilters = Depends(ralenti_filters),
    db: AsyncSession = Depends(get_db),
) -> list[RalentiHeatCell]:
    cells = await ralenti_service.list_heatmap(
        db, filters=filters, fleet_ids=fleet_ids, precision=precision
    )
    return [RalentiHeatCell.model_validate(c) for c in cells]


@router.get("/timeseries", response_model=list[RalentiTimePoint])
async def ralenti_timeseries(
    fleet_ids: ReportFleetIds,
    granularity: Granularity = Query(default="daily"),
    filters: RalentiFilters = Depends(ralenti_filters),
    db: AsyncSession = Depends(get_db),
) -> list[RalentiTimePoint]:
    points = await ralenti_service.list_timeseries(
        db, filters=filters, fleet_ids=fleet_ids, granularity=granularity
    )
    return [RalentiTimePoint.model_validate(p) for p in points]


@router.get("/events", response_model=PaginatedRalentiEvents)
async def ralenti_events(
    fleet_ids: ReportFleetIds,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort_by: RalentiSortField = Query(default="inicio"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    filters: RalentiFilters = Depends(ralenti_filters),
    db: AsyncSession = Depends(get_db),
) -> PaginatedRalentiEvents:
    items, total = await ralenti_service.list_events(
        db,
        filters=filters,
        fleet_ids=fleet_ids,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )
    return PaginatedRalentiEvents(
        items=[RalentiEventRead.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )
