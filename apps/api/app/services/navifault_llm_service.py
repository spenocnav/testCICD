"""Resolución de una falla de Geotab a su página Cummins.

Este módulo **sólo resuelve**: dado un evento de Geotab (fuente, SPN, FMI y el
dateplate del vehículo) encuentra la ``fault_page`` concreta que le corresponde,
o se niega a elegir cuando hay más de una candidata. La identidad nunca es el
número SPN/FMI aislado: conserva manual, motor y variante mediante
``fault_page_id``.

Aquí vivía además la generación de una "descripción técnica" por LLM, retirada
porque no era una función del producto: su endpoint no tenía un solo consumidor
en `apps/web` y su tabla estaba en cero mientras el worker seguía corriendo. La
generación que sí existe —la comunicación redactada para el cliente— vive
completa en ``navifault_client_description_service`` y sólo consume de aquí la
resolución.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import DimVehicle, FactFaultEvent
from app.models.master_data import GeotabDatabase, Vehicle
from app.models.navifault import (
    NavifaultDateplateManualMap,
    NavifaultFaultPage,
    NavifaultFaultProtocolKey,
)

SOURCE_J1939 = "SourceJ1939Id"
SOURCE_J1708 = "SourceJ1708Id"


class NavifaultResolutionError(ValueError):
    """La falla no se puede resolver con certeza a una página Cummins."""


@dataclass(frozen=True)
class FaultInput:
    vehicle_id: str
    source: str
    diagnostic_code: int
    failure_mode: int | None
    lamps: dict[str, bool | None]


@dataclass(frozen=True)
class Resolution:
    page: NavifaultFaultPage
    vehicle: Vehicle
    matched_pub_ids: tuple[str, ...]


@dataclass(frozen=True)
class CandidateResolution:
    """Resultado de resolver una falla sin decidir por el modelo ni por heurística."""

    vehicle: Vehicle
    matched_pub_ids: tuple[str, ...]
    pages: tuple[NavifaultFaultPage, ...]


@dataclass(frozen=True)
class AnalyticsFaultContext:
    """Evento analítico resuelto al vehículo del portal por identidad Geotab."""

    vehicle: Vehicle
    source: str | None
    diagnostic_code: int | None
    failure_mode: float | None
    lamps: dict[str, bool | None]


def normalize_dateplate(value: str | None) -> str:
    """Debe coincidir con el normalizador del mapa dateplate → manual."""
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", without_accents).strip().upper()


async def _matched_publications(session: AsyncSession, vehicle: Vehicle) -> tuple[str, ...]:
    dateplate = normalize_dateplate(vehicle.service_model_name)
    if not dateplate:
        raise NavifaultResolutionError("El vehículo no tiene service_model_name/dateplate")
    result = await session.scalars(
        select(NavifaultDateplateManualMap.pub_id)
        .where(
            NavifaultDateplateManualMap.normalized_service_model_name == dateplate,
            NavifaultDateplateManualMap.mapping_status == "verified",
        )
        .order_by(NavifaultDateplateManualMap.priority, NavifaultDateplateManualMap.pub_id)
    )
    pub_ids = tuple(dict.fromkeys(result.all()))
    if not pub_ids:
        raise NavifaultResolutionError("No hay un manual Cummins verificado para el dateplate")
    return pub_ids


async def find_fault_page_candidates(
    session: AsyncSession, fault: FaultInput
) -> CandidateResolution:
    """Busca todas las FC exactas permitidas por protocolo para una falla."""
    vehicle = await session.get(Vehicle, fault.vehicle_id)
    if vehicle is None or not vehicle.is_active:
        raise NavifaultResolutionError("Vehículo no encontrado o inactivo")
    pub_ids = await _matched_publications(session, vehicle)

    protocol: str
    namespaces: tuple[str, ...]
    if fault.source == SOURCE_J1939:
        protocol, namespaces = "J1939", ("SPN",)
    elif fault.source == SOURCE_J1708:
        protocol, namespaces = "J1708", ("PID", "SID")
    else:
        raise NavifaultResolutionError(
            "La fuente Geotab no usa una numeración Cummins cruzable por protocolo"
        )

    conditions = [
        NavifaultFaultPage.pub_id.in_(pub_ids),
        NavifaultFaultProtocolKey.protocol == protocol,
        NavifaultFaultProtocolKey.namespace.in_(namespaces),
        NavifaultFaultProtocolKey.diagnostic_code == fault.diagnostic_code,
    ]
    if fault.failure_mode is not None:
        conditions.append(NavifaultFaultProtocolKey.fmi == fault.failure_mode)

    result = await session.scalars(
        select(NavifaultFaultPage)
        .join(
            NavifaultFaultProtocolKey,
            NavifaultFaultProtocolKey.fault_page_id == NavifaultFaultPage.fault_page_id,
        )
        .where(*conditions)
        .order_by(NavifaultFaultPage.language, NavifaultFaultPage.fault_page_id)
    )
    pages = list({page.fault_page_id: page for page in result.all()}.values())
    return CandidateResolution(
        vehicle=vehicle,
        matched_pub_ids=pub_ids,
        pages=tuple(pages),
    )


async def resolve_fault_page(session: AsyncSession, fault: FaultInput) -> Resolution:
    """Resuelve sólo cuando la llave protocolaria devuelve una única FC."""
    candidates = await find_fault_page_candidates(session, fault)
    pages = candidates.pages
    if not pages:
        raise NavifaultResolutionError("No hay una FC Cummins exacta para la llave protocolaria")
    if len(pages) != 1:
        raise NavifaultResolutionError(
            f"La llave protocolaria devuelve {len(pages)} FC candidatas; no se generará una descripción"
        )
    return Resolution(
        page=pages[0],
        vehicle=candidates.vehicle,
        matched_pub_ids=candidates.matched_pub_ids,
    )


async def get_analytics_fault_context(
    session: AsyncSession, fault_row_id: str
) -> AnalyticsFaultContext:
    """Lee un evento de analytics y lo asocia al vehículo por identidad Geotab.

    ``analytics.fact_fault_event.vehicle_id`` es un identificador histórico del
    ETL, no el UUID del portal. La unión parte de la dimensión analytics y usa
    estrictamente ``database_key + device_id``; la placa sólo valida que la fila
    analítica y el vehículo sean la misma unidad.
    """
    fact = FactFaultEvent
    statement = (
        select(
            Vehicle,
            fact.nombre_fuente_diagnostico,
            fact.codigo_diagnostico,
            fact.codigo_modo_de_falla,
            fact.luz_de_parada_amber,
            fact.luz_de_parada_roja,
            fact.lampara_de_averia,
            fact.lampara_de_advertencia,
        )
        .select_from(fact)
        .join(DimVehicle, DimVehicle.vehicle_id == fact.vehicle_id)
        .join(
            GeotabDatabase,
            func.lower(GeotabDatabase.database_key) == func.lower(DimVehicle.database_name),
        )
        .join(
            Vehicle,
            and_(
                Vehicle.geotab_database_id == GeotabDatabase.id,
                Vehicle.geotab_device_id == DimVehicle.device_id,
                Vehicle.plate == fact.movil,
            ),
        )
        .where(fact.row_id == fault_row_id)
    )
    rows = (await session.execute(statement)).all()
    contexts = {
        str(row.Vehicle.id): AnalyticsFaultContext(
            vehicle=row.Vehicle,
            source=str(row.nombre_fuente_diagnostico or "").strip() or None,
            diagnostic_code=row.codigo_diagnostico,
            failure_mode=row.codigo_modo_de_falla,
            lamps={
                "stop_amber": row.luz_de_parada_amber,
                "stop_red": row.luz_de_parada_roja,
                "malfunction": row.lampara_de_averia,
                "warning": row.lampara_de_advertencia,
            },
        )
        for row in rows
    }
    if len(contexts) != 1:
        raise NavifaultResolutionError("No fue posible asociar el evento a un vehículo único")
    return next(iter(contexts.values()))


def fault_input_from_analytics(context: AnalyticsFaultContext) -> FaultInput:
    """Convierte valores analytics sin redondear ni reinterpretar un FMI."""
    if context.diagnostic_code is None:
        raise NavifaultResolutionError("El evento no incluye código de diagnóstico")
    if context.failure_mode is None:
        failure_mode = None
    elif float(context.failure_mode).is_integer():
        failure_mode = int(context.failure_mode)
    else:
        raise NavifaultResolutionError("El evento tiene un FMI no entero")
    if not context.source:
        raise NavifaultResolutionError("El evento no incluye fuente/protocolo Geotab")
    return FaultInput(
        vehicle_id=str(context.vehicle.id),
        source=context.source,
        diagnostic_code=int(context.diagnostic_code),
        failure_mode=failure_mode,
        lamps=context.lamps,
    )


