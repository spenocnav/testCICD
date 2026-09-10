from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    CurrentUser,
    require_fleet_access,
    require_permission,
    require_platform_admin,
)
from app.db.advisory_lock import OperationAlreadyRunningError
from app.db.session import get_db
from app.models.master_data import GeotabDatabase, GeotabRule, Vehicle
from app.models.user import User
from app.schemas.fleet import FleetCreate, FleetRead, FleetUpdate
from app.schemas.master_data import (
    BackfillRequest,
    BulkBackfillRequest,
    BulkBackfillResult,
    FleetMotorRead,
    GeotabCredentialRead,
    GeotabDatabaseRead,
    GeotabRuleRead,
    SyncResultRead,
    VehicleExtractionStateRead,
)
from app.schemas.vehicle import VehicleRead, VehicleToggle
from app.services import fleet_service, master_data_service, sync_service, vehicle_service

router = APIRouter(prefix="/fleets", tags=["fleets"])


def _vehicle_read(vehicle: Vehicle) -> VehicleRead:
    db = vehicle.geotab_database
    return VehicleRead.model_validate(vehicle).model_copy(
        update={
            "database_name": db.database_name if db else None,
            "fleet_name": vehicle.fleet.name if vehicle.fleet else None,
        }
    )


def _database_read(
    database: GeotabDatabase,
    vehicle_count: int = 0,
    shared_rules: list[GeotabRule] | None = None,
) -> GeotabDatabaseRead:
    source_rules = database.rules if shared_rules is None else shared_rules
    rule_reads = [
        GeotabRuleRead(
            id=application.id,
            rule_id=rule.rule_id,
            name=rule.name,
            category=application.category,
            event_type=application.event_type,
            motor_type=application.motor_type,
            band=application.band,
            is_descenso=application.is_descenso,
            description=application.description,
            is_active=rule.is_active and application.is_active,
        )
        for rule in source_rules
        for application in rule.applications
    ]
    return GeotabDatabaseRead(
        id=database.id,
        database_name=database.database_name,
        database_key=database.database_key,
        connection_type=database.connection_type,
        plate_prefix=database.plate_prefix,
        is_active=database.is_active,
        synced_at=database.synced_at,
        credentials=[
            GeotabCredentialRead.model_validate(credential) for credential in database.credentials
        ],
        rules=rule_reads,
        vehicle_count=vehicle_count,
    )


@router.get(
    "",
    response_model=list[FleetRead],
    dependencies=[Depends(require_permission("flotas.view"))],
)
async def list_fleets(
    user: CurrentUser,
    only_active: bool | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[FleetRead]:
    fleets = await fleet_service.list_fleets_for_user(db, user, only_active=only_active)
    return [FleetRead.model_validate(f) for f in fleets]


@router.post(
    "",
    response_model=FleetRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_platform_admin)],
)
async def create_fleet(
    payload: FleetCreate,
    db: AsyncSession = Depends(get_db),
) -> FleetRead:
    existing = await fleet_service.get_fleet_by_code(db, payload.code)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Código de flota ya registrado",
        )
    fleet = await fleet_service.create_fleet(
        db, code=payload.code, name=payload.name, is_active=payload.is_active
    )
    await db.commit()
    return FleetRead.model_validate(fleet)


