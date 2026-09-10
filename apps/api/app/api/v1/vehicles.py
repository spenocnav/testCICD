from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.fleets import _vehicle_read
from app.core.deps import SelectedFleets, require_any_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.vehicle import (
    FleetVehicleGroupRead,
    MotorCurveCoverage,
    MotorCurveRead,
    MotorCurvesResponse,
    MotorWithoutCurveRead,
    PaginatedVehicles,
    VehicleRead,
)
from app.services import fleet_service, motor_curve_service, vehicle_service

router = APIRouter(prefix="/vehicles", tags=["vehicles"])
CatalogUser = Annotated[User, Depends(require_any_permission("reportes.view", "novedades.view"))]
DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "",
    response_model=list[VehicleRead],
)
async def list_accessible_vehicles(
    user: CatalogUser,
    db: DbSession,
    selected_fleets: SelectedFleets,
    include_inactive: bool = Query(default=True),
    group_id: list[uuid.UUID] | None = Query(default=None),
) -> list[VehicleRead]:
    # Vehículos es un catálogo histórico: incluye flotas y vehículos activos
    # e inactivos dentro del alcance autorizado del usuario.
    accessible = await fleet_service.list_fleets_for_user(db, user, only_active=None)
    accessible_ids = {f.id for f in accessible}
    if selected_fleets:
        fleet_ids = [fid for fid in selected_fleets if fid in accessible_ids]
    else:
        fleet_ids = [f.id for f in accessible]
    if not fleet_ids:
        return []
    vehicles = await vehicle_service.list_vehicles_by_fleets(
        db,
        fleet_ids,
        only_active=None if include_inactive else True,
        group_ids=group_id,
    )
    return [_vehicle_read(v) for v in vehicles]


@router.get(
    "/groups",
    response_model=list[FleetVehicleGroupRead],
)
async def list_vehicle_groups(
    user: CatalogUser,
    db: DbSession,
    selected_fleets: SelectedFleets,
) -> list[FleetVehicleGroupRead]:
    """Árbol plano de grupos internos (categorías/subcategorías) de las flotas
    del alcance. Incluye inactivos para poder nombrar asignaciones históricas;
    la UI solo ofrece los activos al filtrar."""
    accessible = await fleet_service.list_fleets_for_user(db, user, only_active=None)
    accessible_ids = {f.id for f in accessible}
    if selected_fleets:
        fleet_ids = [fid for fid in selected_fleets if fid in accessible_ids]
    else:
        fleet_ids = [f.id for f in accessible]
    if not fleet_ids:
        return []
    groups = await vehicle_service.list_fleet_vehicle_groups(db, fleet_ids)
    return [FleetVehicleGroupRead.model_validate(g) for g in groups]


