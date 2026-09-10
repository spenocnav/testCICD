from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import (
    ReportFleetIds,
    SelectedFleets,
    require_permission,
    require_platform_admin,
    require_sync_ingest_key,
)
from app.db.session import get_db
from app.models.etl_trigger import EtlTriggerRequest
from app.models.user import User
from app.schemas.data_quality import (
    DataQualitySummary,
    DistanceAnomalyList,
    SyncRunIngest,
    SyncRunItem,
    SyncRunKind,
    SyncRunList,
    TrackingHealth,
)
from app.services import (
    data_quality_service,
    fleet_service,
    sync_run_service,
    tracking_health_service,
)

router = APIRouter(prefix="/data-quality", tags=["data-quality"])
ViewUser = Annotated[User, Depends(require_permission("calidad_datos.view"))]
AdminUser = Annotated[User, Depends(require_platform_admin)]


@router.get("/summary", response_model=DataQualitySummary)
async def summary(
    user: ViewUser,
    selected_fleets: SelectedFleets,
    db: AsyncSession = Depends(get_db),
) -> DataQualitySummary:
    accessible = await fleet_service.accessible_fleets(db, user)
    accessible_ids = {fleet.id for fleet in accessible}
    fleet_ids = (
        [fleet_id for fleet_id in selected_fleets if fleet_id in accessible_ids]
        if selected_fleets
        else list(accessible_ids)
    )
    return await data_quality_service.get_summary(
        db,
        fleet_ids=fleet_ids,
        stale_after_hours=settings.data_quality_stale_hours,
    )


@router.get("/distance-anomalies", response_model=DistanceAnomalyList)
async def distance_anomalies(
    user: AdminUser,
    fleet_ids: ReportFleetIds,
    # El patrón tiene que listar los MISMOS estados que puede devolver
    # `DistanceAnomalyItem.review_status`. Que se desincronicen es un defecto
    # silencioso: la respuesta trae un estado por el que la pantalla no puede
    # filtrar, y el filtro responde 422 antes de llegar al servicio.
    review_status: str | None = Query(
        default=None, pattern="^(pending|auto_corrected|resolved|excluded|no_data)$"
    ),
    severity: str | None = Query(default=None, pattern="^(warning|critical)$"),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> DistanceAnomalyList:
    from app.services import distance_quality_service

    items, total = await distance_quality_service.list_distance_anomalies(
        db,
        fleet_ids=fleet_ids,
        review_status=review_status,
        severity=severity,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return DistanceAnomalyList(items=items, total=total, limit=limit, offset=offset)


@router.get("/tracking-etiquetas", response_model=TrackingHealth)
async def tracking_etiquetas(
    user: ViewUser,
    db: AsyncSession = Depends(get_db),
) -> TrackingHealth:
    """Semáforo del pipeline de etiquetas (worker sidecar + ingestor).

    Se sirve desde `sync_state`, no del runtime privado del sidecar: el API no
    lo monta y INTEGRATION.md prohíbe publicarlo. El estado es global del
    pipeline, no por flota, así que no se filtra por alcance; tampoco devuelve
    OTs, placas ni comentarios.
    """
    return TrackingHealth(**await tracking_health_service.get_tracking_health(db))


@router.get("/sync-runs", response_model=SyncRunList)
async def sync_runs(
    user: ViewUser,
    kind: SyncRunKind | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> SyncRunList:
    """Historial paginado de corridas de sync (auditoría). Más recientes primero.

    `active` trae las corridas EN CURSO con su tiempo transcurrido: `sync_run`
    solo se escribe al cerrar, así que sin esto la auditoría se veía vacía
    mientras el ETL trabajaba. No entra en la paginación: son pocas y la UI las
    muestra siempre arriba, así que `total` cuenta solo corridas cerradas."""
    runs, total = await sync_run_service.list_recent(kind=kind, limit=limit, offset=offset)
    active = await sync_run_service.list_active(kind=kind)
    return SyncRunList(
        active=active,
        total=total,
        limit=limit,
        offset=offset,
        items=[
            SyncRunItem(
                id=str(run.id),
                kind=run.kind,
                trigger=run.trigger,
                mode=run.mode,
                status=run.status,
                started_at=run.started_at,
                finished_at=run.finished_at,
                duration_ms=run.duration_ms,
                result=run.result,
                error=run.error,
                actor_email=run.actor_email,
            )
            for run in runs
        ],
    )


@router.post("/reportes/trigger", status_code=status.HTTP_202_ACCEPTED)
async def trigger_reportes_etl(
    user: Annotated[User, Depends(require_platform_admin)],
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Encola una corrida del ETL de reportes (InformesRendimiento).

    No ejecuta nada acá: inserta una solicitud `pending` en
    `etl_trigger_request`; el contenedor `reportes-etl-worker` la reclama en su
    próximo poll (segundos) y corre extract → transform → load. La corrida
    queda en `sync_run` (kind=reportes, trigger=manual). Si ya hay una
    solicitud pendiente o corriendo, no se apila otra (409)."""
    existing = (
        await db.execute(
            select(EtlTriggerRequest.status)
            .where(EtlTriggerRequest.status.in_(["pending", "running"]))
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Ya hay una corrida del ETL en curso o encolada; "
                "espera a que termine (revisa la Auditoría de syncs)."
            ),
        )

    request = EtlTriggerRequest(
        status="pending",
        requested_by_user_id=user.id,
        requested_by_email=user.email,
    )
    db.add(request)
    await db.commit()
    return {"status": "queued", "request_id": str(request.id)}


@router.post(
    "/sync-runs",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_sync_ingest_key)],
)
async def ingest_sync_run(payload: SyncRunIngest) -> dict[str, str]:
    """Ingesta de auditoría desde un pipeline externo (InformesRendimiento).

    Autenticado con el header X-Sync-Ingest-Key (no con sesión de usuario).
    `kind` se fija a "reportes"; el pipeline solo reporta sus propias corridas."""
    await sync_run_service.record(
        kind="reportes",
        trigger=payload.trigger,
        mode=payload.mode,
        status=payload.status,
        started_at=payload.started_at,
        finished_at=payload.finished_at,
        result=payload.result,
        error=payload.error,
        actor_email=payload.actor_email,
    )
    return {"status": "recorded"}