@router.post(
    "/sync",
    response_model=SyncResultRead,
)
async def sync_master_data(
    user: Annotated[User, Depends(require_platform_admin)],
    full: bool = Query(
        default=False,
        description="true = sync completo + detección de borrados; false = incremental.",
    ),
) -> SyncResultRead:
    """Dispara el sync de data maestra desde Navi Vehículos (flotas, bases,
    credenciales, reglas, vehículos). `run_sync` maneja su propia sesión/commit.
    Queda registrado en `sync_run` con trigger=manual."""
    try:
        result = await sync_service.run_sync(
            full=full,
            trigger="manual",
            actor_user_id=user.id,
            actor_email=user.email,
        )
    except OperationAlreadyRunningError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya hay una sincronización de datos maestros en curso.",
        ) from exc
    except Exception as exc:  # red caída, Navi inaccesible, API key faltante, etc.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"No se pudo sincronizar con Navi Vehículos: {exc}",
        ) from exc
    return SyncResultRead(
        full=result.full,
        generated_at=result.generated_at,
        fleets=result.fleets,
        databases=result.databases,
        credentials=result.credentials,
        rules=result.rules,
        vehicles=result.vehicles,
        motor_rpm_bands=result.motor_rpm_bands,
        motor_speeds=result.motor_speeds,
        deactivated=result.deactivated,
    )


@router.patch(
    "/{fleet_id}",
    response_model=FleetRead,
    dependencies=[Depends(require_permission("flotas.edit")), Depends(require_fleet_access)],
)
async def update_fleet(
    fleet_id: uuid.UUID,
    payload: FleetUpdate,
    db: AsyncSession = Depends(get_db),
) -> FleetRead:
    fleet = await fleet_service.get_fleet_by_id(db, fleet_id)
    if not fleet:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flota no encontrada")
    if payload.code is not None and payload.code != fleet.code:
        clash = await fleet_service.get_fleet_by_code(db, payload.code)
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Código de flota ya registrado",
            )
    updated = await fleet_service.update_fleet(
        db,
        fleet,
        code=payload.code,
        name=payload.name,
        is_active=payload.is_active,
        ralenti_analysis_enabled=payload.ralenti_analysis_enabled,
    )
    await db.commit()
    return FleetRead.model_validate(updated)


