"""Schemas de respuesta para los endpoints de mantenimiento (Cloudfleet KPIs).

Los nombres de campo usan camelCase para coincidir 1:1 con los dicts que
devuelve `app.services.mantenimiento_service` (sin capa de traduccion) y para
que el contrato del frontend sea directo.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ScopePlaca(BaseModel):
    plate: str
    fleet: str
    cd: str
    # Grupo interno del cliente (fleet_vehicle_groups.id); el árbol viaja en
    # GET /vehicles/groups. None = sin grupo asignado.
    vehicle_group_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------
# Disponibilidad
# ---------------------------------------------------------------------------
class DisponibilidadSummary(BaseModel):
    placas: int
    shouldHours: float
    unavailableHoursMec: float
    unavailableHoursProj: float
    availabilityPctMec: float
    availabilityPctProj: float
    affectedVehiclesMec: int
    affectedVehiclesProj: int
    orderCount: int


class IntervaloItem(BaseModel):
    start: str
    end: str


class OtDetalle(BaseModel):
    number: int
    type: str | None = None
    status: str | None = None
    reason: str | None = None
    start: str
    technicalCompletion: str | None = None
    overlapHours: float


class PlacaDowntime(BaseModel):
    plate: str
    hours: float
    availabilityPct: float
    cd: str
    fleet: str
    intervals: list[IntervaloItem]
    orders: list[OtDetalle]


class GrupoDisponibilidad(BaseModel):
    name: str
    availabilityPct: float
    unavailableHours: float
    vehicles: int


class DisponibilidadResponse(BaseModel):
    summary: DisponibilidadSummary
    topPlacas: list[PlacaDowntime]
    porFlota: list[GrupoDisponibilidad]
    porCd: list[GrupoDisponibilidad]


class DisponibilidadTimePoint(BaseModel):
    month: str
    label: str
    mecanica: float
    proyecto: float
    unavailableHours: float


class GroupDisponibilidadBucket(BaseModel):
    """Rollup por grupo interno HOJA (vehicles.vehicle_group_id exacto).

    ``group_id = None`` agrupa las placas sin grupo asignado. El rollup por
    niveles del árbol y los porcentajes los deriva el frontend a partir de
    ``should_hours``/``downtime_hours``.
    """

    group_id: uuid.UUID | None
    placas: int
    should_hours: float
    downtime_hours: float


# ---------------------------------------------------------------------------
# Preventivo
# ---------------------------------------------------------------------------
class PreventivoSummary(BaseModel):
    dueCount: int
    executedCount: int
    onTimeCount: int
    lateExecuted: int
    pending: int
    overduePending: int
    executionPct: float
    onTimePct: float
    byStatus: dict[str, int]


class PreventivoTimePoint(BaseModel):
    month: str
    label: str
    ejecucion: float
    puntualidad: float


class ProgramacionRutina(BaseModel):
    """Ocurrencia de rutina del cronograma: una fila por (placa, rutina, fecha).

    Cloudfleet entrega una fila por tarea; aquí las tareas van en `tasks` y los
    objetivos/diffs (uniformes por ocurrencia) una sola vez. Sirve tanto para
    pendientes (próximas) como para el histórico de ejecutadas.
    """

    plate: str
    fleet: str
    cd: str
    routine: str | None = None
    tasks: list[str]
    tipo: str | None = None
    source: str | None = None
    dateToExecute: str | None = None
    daysToExecute: int | None = None
    status: str | None = None
    odometerToExecute: float | None = None
    currentOdometer: float | None = None
    odometerDiff: float | None = None
    avgOdometerDay: float | None = None
    etaOdometerDays: float | None = None
    hourmeterToExecute: float | None = None
    currentHourmeter: float | None = None
    hourmeterDiff: float | None = None
    avgHourmeterDay: float | None = None
    etaHourmeterDays: float | None = None
    woNumber: int | None = None
    woExecutionDate: str | None = None


# ---------------------------------------------------------------------------
# Confiabilidad
# ---------------------------------------------------------------------------
class ConfiabilidadSummary(BaseModel):
    failureCount: int
    vehiclesWithFailures: int
    mttrHoursAvg: float | None = None
    mttrHoursMedian: float | None = None
    mttrSample: int
    mtbfHoursAvg: float | None = None
    mtbfHoursMedian: float | None = None
    mtbfSample: int


class ConfiabilidadTimePoint(BaseModel):
    month: str
    label: str
    fallas: int
    mttr: float | None = None
    mtbf: float | None = None


# ---------------------------------------------------------------------------
# Ordenes
# ---------------------------------------------------------------------------
class OpenOrderSummary(BaseModel):
    number: int
    plate: str
    fleet: str
    type: str | None = None
    status: str | None = None
    openHours: float
    estimatedFinishDate: str | None = None
    # None = la OT no declara fecha estimada de cierre, que no es lo mismo que
    # estar en plazo: el monitor lo muestra como "Sin estimado".
    overdue: bool | None = None


class OrdenesResponse(BaseModel):
    createdOrStarted: int
    openAtEnd: int
    overdueOpen: int
    currentlyOpen: int
    currentlyOpenOrders: list[OpenOrderSummary]
    avgTechnicalCycleHours: float | None = None
    medianTechnicalCycleHours: float | None = None
    avgFinalClosureLagHours: float | None = None
    byStatus: dict[str, int]
    byType: dict[str, int]
    byFleet: dict[str, int]
    byCd: dict[str, int]


class OrdenesTypeTimePoint(BaseModel):
    month: str
    label: str
    counts: dict[str, int]


class OrdenDetalle(BaseModel):
    number: int
    plate: str
    fleet: str
    cd: str
    type: str | None = None
    status: str | None = None
    reason: str | None = None
    detectedIssue: str | None = None
    startDate: str | None = None
    technicalCompletionDate: str | None = None
    finalCompletionDate: str | None = None
    estimatedFinishDate: str | None = None
    costCenter: str | None = None
    city: str | None = None
    totalCost: float | None = None
    vendor: str | None = None
    affectsAvailability: bool
    warranty: bool


# ---------------------------------------------------------------------------
# Tiempos en taller
# ---------------------------------------------------------------------------
class WorkshopStageDefinition(BaseModel):
    key: str
    label: str
    shortLabel: str


class WorkshopSegment(BaseModel):
    # None en el tramo de entrada inferido: no hubo etiqueta que lo originara.
    labelId: int | None = None
    labelName: str
    stageKey: str | None = None
    stageLabel: str | None = None
    start: str
    end: str | None = None
    hours: float
    open: bool
    timeQuality: str
    precise: bool
    # True cuando el tramo se dedujo (apertura -> primera etiqueta), no se observó.
    inferred: bool = False


class WorkshopOrder(BaseModel):
    number: int
    plate: str
    fleet: str
    cd: str
    site: str
    vehicle: str
    status: str | None = None
    openedAt: str | None = None
    closedAt: str | None = None
    totalHours: float
    classifiedHours: float
    unclassifiedHours: float
    coveragePct: float
    preciseHours: float
    # Horas clasificadas por inferencia, no por observación. Subconjunto de
    # `classifiedHours`: se publica aparte para no confundir dato con deducción.
    inferredHours: float = 0.0
    # Eventos con hora precisa fuera del ciclo de la OT; se descartaron.
    outOfCycleEventCount: int = 0
    stages: dict[str, float]
    trackingCount: int
    labelEventCount: int
    currentLabelId: int | None = None
    currentLabel: str | None = None
    currentSince: str | None = None
    segments: list[WorkshopSegment]


class WorkshopMonthlyPoint(BaseModel):
    month: str
    label: str
    orders: int
    total: float
    recepcion: float
    diagnostico: float
    autorizacion: float
    repuestos: float
    reparacion: float
    calidad: float
    entrega: float


class WorkshopSummary(BaseModel):
    stageTotals: dict[str, float]
    monthly: list[WorkshopMonthlyPoint]
    totalOrders: int
    ordersWithLabels: int
    totalHours: float
    classifiedHours: float
    unclassifiedHours: float
    coveragePct: float
    preciseHours: float
    inferredHours: float = 0.0
    outOfCycleEventCount: int = 0


class TiemposTallerResponse(BaseModel):
    labelCatalogVersion: str
    stageDefinitions: list[WorkshopStageDefinition]
    summary: WorkshopSummary
    items: list[WorkshopOrder]
    total: int
    limit: int
    offset: int
    truncated: bool


# ---------------------------------------------------------------------------
# Rankings
# ---------------------------------------------------------------------------
class RankingItem(BaseModel):
    name: str
    value: float


class RankingGroup(BaseModel):
    peorPuntualidad: list[RankingItem]
    menorCumplimiento: list[RankingItem]
    masFallas: list[RankingItem]
    mttrMasLargo: list[RankingItem]
    mttrMasCorto: list[RankingItem]


class RankingsResponse(BaseModel):
    byPlaca: RankingGroup
    byFleet: RankingGroup
    # Nº de flotas distintas en el scope; el front muestra el bloque de flota
    # solo si es >=2 (cliente de una sola flota se enfoca en placa/CD).
    fleetCount: int


# ---------------------------------------------------------------------------
# Sync de la réplica CloudFleet (botón "forzar traída")
# ---------------------------------------------------------------------------
class CloudfleetSyncResult(BaseModel):
    """Conteos de una pasada de `run_cloudfleet_sync` (espejo del dict summary)."""

    vehiclesFetched: int
    vehiclesUpserted: int
    vehiclesMarkedAbsent: int
    workOrdersFetched: int
    workOrdersUpserted: int
    schedulesFetched: int
    schedulesInserted: int
    metersTargets: int
    metersReadings: int
    metersSent: int
    metersSkipped: int
    metersFailed: int
    metersUncertain: int
    metersReconciled: int


CloudfleetMeterSyncStatus = Literal["pending", "sent", "failed", "uncertain"]
CloudfleetMeterType = Literal["distance", "hours"]


class CloudfleetMeterSyncItem(BaseModel):
    """Estado de publicación de un acumulado por vehículo y tipo."""

    fleetId: str | None
    fleetName: str | None
    vehicleId: str
    plate: str
    meterType: CloudfleetMeterType
    meterValue: float
    meterDate: datetime
    status: CloudfleetMeterSyncStatus
    sentAt: datetime | None
    updatedAt: datetime
    lastError: str | None


class CloudfleetMeterSyncList(BaseModel):
    items: list[CloudfleetMeterSyncItem]
    total: int
    limit: int
    offset: int
