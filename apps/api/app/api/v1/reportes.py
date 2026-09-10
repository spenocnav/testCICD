from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import ReportFleetIds, require_permission, require_platform_admin
from app.db.session import get_db
from app.models.user import User
from app.schemas.analytics import (
    CalificacionResponse,
    CombustibleDailyRead,
    CombustibleSummary,
    CombustibleTimePoint,
    FactorCargaSummary,
    FaultEventRead,
    FaultParetoItem,
    FaultSeverityBucket,
    FaultSummary,
    FaultTimelineItem,
    FaultTimePoint,
    GroupCombustibleBucket,
    GroupFallasBucket,
    GroupHabitosBucket,
    HabitoEventRead,
    HabitoMapPoint,
    HabitoSummary,
    HabitoTimePoint,
    HabitoTypeBucket,
    LocationPointRead,
    MotorTypeBucket,
    OperativoMonthlyPoint,
    PaginatedCombustible,
    PaginatedFallas,
    PaginatedHabitos,
    PaginatedTimeline,
    PedalSummary,
    UbicacionesPage,
    VehicleRankingItem,
    VehicleRead,
)
from app.schemas.calificacion_config import (
    CalificacionConfigHistory,
    CalificacionConfigResponse,
    CalificacionConfigValues,
)
from app.schemas.data_quality import DistanceResolutionCreate
from app.services import (
    analytics_service,
    calificacion_config_service,
    geotab_service,
    ubicaciones_service,
)
from app.services.calificacion_config import (
    CalificacionConfigError,
    config_from_mapping,
    config_to_mapping,
)
from app.services.fleet_service import user_can_access_fleet

router = APIRouter(prefix="/reportes", tags=["reportes"])
AdminUser = Annotated[User, Depends(require_platform_admin)]


# Métricas válidas para el ranking por vehículo de combustible. Coincide con
# analytics_service.RANKING_METRICS. Declarar como Literal permite que FastAPI
# responda 422 directamente para valores desconocidos.
RankingMetric = Literal[
    "comb",
    "kms_ecm",
    "km_gal",
    "km_m3",
    "gal_hr",
    "m3_hr",
    "pct_exceso_rpm",
    "pct_ralenti",
]
# Métricas expresadas en una unidad de combustible concreta: una petición que
# las cruce con el otro `fuel_kind` es inválida, no una lista vacía.
_LIQUID_ONLY_METRICS = {"km_gal", "gal_hr"}
_GAS_ONLY_METRICS = {"km_m3", "m3_hr"}
FuelKind = Literal["liquid", "gas"]


def validate_date_range(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
) -> None:
    """Dependencia reutilizable: 422 si `date_from > date_to`."""
    if (
        date_from is not None
        and date_to is not None
        and date_from > date_to
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="date_from no puede ser mayor que date_to",
        )