@router.delete(
    "/{fleet_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("flotas.edit")), Depends(require_fleet_access)],
)
async def deactivate_fleet(
    fleet_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    fleet = await fleet_service.get_fleet_by_id(db, fleet_id)
    if not fleet:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flota no encontrada")
    await fleet_service.deactivate_fleet(db, fleet)
    await db.commit()


@router.get(
    "/{fleet_id}/databases",
    response_model=list[GeotabDatabaseRead],
    dependencies=[Depends(require_permission("flotas.view")), Depends(require_fleet_access)],
)
async def list_fleet_databases(
    fleet_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[GeotabDatabaseRead]:
    fleet = await fleet_service.get_fleet_by_id(db, fleet_id)
    if not fleet:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flota no encontrada")
    databases, vehicle_counts = await master_data_service.list_databases_by_fleet(db, fleet_id)
    rules_by_key = await master_data_service.list_rules_by_database_keys(
        db, [database.database_key for database in databases]
    )
    return [
        _database_read(
            database,
            vehicle_counts.get(database.id, 0),
            rules_by_key.get(database.database_key),
        )
        for database in databases
    ]


@router.get(
    "/{fleet_id}/motors",
    response_model=list[FleetMotorRead],
    dependencies=[Depends(require_permission("flotas.view")), Depends(require_fleet_access)],
)
async def list_fleet_motors(
    fleet_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[FleetMotorRead]:
    fleet = await fleet_service.get_fleet_by_id(db, fleet_id)
    if not fleet:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flota no encontrada")
    rows = await master_data_service.list_motors_by_fleet(db, fleet_id)
    return [
        FleetMotorRead(
            motor_type=row.motor_type,
            description=row.description,
            governed_speed_rpm=row.governed_speed_rpm,
            max_overspeed_rpm=row.max_overspeed_rpm,
            vehicle_count=row.vehicle_count,
            rpm_band_count=row.rpm_band_count,
        )
        for row in rows
    ]


@router.get(
    "/{fleet_id}/vehicles",
    response_model=list[VehicleRead],
    dependencies=[Depends(require_permission("flotas.view")), Depends(require_fleet_access)],
)
async def list_fleet_vehicles(
    fleet_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[VehicleRead]:
    fleet = await fleet_service.get_fleet_by_id(db, fleet_id)
    if not fleet:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flota no encontrada")
    vehicles = await vehicle_service.list_vehicles_by_fleet(db, fleet_id)
    return [_vehicle_read(v) for v in vehicles]


@router.patch(
    "/{fleet_id}/vehicles/{vehicle_id}",
    response_model=VehicleRead,
    dependencies=[Depends(require_permission("flotas.edit")), Depends(require_fleet_access)],
)
async def toggle_fleet_vehicle(
    fleet_id: uuid.UUID,
    vehicle_id: uuid.UUID,
    payload: VehicleToggle,
    db: AsyncSession = Depends(get_db),
) -> VehicleRead:
    vehicle = await vehicle_service.get_vehicle_in_fleet(db, fleet_id, vehicle_id)
    if not vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vehículo no encontrado en la flota",
        )
    updated = await vehicle_service.set_vehicle_active(db, vehicle, payload.is_active)
    await db.commit()
    return _vehicle_read(updated)


@router.get(
    "/{fleet_id}/vehicles/{vehicle_id}/extraction-state",
    response_model=list[VehicleExtractionStateRead],
    dependencies=[Depends(require_permission("flotas.view")), Depends(require_fleet_access)],
)
async def get_vehicle_extraction_state(
    fleet_id: uuid.UUID,
    vehicle_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[VehicleExtractionStateRead]:
    """Estado de extracción del worker por dataset para un vehículo."""
    vehicle = await vehicle_service.get_vehicle_in_fleet(db, fleet_id, vehicle_id)
    if not vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vehículo no encontrado en la flota",
        )
    rows = await master_data_service.list_vehicle_extraction_state(db, vehicle_id)
    await db.commit()  # persiste filas sembradas la primera vez
    return [VehicleExtractionStateRead(**row.__dict__) for row in rows]


@router.post(
    "/{fleet_id}/vehicles/{vehicle_id}/backfill",
    response_model=list[VehicleExtractionStateRead],
    dependencies=[Depends(require_permission("flotas.edit")), Depends(require_fleet_access)],
)
async def request_vehicle_backfill(
    fleet_id: uuid.UUID,
    vehicle_id: uuid.UUID,
    payload: BackfillRequest,
    db: AsyncSession = Depends(get_db),
) -> list[VehicleExtractionStateRead]:
    """Re-extrae el vehículo desde `from_date` (todos los datasets o uno)."""
    vehicle = await vehicle_service.get_vehicle_in_fleet(db, fleet_id, vehicle_id)
    if not vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vehículo no encontrado en la flota",
        )
    try:
        await master_data_service.request_vehicle_backfill(
            db, vehicle_id, payload.from_date, dataset=payload.dataset
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    rows = await master_data_service.list_vehicle_extraction_state(db, vehicle_id)
    await db.commit()
    return [VehicleExtractionStateRead(**row.__dict__) for row in rows]


@router.post(
    "/{fleet_id}/vehicles/backfill",
    response_model=BulkBackfillResult,
    dependencies=[Depends(require_permission("flotas.edit")), Depends(require_fleet_access)],
)
async def reprocess_fleet_vehicles(
    fleet_id: uuid.UUID,
    payload: BulkBackfillRequest,
    db: AsyncSession = Depends(get_db),
) -> BulkBackfillResult:
    """Programa reprocesamiento (backfill) de vehículos de la flota desde
    `from_date`. NO ejecuta: deja el estado listo para que el worker lo tome.
    `vehicle_ids=None` = todos los de la flota; `datasets=None` = todos."""
    fleet = await fleet_service.get_fleet_by_id(db, fleet_id)
    if not fleet:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flota no encontrada")
    try:
        result = await master_data_service.request_bulk_backfill(
            db,
            fleet_ids=[fleet_id],
            vehicle_ids=payload.vehicle_ids,
            from_date=payload.from_date,
            datasets=payload.datasets,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await db.commit()
    return BulkBackfillResult(**result)