@router.get(
    "/paginated",
    response_model=PaginatedVehicles,
)
async def list_accessible_vehicles_paginated(
    user: CatalogUser,
    db: DbSession,
    selected_fleets: SelectedFleets,
    search: str | None = Query(default=None, max_length=120),
    marca: str | None = Query(default=None, max_length=120),
    motor_type: str | None = Query(default=None, max_length=120),
    group_id: list[uuid.UUID] | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    sort_by: str | None = Query(default=None, max_length=30),
    sort_order: str = Query(default="asc", pattern="^(asc|desc)$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PaginatedVehicles:
    # Vehículos es un catálogo histórico: incluye flotas y vehículos activos
    # e inactivos dentro del alcance autorizado del usuario.
    accessible = await fleet_service.list_fleets_for_user(db, user, only_active=None)
    accessible_ids = {f.id for f in accessible}
    if selected_fleets:
        fleet_ids = [fid for fid in selected_fleets if fid in accessible_ids]
    else:
        fleet_ids = [f.id for f in accessible]
    if not fleet_ids:
        return PaginatedVehicles(
            items=[], total=0, total_all=0, active_total=0, limit=limit, offset=offset
        )

    (
        vehicles,
        total,
        total_all,
        active_total,
    ) = await vehicle_service.list_vehicles_by_fleets_paginated(
        db,
        fleet_ids,
        search=(search or "").strip() or None,
        marca=(marca or "").strip() or None,
        motor_type=(motor_type or "").strip() or None,
        group_ids=group_id,
        only_active=is_active,
        sort_by=(sort_by or "").strip() or None,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
    )
    return PaginatedVehicles(
        items=[_vehicle_read(v) for v in vehicles],
        total=total,
        total_all=total_all,
        active_total=active_total,
        limit=limit,
        offset=offset,
    )


async def _scoped_fleet_ids(
    db: AsyncSession, user: User, selected_fleets: SelectedFleets
) -> list[uuid.UUID]:
    """Intersección del alcance pedido con las flotas autorizadas.

    Mismo criterio de catálogo histórico que los listados: `only_active=None`
    incluye flotas inactivas, porque un vehículo dado de baja sigue teniendo
    historia que explicar.
    """
    accessible = await fleet_service.list_fleets_for_user(db, user, only_active=None)
    accessible_ids = {f.id for f in accessible}
    if selected_fleets:
        return [fid for fid in selected_fleets if fid in accessible_ids]
    return [f.id for f in accessible]


@router.get(
    "/motor-curves",
    response_model=MotorCurvesResponse,
)
async def list_motor_curves(
    user: CatalogUser,
    db: DbSession,
    selected_fleets: SelectedFleets,
) -> MotorCurvesResponse:
    """Curvas de par y potencia de los motores presentes en el alcance.

    Sólo salen los motores que las flotas seleccionadas realmente usan: el
    catálogo de documentos es global y listarlo entero no diría nada del cliente.
    """
    fleet_ids = await _scoped_fleet_ids(db, user, selected_fleets)
    resolution = await motor_curve_service.list_curves_for_fleets(db, fleet_ids)
    return MotorCurvesResponse(
        curvas=[
            MotorCurveRead(
                id=curve.attachment.id,
                motor_type=curve.attachment.motor_type,
                cpl=curve.attachment.cpl,
                original_filename=curve.attachment.original_filename,
                content_type=curve.attachment.content_type,
                file_size=curve.attachment.file_size,
                source_updated_at=curve.attachment.source_updated_at,
                match=curve.match,
                vehicle_count=curve.vehicle_count,
                coverage=[
                    MotorCurveCoverage(
                        cpl=group.cpl,
                        vehicle_count=group.vehicle_count,
                        match=group.match,
                    )
                    for group in curve.covered
                ],
                estado="listo" if curve.attachment.cached else curve.attachment.fetch_status,
                cacheado=curve.attachment.cached,
            )
            for curve in resolution.curves
        ],
        sin_curva=[
            MotorWithoutCurveRead(
                motor_type=group.motor_type,
                cpl=group.cpl,
                vehicle_count=group.vehicle_count,
            )
            for group in resolution.without_curve
        ],
    )


@router.get(
    "/motor-curves/{curve_id}/file",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "Documento de la curva"},
        404: {"description": "La curva no existe o su motor no está en el alcance"},
        503: {"description": "El documento no se pudo obtener del proveedor"},
    },
)
async def download_motor_curve(
    user: CatalogUser,
    db: DbSession,
    selected_fleets: SelectedFleets,
    curve_id: Annotated[uuid.UUID, Path(description="Id de la curva")],
) -> Response:
    """Sirve el documento inline. La primera vez lo descarga de Navi Vehículos y
    lo deja cacheado; después sale de MinIO."""
    fleet_ids = await _scoped_fleet_ids(db, user, selected_fleets)
    try:
        filename, content_type, data = await motor_curve_service.get_curve_binary(
            db, curve_id, fleet_ids
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Curva no encontrada") from exc
    except motor_curve_service.MotorCurveUnavailableError as exc:
        # 503 y no 500: el documento existe, el origen no está disponible ahora.
        raise HTTPException(
            status_code=503,
            detail=f"No se pudo obtener el documento: {exc.label}.",
        ) from exc

    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
        },
    )