@router.get(
    "/vehiculos",
    response_model=list[VehicleRead],
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def list_vehiculos(
    fleet_ids: ReportFleetIds,
    db: AsyncSession = Depends(get_db),
) -> list[VehicleRead]:
    vehicles = await analytics_service.list_vehicles(
        db, fleet_ids=fleet_ids
    )
    return [VehicleRead.model_validate(v) for v in vehicles]


# ---------------------------------------------------------------------------
# Calificación
# ---------------------------------------------------------------------------


@router.get(
    "/calificacion",
    response_model=CalificacionResponse,
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def calificacion(
    fleet_ids: ReportFleetIds,
    vehicle_id: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> CalificacionResponse:
    data = await analytics_service.get_calificacion(
        db,
        vehicle_id=vehicle_id,
        motor_type=motor_type,
        fleet_ids=fleet_ids,
        date_from=date_from,
        date_to=date_to,
    )
    return CalificacionResponse(**data)


# ---------------------------------------------------------------------------
# Calibración de la calificación (por flota)
# ---------------------------------------------------------------------------
#
# `fleet_id` es OBLIGATORIO en las cuatro operaciones: una calibración pertenece
# a una flota concreta. No se acepta "todas", porque escribir la misma fórmula
# en N flotas de una sola petición es una decisión que debe tomarse flota por
# flota, y leer "la de todas" no significa nada cuando difieren.
#
# El acceso a la flota se verifica SIEMPRE en el backend, con
# `_verificar_flota`, y nunca se delega al filtro del front. Se responde 404 y
# no 403, igual que `require_fleet_access`: a un usuario sin alcance no se le
# confirma que la flota existe.
#
# Los permisos son asimétricos a propósito: leer la calibración vigente es parte
# de entender el reporte (`reportes.view`), pero cambiarla altera el puntaje de
# toda la flota y su historial es información de auditoría, así que ambos exigen
# `reportes.edit`.


def _verificar_flota(user: User, fleet_id: uuid.UUID) -> None:
    """404 si el usuario no tiene la flota en su alcance."""
    if not user_can_access_fleet(user, fleet_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Flota no encontrada",
        )


def _config_response(
    efectivo: calificacion_config_service.EffectiveCalificacionConfig,
) -> CalificacionConfigResponse:
    return CalificacionConfigResponse(
        config=config_to_mapping(efectivo.config),  # type: ignore[arg-type]
        origen=efectivo.origen,
        fleet_id=str(efectivo.fleet_id) if efectivo.fleet_id else None,
        actualizado_en=efectivo.actualizado_en,
        actualizado_por=efectivo.actualizado_por,
    )


@router.get(
    "/calificacion/config",
    response_model=CalificacionConfigResponse,
)
async def get_calificacion_config(
    user: Annotated[User, Depends(require_permission("reportes.view"))],
    fleet_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
) -> CalificacionConfigResponse:
    """Calibración vigente de una flota, con los valores por defecto al lado."""
    _verificar_flota(user, fleet_id)
    efectivo = await calificacion_config_service.get_fleet_config(db, fleet_id)
    return _config_response(efectivo)


@router.put(
    "/calificacion/config",
    response_model=CalificacionConfigResponse,
)
async def put_calificacion_config(
    payload: CalificacionConfigValues,
    user: Annotated[User, Depends(require_permission("reportes.edit"))],
    fleet_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
) -> CalificacionConfigResponse:
    """Guarda una versión nueva de la calibración de la flota.

    `CalificacionConfigError` sale como 400 con su mensaje tal cual: está
    escrito para que lo lea la persona que calibró, y convertirlo en un 500
    genérico —o en el 422 anidado de Pydantic— obligaría a adivinar qué
    rechazó el servidor.

    El cuerpo se declara como `CalificacionConfigValues` y NO como
    `CalificacionConfigPayload` justo por eso: el `model_validator` del payload
    corre durante la validación de la petición, o sea ANTES de entrar acá, y
    convierte el motivo en un 422 con el mensaje enterrado en `detail[0].msg`.
    Validando con `config_from_mapping` dentro del handler, la única función que
    valida es la misma que valida lo que se lee de la base, y su mensaje llega
    íntegro al cliente.
    """
    _verificar_flota(user, fleet_id)
    try:
        config = config_from_mapping(payload.model_dump())
        efectivo = await calificacion_config_service.save_fleet_config(
            db,
            fleet_id=fleet_id,
            config=config,
            actor_user_id=user.id,
            actor_email=user.email,
        )
    except CalificacionConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    await db.commit()
    return _config_response(efectivo)


@router.delete(
    "/calificacion/config",
    response_model=CalificacionConfigResponse,
)
async def delete_calificacion_config(
    user: Annotated[User, Depends(require_permission("reportes.edit"))],
    fleet_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
) -> CalificacionConfigResponse:
    """Devuelve la flota a los valores por defecto.

    No borra el historial: registra una versión más marcada como reset, con su
    autor y su fecha. Quién decidió volver al default también es auditoría.
    """
    _verificar_flota(user, fleet_id)
    efectivo = await calificacion_config_service.reset_fleet_config(
        db,
        fleet_id=fleet_id,
        actor_user_id=user.id,
        actor_email=user.email,
    )
    await db.commit()
    return _config_response(efectivo)


@router.get(
    "/calificacion/config/history",
    response_model=CalificacionConfigHistory,
)
async def get_calificacion_config_history(
    user: Annotated[User, Depends(require_permission("reportes.edit"))],
    fleet_id: uuid.UUID = Query(...),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> CalificacionConfigHistory:
    """Quién calibró qué y cuándo. Exige `reportes.edit`: es auditoría."""
    _verificar_flota(user, fleet_id)
    items = await calificacion_config_service.config_history(
        db, fleet_id=fleet_id, limit=limit
    )
    return CalificacionConfigHistory(items=items)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Combustible / Rendimiento
# ---------------------------------------------------------------------------


@router.get(
    "/combustible/daily",
    response_model=PaginatedCombustible,
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def combustible_daily(
    fleet_ids: ReportFleetIds,
    fuel_kind: FuelKind = Query(default="liquid"),
    vehicle_id: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=50, le=200, ge=1),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> PaginatedCombustible:
    rows, total = await analytics_service.list_combustible_daily(
        db,
        vehicle_id=vehicle_id,
        motor_type=motor_type,
        fuel_kind=fuel_kind,
        fleet_ids=fleet_ids,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return PaginatedCombustible(
        items=[CombustibleDailyRead.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/combustible/{fact_row_id}/distance-resolution")
async def resolve_combustible_distance(
    fact_row_id: str,
    payload: DistanceResolutionCreate,
    user: AdminUser,
    fleet_ids: ReportFleetIds,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    from app.services import distance_quality_service

    decision = await distance_quality_service.create_manual_decision(
        db,
        fact_row_id=fact_row_id,
        action=payload.action,
        justification=payload.reason,
        expected_fingerprint=payload.expected_fingerprint,
        actor=user,
        fleet_ids=fleet_ids,
    )
    return {"status": "applied", "decision_id": str(decision.id)}


@router.get(
    "/combustible/summary",
    response_model=CombustibleSummary,
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def combustible_summary(
    fleet_ids: ReportFleetIds,
    fuel_kind: FuelKind = Query(default="liquid"),
    vehicle_id: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> CombustibleSummary:
    data = await analytics_service.get_combustible_summary(
        db,
        vehicle_id=vehicle_id,
        motor_type=motor_type,
        fuel_kind=fuel_kind,
        fleet_ids=fleet_ids,
        date_from=date_from,
        date_to=date_to,
    )
    return CombustibleSummary(**data)


@router.get(
    "/combustible/timeseries",
    response_model=list[CombustibleTimePoint],
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def combustible_timeseries(
    fleet_ids: ReportFleetIds,
    granularity: Literal["daily", "monthly"] = Query(default="daily"),
    fuel_kind: FuelKind = Query(default="liquid"),
    vehicle_id: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> list[CombustibleTimePoint]:
    fids = fleet_ids
    if granularity == "monthly":
        rows = await analytics_service.get_combustible_timeseries_monthly(
            db, vehicle_id=vehicle_id, motor_type=motor_type, fuel_kind=fuel_kind,
            fleet_ids=fids, date_from=date_from, date_to=date_to,
        )
    else:
        rows = await analytics_service.get_combustible_timeseries_daily(
            db, vehicle_id=vehicle_id, motor_type=motor_type, fuel_kind=fuel_kind,
            fleet_ids=fids, date_from=date_from, date_to=date_to,
        )
    return [CombustibleTimePoint(**r) for r in rows]


@router.get(
    "/combustible/ranking",
    response_model=list[VehicleRankingItem],
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def combustible_ranking(
    fleet_ids: ReportFleetIds,
    metric: RankingMetric = Query(default="comb"),
    sort_order: Literal["asc", "desc"] = Query(default="desc"),
    fuel_kind: FuelKind = Query(default="liquid"),
    vehicle_id: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    # `0` significa sin límite; el valor por defecto conserva el Top 10.
    limit: int = Query(default=10, le=500, ge=0),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> list[VehicleRankingItem]:
    if (fuel_kind == "gas" and metric in _LIQUID_ONLY_METRICS) or (
        fuel_kind == "liquid" and metric in _GAS_ONLY_METRICS
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La métrica de rendimiento no corresponde a la unidad seleccionada",
        )
    rows = await analytics_service.get_vehicle_ranking(
        db, metric=metric, vehicle_id=vehicle_id, motor_type=motor_type,
        fuel_kind=fuel_kind,
        fleet_ids=fleet_ids,
        date_from=date_from, date_to=date_to, limit=limit, sort_order=sort_order,
    )
    return [VehicleRankingItem(**r) for r in rows]


@router.get(
    "/combustible/por-grupo",
    response_model=list[GroupCombustibleBucket],
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def combustible_por_grupo(
    fleet_ids: ReportFleetIds,
    fuel_kind: FuelKind = Query(default="liquid"),
    vehicle_id: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> list[GroupCombustibleBucket]:
    """Totales ADITIVOS por grupo hoja del vehículo (`vehicle_group_id`).

    Una fila por grupo presente en los datos, incluida `group_id=null` para
    vehículos sin grupo. El rollup por niveles del árbol y las razones las
    calcula el cliente después de sumar.
    """
    rows = await analytics_service.combustible_por_grupo(
        db,
        vehicle_id=vehicle_id,
        motor_type=motor_type,
        fuel_kind=fuel_kind,
        fleet_ids=fleet_ids,
        date_from=date_from,
        date_to=date_to,
    )
    return [GroupCombustibleBucket(**r) for r in rows]


# ---------------------------------------------------------------------------
# Hábitos operativos (rangos de RPM, ralentí, pedal)
# ---------------------------------------------------------------------------


@router.get(
    "/operativos/timeseries",
    response_model=list[OperativoMonthlyPoint],
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def operativos_timeseries(
    fleet_ids: ReportFleetIds,
    granularity: Literal["daily", "monthly"] = Query(default="monthly"),
    vehicle_id: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> list[OperativoMonthlyPoint]:
    rows = await analytics_service.get_operativos_timeseries(
        db, granularity=granularity, vehicle_id=vehicle_id, motor_type=motor_type,
        fleet_ids=fleet_ids,
        date_from=date_from, date_to=date_to,
    )
    return [OperativoMonthlyPoint(**r) for r in rows]


@router.get(
    "/operativos/pedal",
    response_model=PedalSummary,
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def operativos_pedal(
    fleet_ids: ReportFleetIds,
    vehicle_id: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> PedalSummary:
    data = await analytics_service.get_pedal_summary(
        db, vehicle_id=vehicle_id, motor_type=motor_type,
        fleet_ids=fleet_ids,
        date_from=date_from, date_to=date_to,
    )
    return PedalSummary(**data)


@router.get(
    "/operativos/factor-carga",
    response_model=FactorCargaSummary,
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def operativos_factor_carga(
    fleet_ids: ReportFleetIds,
    granularity: Literal["daily", "monthly"] = Query(default="monthly"),
    vehicle_id: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> FactorCargaSummary:
    data = await analytics_service.get_factor_carga(
        db, granularity=granularity, vehicle_id=vehicle_id, motor_type=motor_type,
        fleet_ids=fleet_ids,
        date_from=date_from, date_to=date_to,
    )
    return FactorCargaSummary(**data)


# ---------------------------------------------------------------------------
# Hábitos seguros de conducción
# ---------------------------------------------------------------------------


@router.get(
    "/habitos/summary",
    response_model=HabitoSummary,
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def habitos_summary(
    fleet_ids: ReportFleetIds,
    vehicle_id: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    event_type: str | None = Query(default=None),
    rpm_min: float | None = Query(default=None, gt=0),
    rpm_threshold: Literal["governed", "overspeed"] | None = Query(
        default=None,
        description="Filtra excesos de RPM contra el límite propio del motor del vehículo: "
        "'governed' = velocidad gobernada, 'overspeed' = sobrevelocidad máxima. "
        "Un motor sin el dato capturado queda excluido.",
    ),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> HabitoSummary:
    data = await analytics_service.get_habito_summary(
        db, vehicle_id=vehicle_id, motor_type=motor_type,
        fleet_ids=fleet_ids,
        date_from=date_from, date_to=date_to, event_type=event_type, rpm_min=rpm_min,
        rpm_threshold=rpm_threshold,
    )
    return HabitoSummary(**data)


@router.get(
    "/habitos/by-type",
    response_model=list[HabitoTypeBucket],
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def habitos_by_type(
    fleet_ids: ReportFleetIds,
    vehicle_id: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    rpm_min: float | None = Query(default=None, gt=0),
    rpm_threshold: Literal["governed", "overspeed"] | None = Query(
        default=None,
        description="Filtra excesos de RPM contra el límite propio del motor del vehículo: "
        "'governed' = velocidad gobernada, 'overspeed' = sobrevelocidad máxima. "
        "Un motor sin el dato capturado queda excluido.",
    ),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> list[HabitoTypeBucket]:
    rows = await analytics_service.get_habito_by_type(
        db, vehicle_id=vehicle_id, motor_type=motor_type,
        fleet_ids=fleet_ids,
        date_from=date_from, date_to=date_to, rpm_min=rpm_min,
        rpm_threshold=rpm_threshold,
    )
    return [HabitoTypeBucket(**r) for r in rows]


@router.get(
    "/habitos/por-grupo",
    response_model=list[GroupHabitosBucket],
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def habitos_por_grupo(
    fleet_ids: ReportFleetIds,
    vehicle_id: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    event_type: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> list[GroupHabitosBucket]:
    """Eventos de hábitos seguros ADITIVOS por grupo hoja del vehículo.

    `kms` viene del hecho de combustible del MISMO rango, alcance y filtro de
    vehículos (todas las unidades), para normalizar ev/1000km tras el rollup.
    """
    rows = await analytics_service.habitos_por_grupo(
        db,
        vehicle_id=vehicle_id,
        motor_type=motor_type,
        fleet_ids=fleet_ids,
        date_from=date_from,
        date_to=date_to,
        event_type=event_type,
    )
    return [GroupHabitosBucket(**r) for r in rows]


@router.get(
    "/habitos/timeseries",
    response_model=list[HabitoTimePoint],
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def habitos_timeseries(
    fleet_ids: ReportFleetIds,
    granularity: Literal["daily", "monthly"] = Query(default="daily"),
    vehicle_id: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    event_type: str | None = Query(default=None),
    rpm_min: float | None = Query(default=None, gt=0),
    rpm_threshold: Literal["governed", "overspeed"] | None = Query(
        default=None,
        description="Filtra excesos de RPM contra el límite propio del motor del vehículo: "
        "'governed' = velocidad gobernada, 'overspeed' = sobrevelocidad máxima. "
        "Un motor sin el dato capturado queda excluido.",
    ),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> list[HabitoTimePoint]:
    fids = fleet_ids
    if granularity == "monthly":
        rows = await analytics_service.get_habito_timeseries_monthly(
            db, vehicle_id=vehicle_id, motor_type=motor_type,
            fleet_ids=fids, date_from=date_from, date_to=date_to,
            event_type=event_type, rpm_min=rpm_min, rpm_threshold=rpm_threshold,
        )
    else:
        rows = await analytics_service.get_habito_timeseries_daily(
            db, vehicle_id=vehicle_id, motor_type=motor_type,
            fleet_ids=fids, date_from=date_from, date_to=date_to,
            event_type=event_type, rpm_min=rpm_min, rpm_threshold=rpm_threshold,
        )
    return [HabitoTimePoint(**r) for r in rows]


@router.get(
    "/habitos/ranking",
    response_model=list[VehicleRankingItem],
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def habitos_ranking(
    fleet_ids: ReportFleetIds,
    vehicle_id: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    event_type: str | None = Query(default=None),
    rpm_min: float | None = Query(default=None, gt=0),
    rpm_threshold: Literal["governed", "overspeed"] | None = Query(
        default=None,
        description="Filtra excesos de RPM contra el límite propio del motor del vehículo: "
        "'governed' = velocidad gobernada, 'overspeed' = sobrevelocidad máxima. "
        "Un motor sin el dato capturado queda excluido.",
    ),
    motor_type: list[str] | None = Query(default=None),
    limit: int = Query(default=10, le=50, ge=1),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> list[VehicleRankingItem]:
    rows = await analytics_service.get_habito_ranking(
        db, vehicle_id=vehicle_id, date_from=date_from, date_to=date_to,
        event_type=event_type, rpm_min=rpm_min, rpm_threshold=rpm_threshold,
        motor_type=motor_type, fleet_ids=fleet_ids, limit=limit,
    )
    return [VehicleRankingItem(**r) for r in rows]


@router.get(
    "/habitos/events",
    response_model=PaginatedHabitos,
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def habitos_events(
    fleet_ids: ReportFleetIds,
    vehicle_id: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    event_type: str | None = Query(default=None),
    rpm_min: float | None = Query(default=None, gt=0),
    rpm_threshold: Literal["governed", "overspeed"] | None = Query(
        default=None,
        description="Filtra excesos de RPM contra el límite propio del motor del vehículo: "
        "'governed' = velocidad gobernada, 'overspeed' = sobrevelocidad máxima. "
        "Un motor sin el dato capturado queda excluido.",
    ),
    sort_by: Literal[
        "fecha",
        "event_value",
        "rpm",
        "velocidad_kmh",
        "g_force",
        "duracion_evento",
        "distancia_evento_mt",
    ] = Query(
        default="fecha",
        description="Columna de ordenamiento. 'fecha' es la vista por defecto "
        "(más reciente primero). Los NULL van al final en ambas direcciones: "
        "una métrica que el ETL aún no publicó para esa fila no es un máximo. "
        "CUIDADO con 'event_value': es la métrica que define cada tipo de "
        "evento, así que sus unidades cambian entre tipos (RPM, km/h, G). "
        "Ordenar por ella es útil con un 'event_type' filtrado y engañoso sin "
        "él, porque compara un 2400 RPM contra un 1.99 G; la UI debería "
        "advertirlo. Cada orden desempata por PK, así que la paginación no "
        "repite ni omite filas.",
    ),
    sort_dir: Literal["asc", "desc"] = Query(default="desc"),
    limit: int = Query(default=50, le=200, ge=1),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> PaginatedHabitos:
    rows, total = await analytics_service.list_habito_events(
        db, vehicle_id=vehicle_id, motor_type=motor_type,
        fleet_ids=fleet_ids,
        date_from=date_from, date_to=date_to, event_type=event_type, rpm_min=rpm_min,
        rpm_threshold=rpm_threshold,
        sort_by=sort_by, sort_dir=sort_dir,
        limit=limit, offset=offset,
    )
    return PaginatedHabitos(
        items=[HabitoEventRead(**r) for r in rows],
        total=total, limit=limit, offset=offset,
    )


@router.get(
    "/habitos/map",
    response_model=list[HabitoMapPoint],
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def habitos_map(
    fleet_ids: ReportFleetIds,
    vehicle_id: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    event_type: str | None = Query(default=None),
    rpm_min: float | None = Query(default=None, gt=0),
    rpm_threshold: Literal["governed", "overspeed"] | None = Query(
        default=None,
        description="Filtra excesos de RPM contra el límite propio del motor del vehículo: "
        "'governed' = velocidad gobernada, 'overspeed' = sobrevelocidad máxima. "
        "Un motor sin el dato capturado queda excluido.",
    ),
    limit: int = Query(default=2000, le=5000, ge=1),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> list[HabitoMapPoint]:
    rows = await analytics_service.list_habito_map_points(
        db, vehicle_id=vehicle_id, motor_type=motor_type,
        fleet_ids=fleet_ids,
        date_from=date_from, date_to=date_to, event_type=event_type, rpm_min=rpm_min,
        rpm_threshold=rpm_threshold,
        limit=limit,
    )
    return [HabitoMapPoint(**r) for r in rows]


# ---------------------------------------------------------------------------
# Fallas / alertas técnicas
# ---------------------------------------------------------------------------


@router.get(
    "/fallas/summary",
    response_model=FaultSummary,
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def fallas_summary(
    fleet_ids: ReportFleetIds,
    vehicle_id: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    severity: list[str] | None = Query(default=None),
    fault_dimension: Literal["diagnostico", "controlador", "modo_falla", "fuente"] | None = Query(
        default=None
    ),
    fault_value: list[str] | None = Query(default=None),
    match_review: list[Literal["direct", "ambiguous", "no_match"]] | None = Query(default=None),
    exclude_telematics: bool = Query(
        default=False,
        description=(
            "Excluye las fallas que reporta el dispositivo telemático sobre sí mismo. "
            "El default NO excluye nada: estos endpoints los consumen el tab de Fallas "
            "de reportes, que sí las quiere fuera, y el módulo Navifault, que trabaja "
            "sobre todo lo que llega."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> FaultSummary:
    data = await analytics_service.get_fault_summary(
        db, vehicle_id=vehicle_id, motor_type=motor_type,
        fleet_ids=fleet_ids,
        date_from=date_from, date_to=date_to, severity=severity,
        fault_dimension=fault_dimension, fault_value=fault_value,
        match_review=match_review,
        exclude_telematics=exclude_telematics,
    )
    return FaultSummary(**data)


@router.get(
    "/fallas/by-severity",
    response_model=list[FaultSeverityBucket],
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def fallas_by_severity(
    fleet_ids: ReportFleetIds,
    vehicle_id: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    fault_dimension: Literal["diagnostico", "controlador", "modo_falla", "fuente"] | None = Query(
        default=None
    ),
    fault_value: list[str] | None = Query(default=None),
    match_review: list[Literal["direct", "ambiguous", "no_match"]] | None = Query(default=None),
    exclude_telematics: bool = Query(
        default=False,
        description=(
            "Excluye las fallas que reporta el dispositivo telemático sobre sí mismo. "
            "El default NO excluye nada: estos endpoints los consumen el tab de Fallas "
            "de reportes, que sí las quiere fuera, y el módulo Navifault, que trabaja "
            "sobre todo lo que llega."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> list[FaultSeverityBucket]:
    rows = await analytics_service.get_fault_by_severity(
        db, vehicle_id=vehicle_id, motor_type=motor_type,
        fleet_ids=fleet_ids,
        date_from=date_from, date_to=date_to,
        fault_dimension=fault_dimension, fault_value=fault_value,
        match_review=match_review,
        exclude_telematics=exclude_telematics,
    )
    return [FaultSeverityBucket(**r) for r in rows]


@router.get(
    "/fallas/por-grupo",
    response_model=list[GroupFallasBucket],
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def fallas_por_grupo(
    fleet_ids: ReportFleetIds,
    vehicle_id: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    exclude_telematics: bool = Query(
        default=False,
        description=(
            "Excluye las fallas que reporta el dispositivo telemático sobre sí mismo. "
            "El default NO excluye nada: estos endpoints los consumen el tab de Fallas "
            "de reportes, que sí las quiere fuera, y el módulo Navifault, que trabaja "
            "sobre todo lo que llega."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> list[GroupFallasBucket]:
    """Fallas ADITIVAS por grupo hoja del vehículo.

    `n_fallas` usa la misma llave de deduplicación del summary y `n_urgentes`
    el mismo criterio (luz de parada roja).
    """
    rows = await analytics_service.fallas_por_grupo(
        db,
        vehicle_id=vehicle_id,
        motor_type=motor_type,
        fleet_ids=fleet_ids,
        date_from=date_from,
        date_to=date_to,
        exclude_telematics=exclude_telematics,
    )
    return [GroupFallasBucket(**r) for r in rows]


@router.get(
    "/fallas/pareto",
    response_model=list[FaultParetoItem],
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def fallas_pareto(
    fleet_ids: ReportFleetIds,
    dimension: Literal["diagnostico", "controlador", "modo_falla", "fuente"] = Query(
        default="diagnostico"
    ),
    vehicle_id: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    severity: list[str] | None = Query(default=None),
    match_review: list[Literal["direct", "ambiguous", "no_match"]] | None = Query(default=None),
    limit: int = Query(default=10, le=50, ge=1),
    exclude_telematics: bool = Query(
        default=False,
        description=(
            "Excluye las fallas que reporta el dispositivo telemático sobre sí mismo. "
            "El default NO excluye nada: estos endpoints los consumen el tab de Fallas "
            "de reportes, que sí las quiere fuera, y el módulo Navifault, que trabaja "
            "sobre todo lo que llega."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> list[FaultParetoItem]:
    rows = await analytics_service.get_fault_pareto(
        db, dimension=dimension, vehicle_id=vehicle_id, motor_type=motor_type,
        fleet_ids=fleet_ids,
        date_from=date_from, date_to=date_to, severity=severity,
        match_review=match_review, limit=limit,
        exclude_telematics=exclude_telematics,
    )
    return [FaultParetoItem(**r) for r in rows]


@router.get(
    "/fallas/timeseries",
    response_model=list[FaultTimePoint],
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def fallas_timeseries(
    fleet_ids: ReportFleetIds,
    granularity: Literal["daily", "monthly"] = Query(default="daily"),
    vehicle_id: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    severity: list[str] | None = Query(default=None),
    fault_dimension: Literal["diagnostico", "controlador", "modo_falla", "fuente"] | None = Query(
        default=None
    ),
    fault_value: list[str] | None = Query(default=None),
    match_review: list[Literal["direct", "ambiguous", "no_match"]] | None = Query(default=None),
    exclude_telematics: bool = Query(
        default=False,
        description=(
            "Excluye las fallas que reporta el dispositivo telemático sobre sí mismo. "
            "El default NO excluye nada: estos endpoints los consumen el tab de Fallas "
            "de reportes, que sí las quiere fuera, y el módulo Navifault, que trabaja "
            "sobre todo lo que llega."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> list[FaultTimePoint]:
    fids = fleet_ids
    if granularity == "monthly":
        rows = await analytics_service.get_fault_timeseries_monthly(
            db, vehicle_id=vehicle_id, motor_type=motor_type,
            fleet_ids=fids, date_from=date_from, date_to=date_to, severity=severity,
            fault_dimension=fault_dimension, fault_value=fault_value,
            match_review=match_review,
            exclude_telematics=exclude_telematics,
        )
    else:
        rows = await analytics_service.get_fault_timeseries_daily(
            db, vehicle_id=vehicle_id, motor_type=motor_type,
            fleet_ids=fids, date_from=date_from, date_to=date_to, severity=severity,
            fault_dimension=fault_dimension, fault_value=fault_value,
            match_review=match_review,
            exclude_telematics=exclude_telematics,
        )
    return [FaultTimePoint(**r) for r in rows]


@router.get(
    "/fallas/ranking",
    response_model=list[VehicleRankingItem],
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def fallas_ranking(
    fleet_ids: ReportFleetIds,
    vehicle_id: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    severity: list[str] | None = Query(default=None),
    fault_dimension: Literal["diagnostico", "controlador", "modo_falla", "fuente"] | None = Query(
        default=None
    ),
    fault_value: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    match_review: list[Literal["direct", "ambiguous", "no_match"]] | None = Query(default=None),
    limit: int = Query(default=10, le=50, ge=1),
    exclude_telematics: bool = Query(
        default=False,
        description=(
            "Excluye las fallas que reporta el dispositivo telemático sobre sí mismo. "
            "El default NO excluye nada: estos endpoints los consumen el tab de Fallas "
            "de reportes, que sí las quiere fuera, y el módulo Navifault, que trabaja "
            "sobre todo lo que llega."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> list[VehicleRankingItem]:
    rows = await analytics_service.get_fault_ranking(
        db, vehicle_id=vehicle_id, date_from=date_from, date_to=date_to,
        severity=severity,
        fault_dimension=fault_dimension, fault_value=fault_value,
        motor_type=motor_type, fleet_ids=fleet_ids,
        match_review=match_review, limit=limit,
        exclude_telematics=exclude_telematics,
    )
    return [VehicleRankingItem(**r) for r in rows]


@router.get(
    "/fallas/events",
    response_model=PaginatedFallas,
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def fallas_events(
    fleet_ids: ReportFleetIds,
    vehicle_id: list[str] | None = Query(default=None),
    motor_type: list[str] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    severity: list[str] | None = Query(default=None),
    fault_dimension: Literal["diagnostico", "controlador", "modo_falla", "fuente"] | None = Query(
        default=None
    ),
    fault_value: list[str] | None = Query(default=None),
    match_review: list[Literal["direct", "ambiguous", "no_match"]] | None = Query(default=None),
    management_state: Literal[
        "all", "pending", "escalada", "pendiente_registro", "repeated", "managed"
    ]
    | None = Query(
        default=None
    ),
    only_urgent: bool = Query(default=False),
    limit: int = Query(default=50, le=200, ge=1),
    offset: int = Query(default=0, ge=0),
    exclude_telematics: bool = Query(
        default=False,
        description=(
            "Excluye las fallas que reporta el dispositivo telemático sobre sí mismo. "
            "El default NO excluye nada: estos endpoints los consumen el tab de Fallas "
            "de reportes, que sí las quiere fuera, y el módulo Navifault, que trabaja "
            "sobre todo lo que llega."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> PaginatedFallas:
    rows, total = await analytics_service.list_fault_events(
        db,
        vehicle_id=vehicle_id,
        motor_type=motor_type,
        fleet_ids=fleet_ids,
        date_from=date_from,
        date_to=date_to,
        severity=severity,
        fault_dimension=fault_dimension,
        fault_value=fault_value,
        match_review=match_review,
        management_state=management_state if management_state != "all" else None,
        only_urgent=only_urgent,
        limit=limit,
        offset=offset,
        exclude_telematics=exclude_telematics,
    )
    return PaginatedFallas(
        items=[FaultEventRead.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/fallas/timeline",
    response_model=PaginatedTimeline,
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def fallas_timeline(
    fleet_ids: ReportFleetIds,
    vehicle_id: str = Query(...),
    codigo_diagnostico: int | None = Query(default=None),
    codigo_modo_de_falla: float | None = Query(default=None),
    diagnostico: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=10, le=100, ge=1),
    offset: int = Query(default=0, ge=0),
    exclude_telematics: bool = Query(
        default=False,
        description=(
            "Excluye las fallas que reporta el dispositivo telemático sobre sí mismo. "
            "El default NO excluye nada: estos endpoints los consumen el tab de Fallas "
            "de reportes, que sí las quiere fuera, y el módulo Navifault, que trabaja "
            "sobre todo lo que llega."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(validate_date_range),
) -> PaginatedTimeline:
    rows, total = await analytics_service.get_fault_timeline(
        db,
        vehicle_id=vehicle_id,
        codigo_diagnostico=codigo_diagnostico,
        codigo_modo_de_falla=codigo_modo_de_falla,
        diagnostico=diagnostico,
        date_from=date_from,
        date_to=date_to,
        fleet_ids=fleet_ids,
        limit=limit,
        offset=offset,
        exclude_telematics=exclude_telematics,
    )
    return PaginatedTimeline(
        items=[FaultTimelineItem(**r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/motor-types",
    response_model=list[MotorTypeBucket],
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def motor_types(
    fleet_ids: ReportFleetIds,
    db: AsyncSession = Depends(get_db),
) -> list[MotorTypeBucket]:
    rows = await analytics_service.list_motor_types(
        db, fleet_ids=fleet_ids
    )
    return [MotorTypeBucket(**r) for r in rows]



# ---------------------------------------------------------------------------
# Ubicaciones (informe personalizado) — consulta viva a MyGeotab
# ---------------------------------------------------------------------------


@router.get(
    "/ubicaciones",
    response_model=UbicacionesPage,
    dependencies=[Depends(require_permission("reportes.view"))],
)
async def ubicaciones(
    fleet_ids: ReportFleetIds,
    vehicle_id: uuid.UUID = Query(),
    date_from: date = Query(),
    date_to: date = Query(),
    sample_minutes: int = Query(default=ubicaciones_service.DEFAULT_SAMPLE_MINUTES),
    cursor: date | None = Query(default=None),
    with_address: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
) -> UbicacionesPage:
    """Rastro de UNA placa pedido a MyGeotab en el momento, un día por petición.

    No se lee del modelo semántico ni se guarda nada: cada llamada consulta al
    proveedor el día indicado por `cursor` (o `date_from`), muestrea un punto
    cada `sample_minutes`, geocodifica esos puntos y los devuelve. El cliente
    encadena los días con `next_cursor`, así ningún rango se pide de golpe.
    """
    if date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="date_from no puede ser mayor que date_to",
        )
    if (date_to - date_from).days + 1 > ubicaciones_service.MAX_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "El rango de ubicaciones no puede superar "
                f"{ubicaciones_service.MAX_DAYS} días"
            ),
        )
    if sample_minutes not in ubicaciones_service.SAMPLE_MINUTES_CHOICES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "sample_minutes debe ser uno de "
                f"{list(ubicaciones_service.SAMPLE_MINUTES_CHOICES)}"
            ),
        )
    day = cursor or date_from
    if day < date_from or day > date_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="cursor fuera del rango solicitado",
        )

    try:
        items = await ubicaciones_service.fetch_day(
            db,
            vehicle_id=vehicle_id,
            fleet_ids=fleet_ids,
            day=day,
            sample_minutes=sample_minutes,
            with_address=with_address,
        )
    except ubicaciones_service.UbicacionesUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except geotab_service.GeotabError as exc:
        # El proveedor es la única fuente del informe: si no responde, decirlo
        # en vez de devolver un archivo vacío que parezca "sin recorrido".
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    if items is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Vehículo no encontrado"
        )

    next_day = day + timedelta(days=1)
    return UbicacionesPage(
        items=[LocationPointRead(**item) for item in items],
        day=day,
        sample_minutes=sample_minutes,
        next_cursor=next_day if next_day <= date_to else None,
    )
