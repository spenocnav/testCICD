"""Servicio de lectura de tablas analytics.* (escritas por InformesRendimiento).

Solo lectura — sin commit ni flush. Filtra por vehicle_id y rango de fechas (date_key).

Aislamiento multitenant (P0):
- La frontera de seguridad es la identidad compuesta
  `(database_key, geotab_device_id)` de cada vehículo analytics.
- `database_name` se conserva como metadato legible (se sigue exponiendo en
  `VehicleRead.database_name`) y se valida contra `GeotabDatabase.database_key`.
- `_apply_vehicle_scope(...)` inserta una subquery set-based con los
  `vehicle_id` que el llamante está autorizado a ver, cruzando
  `(analytics.dim_vehicle.database_name, analytics.dim_vehicle.device_id)` con
  `(geotab_databases.database_key, vehicles.geotab_device_id)` y exigiendo que
  el par base+dispositivo sólo pertenezca a flotas del conjunto solicitado
  (fail closed si está asignado a otra flota).
- El helper materializador `fleet_analytics_vehicle_ids` se conserva para
  diagnósticos/tests; las consultas de negocio no devuelven IDs a Python.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

import sqlalchemy
from sqlalchemy import Float, Integer, String, and_, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from app.models.analytics import (
    DimRule,
    DimVehicle,
    FactCombustibleDaily,
    FactFactorCargaDaily,
    FactFaultEvent,
    FactHabitoEvent,
    FactPedalReading,
)
from app.models.distance_quality import DistanceQualityDecision
from app.models.master_data import GeotabDatabase, MotorCatalog, Vehicle
from app.models.navifault import (
    NavifaultDateplateManualMap,
    NavifaultFaultPage,
    NavifaultFaultProtocolKey,
    NavifaultManagedFaultCase,
)
from app.services import calificacion_config_service
from app.services.calificacion_config import (
    PENALIZACION_SOBREVELOCIDAD_RPM,
    CalificacionConfig,
    config_to_mapping,
)
from app.services.navifault_management_service import (
    _first_reappearance_expression,
    _management_state_expression,
    _signature_join_conditions,
)

# Abreviaturas de mes (es) para etiquetar series mensuales (month_key = yyyymm).
_MES_ABBR = {
    1: "Ene",
    2: "Feb",
    3: "Mar",
    4: "Abr",
    5: "May",
    6: "Jun",
    7: "Jul",
    8: "Ago",
    9: "Sep",
    10: "Oct",
    11: "Nov",
    12: "Dic",
}


def _month_label(month_key: int) -> str:
    anio, mes = divmod(month_key, 100)
    return f"{_MES_ABBR.get(mes, str(mes))} {anio}"


# Categoría de reglas que corresponde a hábitos seguros de conducción.
HABITO_CATEGORIA = "Seguridad"

# Dimensiones válidas para el Pareto de fallas y filtros: código público -> columna.
FAULT_DIMENSIONS = {
    "diagnostico": FactFaultEvent.diagnostico,
    "controlador": FactFaultEvent.nombre_de_controlador,
    "modo_falla": FactFaultEvent.modo_de_falla,
    "fuente": FactFaultEvent.nombre_fuente_diagnostico,
}

#: Fuente de diagnóstico del propio equipo telemático Geotab.
#:
#: Las demás fuentes del hecho —`SourceJ1939Id`, `SourceObdSaId`,
#: `SourceJ1708Id`— son buses del vehículo. Esta es el equipo hablando de sí
#: mismo, y por eso queda fuera del reporte de fallas (ver
#: `_apply_fault_filters`).
TELEMATICS_DEVICE_SOURCE = "SourceGeotabGoId"

# Estados de revisión del cruce Geotab → Cummins. No son una clasificación
# clínica de la falla: describen exclusivamente el resultado de la llave
# exacta por manual/dateplate + protocolo + código + FMI.
MATCH_REVIEW_DIRECT = "direct"
MATCH_REVIEW_AMBIGUOUS = "ambiguous"
MATCH_REVIEW_NO_MATCH = "no_match"
MATCH_REVIEW_VALUES = frozenset(
    {MATCH_REVIEW_DIRECT, MATCH_REVIEW_AMBIGUOUS, MATCH_REVIEW_NO_MATCH}
)

# Métricas válidas para el ranking por vehículo. Mapea código público -> columna.
RANKING_METRICS = {
    "comb",
    "kms_ecm",
    "km_gal",
    "km_m3",
    # Consumo por hora: el rendimiento de una flota vocacional, que trabaja por
    # horas ECM y no por kilómetros (ver `all_vocacional` en el resumen).
    "gal_hr",
    "m3_hr",
    "pct_exceso_rpm",
    "pct_ralenti",
}

FUEL_KIND_LIQUID = "liquid"
FUEL_KIND_GAS = "gas"


def _fuel_kind_predicate(column: Any, fuel_kind: str) -> Any:
    """Filtro de unidad obligatorio para no sumar galones y m³.

    Los hechos sin clasificación se excluyen: asumir que son líquidos podría
    presentar como galones un registro histórico de gas. El despliegue debe
    ejecutar el backfill ETL antes de habilitar esta versión del API.
    """

    return column == fuel_kind


def _date_to_key(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


def _latest_manual_distance_action(f: Any = FactCombustibleDaily) -> Any:
    """Última decisión humana que aún corresponde a la observación actual."""
    return (
        select(DistanceQualityDecision.action)
        .where(
            DistanceQualityDecision.fact_row_id == f.fact_row_id,
            DistanceQualityDecision.origin == "manual",
            DistanceQualityDecision.observation_fingerprint == f.distance_quality_fingerprint,
        )
        .order_by(
            DistanceQualityDecision.created_at.desc(),
            DistanceQualityDecision.id.desc(),
        )
        .limit(1)
        .correlate(f)
        .scalar_subquery()
    )


def _effective_distance_expressions(f: Any = FactCombustibleDaily) -> dict[str, Any]:
    """Distancia efectiva del día, con la última decisión humana aplicada.

    `use_ecm` estaba declarado en el contrato del endpoint y en el
    `CheckConstraint` de la tabla, pero no aquí: caía al `else_` y devolvía
    `kms_effective`, que en una fila marcada es NULL. Es decir, un
    administrador que resolvía una anomalía con "usar ECM" no cambiaba nada y
    la fila seguía contándose como pendiente. Es la acción que hace falta
    cuando el ECM es coherente y el GPS perdió viajes, que es el caso más
    frecuente: en la flota GNL el rendimiento km/m3 respalda al ECM en 101 de
    135 días marcados, contra 16 del GPS.
    """
    action = _latest_manual_distance_action(f)
    kms = case(
        (action == "use_ecm", f.kms_ecm),
        (action == "use_gps", f.kms_gps),
        (action == "exclude", None),
        else_=f.kms_effective,
    )
    source = case(
        (action == "use_ecm", "ecm_manual"),
        (action == "use_gps", "gps_manual"),
        (action == "exclude", "none"),
        else_=f.distance_source,
    )
    hours = case(
        (action == "use_ecm", f.hrs_ecm),
        (action == "use_gps", f.hrs_gps),
        (action == "exclude", None),
        (f.distance_source == "ecm", f.hrs_ecm),
        (f.distance_source == "gps_auto", f.hrs_gps),
        else_=None,
    )
    status = case(
        (action == "use_ecm", "resolved"),
        (action == "use_gps", "resolved"),
        (action == "exclude", "excluded"),
        else_=f.distance_quality_status,
    )
    return {
        "action": action,
        "kms": kms,
        "source": source,
        "hours": hours,
        "status": status,
    }


# ---------------------------------------------------------------------------
# Aislamiento por vehicle_id (P0)
# ---------------------------------------------------------------------------


async def fleet_analytics_vehicle_ids(
    db: AsyncSession, fleet_ids: Sequence[uuid.UUID] | None
) -> set[str] | None:
    """Devuelve el conjunto de `analytics.dim_vehicle.vehicle_id` que la(s)
    flota(s) solicitada(s) está(n) autorizada(s) a ver.

    - `fleet_ids is None` → `None` (scope global administrativo: no scoping).
    - `fleet_ids == []` → `set()` (sin flotas seleccionadas: nada visible).
    - Lista concreta de UUIDs → resuelve cruzando
      `(DimVehicle.database_name, DimVehicle.device_id)` con
      `(GeotabDatabase.database_key, Vehicle.geotab_device_id)`, además de
      `Vehicle.is_active` y `Vehicle.fleet_id` solicitado.

    Fail closed: si un par `database_key + geotab_device_id` activo aparece
    también en un `Vehicle` activo de una flota fuera del conjunto solicitado,
    ese par (y por extensión sus `dim_vehicle.vehicle_id`) queda excluido
    aunque la flota solicitada también lo tenga.

    Notas:
    - `database_name` de analytics se compara con `database_key`, la identidad
      física de la base; no se usa el nombre de la flota como sustituto.
    - Filas de `dim_vehicle` cuyo `device_id` no mapea a un `Vehicle` activo
      en la misma base y en las flotas solicitadas se omiten (no se autorizan
      por defecto).
    """
    if fleet_ids is None:
        return None
    if not fleet_ids:
        return set()

    rows = (await db.execute(_fleet_analytics_vehicle_id_query(fleet_ids))).all()
    return {row[0] for row in rows if row[0]}


#: Etiqueta de la columna-ventana que transporta el total de una página.
_WINDOW_TOTAL_LABEL = "window_total"


def _add_window_total(stmt: Select[Any]) -> Select[Any]:
    """Adjunta el total del conjunto filtrado a la MISMA sentencia de la página.

    Un endpoint paginado necesita las filas de la página y cuántas hay en total.
    Resolverlo con dos sentencias hace que la petición pague dos veces el mismo
    recorrido: sobre `fact_fault_event` (1,0 M de filas) la agregación de nueve
    columnas se ejecutaba entera para la página y otra vez envuelta en
    `count(*)`, y las dos son secuenciales porque comparten la sesión.

    `count(*) OVER ()` se evalúa DESPUÉS de `GROUP BY` y ANTES de `LIMIT`, así
    que en una consulta agregada cuenta grupos y en una plana cuenta filas: en
    ambos casos es el mismo número que devolvía la subconsulta envuelta.

    El total viaja repetido en cada fila de la página, que es el precio de una
    pasada: son ~50 enteros por respuesta.

    NO es una mejora universal, y por eso no se aplicó a todos los endpoints
    paginados. Sirve cuando la consulta ya tenía que recorrer el conjunto
    entero (una agregación, un `GROUP BY`): ahí la segunda pasada era puro
    desperdicio. Perjudica cuando el planificador podía resolver la página por
    top-N y frenar en el `LIMIT`, porque la ventana lo obliga a materializar
    todo para poder contarlo. Medido contra la base real: `list_fault_events`
    (agregada) baja a 0,52-0,63x, mientras que `list_habito_events` (plana,
    con `ORDER BY` respaldado por índice) subía a 1,03-1,54x y por eso quedó
    con sus dos sentencias. Antes de aplicarlo a otro endpoint, medirlo.

    Cuidado: si la página sale vacía la ventana no devuelve ninguna fila y el
    total no se puede leer. Con `offset == 0` eso significa que el filtro no
    encontró nada y el total es 0; con `offset > 0` puede ser un offset que
    desbordó un conjunto no vacío, y ahí hay que preguntar el total aparte.
    Cada llamador conserva su sentencia de conteo para ese caso.
    """
    return stmt.add_columns(func.count().over().label(_WINDOW_TOTAL_LABEL))


def _fleet_analytics_vehicle_id_query(
    fleet_ids: Sequence[uuid.UUID],
) -> Select[tuple[str]]:
    """Subquery reusable de autorización, ejecutada dentro de la query final."""
    candidate = aliased(Vehicle, name="candidate")
    outsider = aliased(Vehicle, name="outsider")
    candidate_db = aliased(GeotabDatabase, name="candidate_db")
    outsider_db = aliased(GeotabDatabase, name="outsider_db")
    return (
        select(func.distinct(DimVehicle.vehicle_id))
        .join(
            candidate,
            and_(
                candidate.geotab_device_id == DimVehicle.device_id,
                candidate.is_active.is_(True),
                candidate.fleet_id.in_(fleet_ids),
                candidate.geotab_device_id.isnot(None),
            ),
        )
        .join(candidate_db, candidate.geotab_database_id == candidate_db.id)
        .where(
            candidate_db.is_active.is_(True),
            func.lower(candidate_db.database_key)
            == func.lower(DimVehicle.database_name),
            ~sqlalchemy.exists().where(
                sqlalchemy.and_(
                    outsider.geotab_device_id == candidate.geotab_device_id,
                    outsider.is_active.is_(True),
                    outsider.fleet_id.notin_(fleet_ids),
                    outsider.geotab_database_id == outsider_db.id,
                    func.lower(outsider_db.database_key)
                    == func.lower(candidate_db.database_key),
                ),
            )
        )
        .correlate(DimVehicle, candidate, candidate_db)
    )


def _apply_vehicle_scope(
    stmt: Any,
    vehicle_col: Any,
    allowed_fleet_ids: Sequence[uuid.UUID] | None,
) -> tuple[Any, bool]:
    """Restringe `vehicle_col` mediante una subquery de autorización.

    Mantener el scope dentro de PostgreSQL evita materializar miles de IDs en
    Python, un segundo round-trip y un ``IN (...)`` cuyo SQL crece con la flota.
    """
    if allowed_fleet_ids is None:
        return stmt, False
    if not allowed_fleet_ids:
        return stmt, True
    return (
        stmt.where(vehicle_col.in_(_fleet_analytics_vehicle_id_query(allowed_fleet_ids))),
        False,
    )


async def list_vehicles(
    db: AsyncSession, fleet_ids: list[uuid.UUID] | None = None
) -> list[dict[str, Any]]:
    """Devuelve vehículos (desde dim_vehicle) con vocacional (desde vehicles).
    Retorna dicts porque VehicleRead necesita vocacional que no está en DimVehicle."""
    allowed = fleet_ids
    if allowed == []:
        return []
    vehicle_db = aliased(GeotabDatabase, name="vehicle_db")
    # Grupo interno del cliente: escalar correlacionada con la MISMA identidad
    # compuesta (device + database) que el resto del módulo; no un join, para
    # no alterar la cardinalidad del listado.
    group_db = aliased(GeotabDatabase, name="group_db")
    group_vehicle = aliased(Vehicle, name="group_vehicle")
    group_subq = (
        select(group_vehicle.vehicle_group_id)
        .join(group_db, group_vehicle.geotab_database_id == group_db.id)
        .where(
            group_vehicle.geotab_device_id == DimVehicle.device_id,
            func.lower(group_db.database_key) == func.lower(DimVehicle.database_name),
            group_vehicle.is_active.is_(True),
        )
        .correlate(DimVehicle)
        .limit(1)
        .scalar_subquery()
    )
    stmt = select(
        DimVehicle,
        sqlalchemy.exists()
        .where(
            Vehicle.geotab_database_id == vehicle_db.id,
            Vehicle.geotab_device_id == DimVehicle.device_id,
            func.lower(vehicle_db.database_key)
            == func.lower(DimVehicle.database_name),
            Vehicle.is_active.is_(True),
            Vehicle.vocacional.is_(True),
        )
        .label("vocacional"),
        group_subq.label("vehicle_group_id"),
    ).order_by(DimVehicle.vehicle_label)
    stmt, _empty = _apply_vehicle_scope(stmt, DimVehicle.vehicle_id, allowed)
    if _empty:
        return []
    rows = (await db.execute(stmt)).all()
    result: list[dict[str, Any]] = []
    for v, vocacional, vehicle_group_id in rows:
        is_n15_fallback = v.fuel_kind is None and (v.motor_type or "").strip().upper() == "N15"
        fuel_kind = v.fuel_kind or (FUEL_KIND_GAS if is_n15_fallback else FUEL_KIND_LIQUID)
        result.append(
            {
                "vehicle_id": v.vehicle_id,
                "vehicle_label": v.vehicle_label,
                "database_name": v.database_name,
                "motor_type": v.motor_type,
                "group_key": v.group_key,
                "rpm_class": v.rpm_class,
                "fuel_type_raw": v.fuel_type_raw,
                "fuel_kind": fuel_kind,
                "fuel_unit": v.fuel_unit or ("m3" if fuel_kind == FUEL_KIND_GAS else "gal"),
                "fuel_classification_source": (
                    v.fuel_classification_source
                    or ("motor_n15_fallback" if is_n15_fallback else "legacy_default")
                ),
                "fuel_classification_conflict": bool(v.fuel_classification_conflict),
                "is_active": v.is_active,
                "vocacional": vocacional,
                "vehicle_group_id": vehicle_group_id,
            }
        )
    return result


async def _apply_motor_type_filter(
    db: AsyncSession,
    vehicle_id: list[str] | None,
    motor_type: list[str] | None,
    fleet_ids: list[uuid.UUID] | None = None,
) -> tuple[list[str] | None, bool]:
    """Resuelve el filtro de `motor_type` intersectándolo con `vehicle_id`.

    Devuelve `(vehicle_id_ajustado, empty)`:
    - `empty=True` indica que el filtro no puede devolver ninguna fila.
    - Si `motor_type` es None, devuelve `vehicle_id` intacto.
    - Si `fleet_ids` se pasa, restringe `motor_vehicle_ids` a las flotas.
    """
    if motor_type is None:
        return vehicle_id, False
    motor_vehicle_ids = await resolve_motor_type_vehicle_ids(db, motor_type, fleet_ids)
    if motor_vehicle_ids == []:
        return None, True
    resolved = _resolve_vehicle_filter(vehicle_id, motor_vehicle_ids)
    if resolved == []:
        return None, True
    return resolved, False


def _empty_summary_combustible(
    fuel_kind: str = FUEL_KIND_LIQUID,
) -> dict[str, Any]:
    is_gas = fuel_kind == FUEL_KIND_GAS
    return {
        "fuel_kind": fuel_kind,
        "fuel_unit": "m3" if is_gas else "gal",
        "kms_ecm": 0.0,
        "hrs_ecm": 0.0,
        "hrs_gps": 0.0,
        "comb": 0.0,
        "comb_ralenti": None,
        "km_gal": None,
        "gal_hr": None,
        "gal_hr_ralenti": None,
        "km_m3": None,
        "m3_hr": None,
        "m3_hr_ralenti": None,
        "velocidad_promedio": None,
        "pct_ralenti": None,
        "pct_exceso_rpm": None,
        "n_vehiculos": 0,
        "n_registros": 0,
        "all_vocacional": False,
    }


async def list_motor_types(
    db: AsyncSession, fleet_ids: list[uuid.UUID] | None = None
) -> list[dict[str, Any]]:
    """Tipos de motor (catalog) que tienen al menos un vehículo en dim_vehicle."""
    allowed = fleet_ids
    if allowed == []:
        return []
    # LEFT OUTER a public.motor_catalog (misma base física) para exponer los
    # límites de placa. `motor_catalog.motor_type` es PK, así que el outer join
    # no altera el conteo de vehículos.
    stmt = (
        select(
            DimVehicle.motor_type,
            func.count(DimVehicle.vehicle_id),
            MotorCatalog.governed_speed_rpm,
            MotorCatalog.max_overspeed_rpm,
        )
        .select_from(DimVehicle)
        .join(
            MotorCatalog,
            MotorCatalog.motor_type == DimVehicle.motor_type,
            isouter=True,
        )
        .where(DimVehicle.motor_type.isnot(None))
    )
    stmt, _empty = _apply_vehicle_scope(stmt, DimVehicle.vehicle_id, allowed)
    if _empty:
        return []
    stmt = stmt.group_by(
        DimVehicle.motor_type,
        MotorCatalog.governed_speed_rpm,
        MotorCatalog.max_overspeed_rpm,
    ).order_by(DimVehicle.motor_type)
    return [
        {
            "motor_type": mt,
            "n_vehiculos": n,
            "governed_speed_rpm": governed,
            "max_overspeed_rpm": overspeed,
        }
        for mt, n, governed, overspeed in (await db.execute(stmt)).all()
        if mt
    ]


async def resolve_motor_type_vehicle_ids(
    db: AsyncSession,
    motor_type: list[str] | None,
    fleet_ids: list[uuid.UUID] | None = None,
) -> list[str] | None:
    """Resuelve la lista de `vehicle_id` cuyos `motor_type` están en `motor_type`.

    Devuelve `None` si no se proporcionaron filtros (caller debe tratarlo como
    "sin restricción"). Si la lista está vacía, devuelve `[]` (sin vehículos).
    Si `fleet_ids` se pasa, restringe a vehículos de las flotas permitidas
    vía el join `(dim_vehicle.database_name, dim_vehicle.device_id) ==
    (geotab_databases.database_key, vehicles.geotab_device_id)`.
    """
    if motor_type is None:
        return None
    if not motor_type:
        return []
    stmt = select(DimVehicle.vehicle_id).where(DimVehicle.motor_type.in_(motor_type))
    allowed = fleet_ids
    if allowed == []:
        return []
    stmt, _empty = _apply_vehicle_scope(stmt, DimVehicle.vehicle_id, allowed)
    if _empty:
        return []
    return [row[0] for row in (await db.execute(stmt)).all()]


async def list_combustible_daily(
    db: AsyncSession,
    *,
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fuel_kind: str = FUEL_KIND_LIQUID,
    fleet_ids: list[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[Sequence[dict[str, Any]], int]:
    allowed = fleet_ids
    if allowed == []:
        return [], 0

    f = FactCombustibleDaily
    effective = _effective_distance_expressions(f)
    stmt = select(
        f.fact_row_id,
        f.vehicle_id,
        f.database_name,
        f.motor_type,
        f.date_key,
        f.fecha,
        f.placa,
        f.hrs_ecm,
        f.hrs_gps,
        f.comb,
        f.comb_ralenti,
        f.fuel_kind,
        f.fuel_unit,
        f.gal_hr,
        f.gal_hr_ralenti,
        f.m3_hr,
        f.m3_hr_ralenti,
        f.pct_rango_bajo,
        f.pct_rango_economico,
        f.pct_rango_balanceado,
        f.pct_rango_potencia,
        f.pct_exceso_rpm,
        f.pct_rango_potencia_ineficiente,
        f.pct_ralenti,
        f.ralenti,
        effective["kms"].label("resolved_kms_effective"),
        effective["hours"].label("resolved_distance_hours"),
    )
    count_stmt = select(func.count()).select_from(FactCombustibleDaily)
    fuel_predicate = _fuel_kind_predicate(FactCombustibleDaily.fuel_kind, fuel_kind)
    stmt = stmt.where(fuel_predicate)
    count_stmt = count_stmt.where(fuel_predicate)
    stmt, _empty = _apply_vehicle_scope(stmt, FactCombustibleDaily.vehicle_id, allowed)
    count_stmt, _empty_c = _apply_vehicle_scope(
        count_stmt, FactCombustibleDaily.vehicle_id, allowed
    )
    if _empty or _empty_c:
        return [], 0

    if motor_type is not None:
        motor_vehicle_ids = await resolve_motor_type_vehicle_ids(db, motor_type, allowed)
        if not motor_vehicle_ids:
            return [], 0
        if vehicle_id is not None:
            vehicle_id = [v for v in vehicle_id if v in set(motor_vehicle_ids)]
            if not vehicle_id:
                return [], 0
        else:
            vehicle_id = motor_vehicle_ids

    if vehicle_id is not None:
        stmt = stmt.where(FactCombustibleDaily.vehicle_id.in_(vehicle_id))
        count_stmt = count_stmt.where(FactCombustibleDaily.vehicle_id.in_(vehicle_id))
    if date_from is not None:
        key = _date_to_key(date_from)
        stmt = stmt.where(FactCombustibleDaily.date_key >= key)
        count_stmt = count_stmt.where(FactCombustibleDaily.date_key >= key)
    if date_to is not None:
        key = _date_to_key(date_to)
        stmt = stmt.where(FactCombustibleDaily.date_key <= key)
        count_stmt = count_stmt.where(FactCombustibleDaily.date_key <= key)

    stmt = (
        stmt.order_by(
            FactCombustibleDaily.date_key.desc(),
            FactCombustibleDaily.fact_row_id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )

    raw_rows = (await db.execute(stmt)).mappings().all()
    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        item = dict(row)
        kms_effective = item.pop("resolved_kms_effective")
        effective_hours = item.pop("resolved_distance_hours")
        item.update(
            {
                # Contrato público: `kms_ecm` representa la distancia operativa
                # ya resuelta. La fuente y la decisión solo salen por el API
                # administrativo de calidad.
                "kms_ecm": kms_effective,
                "km_gal": (
                    kms_effective / item["comb"]
                    if kms_effective is not None and item["comb"] and item["comb"] > 0
                    else None
                )
                if item["fuel_kind"] != FUEL_KIND_GAS
                else None,
                "km_m3": (
                    kms_effective / item["comb"]
                    if kms_effective is not None and item["comb"] and item["comb"] > 0
                    else None
                )
                if item["fuel_kind"] == FUEL_KIND_GAS
                else None,
                "velocidad_promedio": (
                    kms_effective / effective_hours
                    if kms_effective is not None and effective_hours and effective_hours > 0
                    else None
                ),
            }
        )
        rows.append(item)
    total = (await db.execute(count_stmt)).scalar_one()
    return rows, total


def _resolve_vehicle_filter(
    vehicle_id: list[str] | None,
    motor_vehicle_ids: list[str] | None,
) -> list[str] | None:
    """Interseca vehicle_id con motor_vehicle_ids cuando ambos están presentes.

    - motor_vehicle_ids is None: sin restricción por motor.
    - motor_vehicle_ids == []: ningún vehículo match (resultado vacío aguas abajo).
    """
    if motor_vehicle_ids is None:
        return vehicle_id
    if not motor_vehicle_ids:
        return []
    if vehicle_id is None:
        return motor_vehicle_ids
    motor_set = set(motor_vehicle_ids)
    return [v for v in vehicle_id if v in motor_set]


def _apply_daily_filters(stmt, *, vehicle_id, date_from, date_to, motor_vehicle_ids=None):
    """Aplica los filtros estándar (vehicle_id + rango de fechas) sobre date_key."""
    resolved = _resolve_vehicle_filter(vehicle_id, motor_vehicle_ids)
    if resolved is not None:
        if not resolved:
            # Empty IN () is not valid SQL — caller must short-circuit before.
            raise ValueError("empty vehicle filter")
        stmt = stmt.where(FactCombustibleDaily.vehicle_id.in_(resolved))
    if date_from is not None:
        stmt = stmt.where(FactCombustibleDaily.date_key >= _date_to_key(date_from))
    if date_to is not None:
        stmt = stmt.where(FactCombustibleDaily.date_key <= _date_to_key(date_to))
    return stmt


# Promedio que ignora nulos; positivos solo donde tiene sentido (km/gal, gal/hr).
# Válido cuando cada fila es una observación individual (una lectura de pedal,
# la duración de un evento). Para porcentajes ya agregados por día usar
# `_weighted`: promediarlos da el mismo peso a un día de dos horas que a una
# jornada completa, y a los días sin operación, que entran como cero.
def _avg(col, positive: bool = False):
    if positive:
        return func.avg(func.nullif(col, 0))
    return func.avg(col)


def _weighted(col, weight):
    """Promedio de `col` ponderado por `weight`: Σ(col x peso)/Σ(peso).

    Las bandas de RPM promediadas sin ponderar ni siquiera sumaban 100 % en el
    gráfico apilado (76 % en 2026-07), porque cada día pesaba igual sin importar
    cuántas horas tuviera detrás.
    """
    return func.sum(col * weight) / func.nullif(func.sum(weight), 0)


# Peso para reagregar `pct_ralenti`: tiene que ser EXACTAMENTE el denominador
# que usó el ETL, o la media ponderada deja de ser horas de ralentí sobre horas
# de operación. Desde la cascada de fuentes ese divisor viaja en
# `horas_ralenti_base` (ECM, banda o GPS según lo disponible). El fallback a
# max(GPS, ECM) cubre las filas cargadas antes de que existiera la columna.
def _ralenti_weight(f=None):
    fact = f if f is not None else FactCombustibleDaily
    return func.coalesce(
        func.nullif(fact.horas_ralenti_base, 0.0),
        func.greatest(func.coalesce(fact.hrs_gps, 0.0), func.coalesce(fact.hrs_ecm, 0.0)),
    )


def _idle_hours(fact, is_gas: bool):
    """Horas de ralentí disponibles para calcular la tasa de combustible.

    Algunos lotes históricos de gas no tienen ``ralenti_ecm`` en la tabla
    agregada, aunque sí conservan el tiempo general de ralentí, la tasa
    persistida o ambos. Se prueban esas fuentes en orden para que el KPI y las
    series no queden vacíos.
    """
    idle_hours = fact.ralenti_ecm
    general_idle_hours = getattr(fact, "ralenti", None)
    if general_idle_hours is not None:
        idle_hours = sqlalchemy.case(
            (idle_hours > 0, idle_hours),
            (general_idle_hours > 0, general_idle_hours),
            else_=None,
        )
    if not is_gas:
        return idle_hours
    derived_hours = sqlalchemy.case(
        (
            and_(fact.comb_ralenti > 0, fact.m3_hr_ralenti > 0),
            fact.comb_ralenti / fact.m3_hr_ralenti,
        ),
        else_=None,
    )
    return sqlalchemy.case((idle_hours > 0, idle_hours), else_=derived_hours)


async def get_combustible_summary(
    db: AsyncSession,
    *,
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fuel_kind: str = FUEL_KIND_LIQUID,
    fleet_ids: list[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    """Totales y promedios sobre todo el rango filtrado (no solo una página)."""
    allowed = fleet_ids
    if allowed == []:
        return _empty_summary_combustible(fuel_kind)
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return _empty_summary_combustible(fuel_kind)
    f = FactCombustibleDaily
    is_gas = fuel_kind == FUEL_KIND_GAS
    effective = _effective_distance_expressions(f)
    idle_hours = _idle_hours(f, is_gas)
    ratio_hours = (
        func.sum(sqlalchemy.case((f.comb > 0, f.hrs_ecm), else_=None))
        if is_gas
        else func.sum(f.hrs_ecm)
    )
    stmt = select(
        func.coalesce(func.sum(f.hrs_ecm), 0.0),
        func.coalesce(func.sum(f.hrs_gps), 0.0),
        func.coalesce(ratio_hours, 0.0),
        func.coalesce(func.sum(f.comb), 0.0),
        func.sum(f.comb_ralenti),
        func.coalesce(
            func.sum(
                sqlalchemy.case(
                    (f.comb_ralenti.isnot(None), idle_hours),
                    else_=0.0,
                )
            ),
            0.0,
        ),
        _weighted(f.pct_ralenti, _ralenti_weight(f)),
        _weighted(f.pct_exceso_rpm, f.tiempo_total_en_rango),
        func.count(func.distinct(f.vehicle_id)),
        func.count(),
        func.coalesce(func.sum(effective["kms"]), 0.0),
        func.coalesce(
            func.sum(
                case(
                    (and_(f.comb > 0, effective["kms"].isnot(None)), effective["kms"]),
                    else_=None,
                )
            ),
            0.0,
        ),
        func.coalesce(
            func.sum(
                case(
                    (and_(f.comb > 0, effective["kms"].isnot(None)), f.comb),
                    else_=None,
                )
            ),
            0.0,
        ),
        func.coalesce(func.sum(effective["hours"]), 0.0),
    )
    stmt, _empty_v = _apply_vehicle_scope(stmt, f.vehicle_id, allowed)
    if _empty_v:
        return _empty_summary_combustible(fuel_kind)
    stmt = stmt.where(_fuel_kind_predicate(f.fuel_kind, fuel_kind))
    stmt = _apply_daily_filters(stmt, vehicle_id=vehicle_id, date_from=date_from, date_to=date_to)
    row = (await db.execute(stmt)).one()
    (
        hrs_ecm,
        hrs_gps,
        hrs_ecm_with_fuel,
        comb,
        comb_ralenti,
        hrs_ralenti_ecm,
        ralenti_avg,
        exceso_rpm_avg,
        n_vehiculos,
        n_registros,
        kms_effective,
        kms_effective_with_fuel,
        effective_comb,
        effective_hours,
    ) = row
    # Razón de sumas, no promedio de promedios: promediar los ratios diarios da
    # el mismo peso a un día de dos horas que a una jornada completa, y sesga
    # el resultado (gal/hr 3.02 vs 2.30 real; velocidad 22.7 vs 9.6 en 2026-07).
    fuel_per_hour = (
        (comb / hrs_ecm_with_fuel) if hrs_ecm_with_fuel and hrs_ecm_with_fuel > 0 else None
    )
    idle_fuel_per_hour = (
        comb_ralenti / hrs_ralenti_ecm if hrs_ralenti_ecm and hrs_ralenti_ecm > 0 else None
    )
    effective_efficiency = (
        kms_effective_with_fuel / effective_comb if effective_comb and effective_comb > 0 else None
    )
    effective_speed = (
        kms_effective / effective_hours if effective_hours and effective_hours > 0 else None
    )

    # Determinar si todos los vehículos con datos son vocacionales.
    distinct_vids = select(FactCombustibleDaily.vehicle_id).distinct()
    distinct_vids, _empty_v2 = _apply_vehicle_scope(
        distinct_vids, FactCombustibleDaily.vehicle_id, allowed
    )
    all_vocacional = False
    if not _empty_v2:
        distinct_vids = distinct_vids.where(
            _fuel_kind_predicate(FactCombustibleDaily.fuel_kind, fuel_kind)
        )
        distinct_vids = _apply_daily_filters(
            distinct_vids, vehicle_id=vehicle_id, date_from=date_from, date_to=date_to
        )
        dv_sub = distinct_vids.subquery()
        # EXISTS y no un outerjoin: `geotab_databases` repite la misma clave en
        # una fila por flota (13 filas `navitrans` el 2026-09-01), así que un
        # join por clave multiplica cada vehículo por esas filas y las que no
        # traen su `Vehicle` caen a False, con lo que `bool_and` nunca daba
        # True. Es la misma identidad compuesta (base + device) que usan
        # `list_vehicles` y la calificación. Un vehículo sin `dim_vehicle` o sin
        # maestro sigue sin contar como vocacional (fail-closed).
        is_vocacional = sqlalchemy.exists().where(
            Vehicle.geotab_database_id == GeotabDatabase.id,
            Vehicle.geotab_device_id == DimVehicle.device_id,
            func.lower(GeotabDatabase.database_key) == func.lower(DimVehicle.database_name),
            Vehicle.is_active.is_(True),
            Vehicle.vocacional.is_(True),
        )
        vocacional_stmt = (
            select(func.coalesce(func.bool_and(is_vocacional), False))
            .select_from(dv_sub)
            .outerjoin(DimVehicle, DimVehicle.vehicle_id == dv_sub.c.vehicle_id)
        )
        all_vocacional = (await db.execute(vocacional_stmt)).scalar_one()

    return {
        "fuel_kind": fuel_kind,
        "fuel_unit": "m3" if is_gas else "gal",
        "kms_ecm": kms_effective,
        "hrs_ecm": hrs_ecm,
        "hrs_gps": hrs_gps,
        "comb": comb,
        "comb_ralenti": comb_ralenti,
        "km_gal": effective_efficiency if not is_gas else None,
        "gal_hr": fuel_per_hour if not is_gas else None,
        "gal_hr_ralenti": idle_fuel_per_hour if not is_gas else None,
        "km_m3": effective_efficiency if is_gas else None,
        "m3_hr": fuel_per_hour if is_gas else None,
        "m3_hr_ralenti": idle_fuel_per_hour if is_gas else None,
        "velocidad_promedio": effective_speed,
        "pct_ralenti": ralenti_avg,
        "pct_exceso_rpm": exceso_rpm_avg,
        "n_vehiculos": n_vehiculos,
        "n_registros": n_registros,
        "all_vocacional": all_vocacional,
    }


async def get_combustible_timeseries_daily(
    db: AsyncSession,
    *,
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fuel_kind: str = FUEL_KIND_LIQUID,
    fleet_ids: list[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict[str, Any]]:
    """Serie diaria agregada (un punto por date_key)."""
    allowed = fleet_ids
    if allowed == []:
        return []
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return []
    f = FactCombustibleDaily
    is_gas = fuel_kind == FUEL_KIND_GAS
    effective = _effective_distance_expressions(f)
    sum_hrs = func.coalesce(func.sum(f.hrs_ecm), 0.0)
    sum_hrs_with_fuel = (
        func.coalesce(
            func.sum(sqlalchemy.case((f.comb > 0, f.hrs_ecm), else_=None)),
            0.0,
        )
        if is_gas
        else sum_hrs
    )
    sum_comb = func.coalesce(func.sum(f.comb), 0.0)
    sum_comb_ralenti = func.sum(f.comb_ralenti)
    sum_effective_kms = func.coalesce(func.sum(effective["kms"]), 0.0)
    sum_effective_kms_with_fuel = func.coalesce(
        func.sum(
            case(
                (and_(f.comb > 0, effective["kms"].isnot(None)), effective["kms"]),
                else_=None,
            )
        ),
        0.0,
    )
    sum_effective_comb = func.coalesce(
        func.sum(
            case(
                (and_(f.comb > 0, effective["kms"].isnot(None)), f.comb),
                else_=None,
            )
        ),
        0.0,
    )
    sum_effective_hours = func.coalesce(func.sum(effective["hours"]), 0.0)
    idle_hours = _idle_hours(f, is_gas)
    sum_hrs_ralenti = func.coalesce(
        func.sum(
            sqlalchemy.case(
                (f.comb_ralenti.isnot(None), idle_hours),
                else_=0.0,
            )
        ),
        0.0,
    )
    stmt = select(
        f.date_key,
        func.max(f.fecha),
        func.coalesce(func.sum(f.kms_gps), 0.0),
        sum_hrs,
        func.coalesce(func.sum(f.hrs_gps), 0.0),
        sum_hrs_with_fuel,
        sum_comb,
        sum_comb_ralenti,
        sum_hrs_ralenti,
        _weighted(f.pct_ralenti, _ralenti_weight(f)),
        sum_effective_kms,
        sum_effective_kms_with_fuel,
        sum_effective_comb,
        sum_effective_hours,
    )
    stmt, _empty_v = _apply_vehicle_scope(stmt, f.vehicle_id, allowed)
    if _empty_v:
        return []
    stmt = stmt.where(_fuel_kind_predicate(f.fuel_kind, fuel_kind))
    stmt = _apply_daily_filters(stmt, vehicle_id=vehicle_id, date_from=date_from, date_to=date_to)
    stmt = stmt.group_by(f.date_key).order_by(f.date_key)
    result = await db.execute(stmt)
    out: list[dict[str, Any]] = []
    for (
        date_key,
        fecha,
        kms_gps,
        hrs_ecm,
        hrs_gps,
        hrs_ecm_with_fuel,
        comb,
        comb_ralenti,
        hrs_ralenti,
        ralenti,
        kms_effective,
        kms_effective_with_fuel,
        effective_comb,
        effective_hours,
    ) in result.all():
        out.append(
            {
                "fuel_kind": fuel_kind,
                "fuel_unit": "m3" if is_gas else "gal",
                "periodo": date_key,
                "label": fecha.isoformat() if fecha else str(date_key),
                "kms_ecm": kms_effective,
                "kms_gps": kms_gps,
                "hrs_ecm": hrs_ecm,
                "hrs_gps": hrs_gps,
                "comb": comb,
                "comb_ralenti": comb_ralenti,
                # Todas las razones se derivan de las sumas del punto, para que
                # el gráfico cuadre con los totales del KPI y con la tabla.
                "km_gal": (
                    kms_effective_with_fuel / effective_comb
                    if effective_comb and effective_comb > 0 and not is_gas
                    else None
                ),
                "gal_hr": ((comb / hrs_ecm) if hrs_ecm and hrs_ecm > 0 and not is_gas else None),
                "gal_hr_ralenti": (
                    comb_ralenti / hrs_ralenti
                    if hrs_ralenti and hrs_ralenti > 0 and not is_gas
                    else None
                ),
                "km_m3": (
                    kms_effective_with_fuel / effective_comb
                    if effective_comb and effective_comb > 0 and is_gas
                    else None
                ),
                "m3_hr": (
                    (comb / hrs_ecm_with_fuel)
                    if hrs_ecm_with_fuel and hrs_ecm_with_fuel > 0 and is_gas
                    else None
                ),
                "m3_hr_ralenti": (
                    comb_ralenti / hrs_ralenti
                    if hrs_ralenti and hrs_ralenti > 0 and is_gas
                    else None
                ),
                "velocidad_promedio": (
                    kms_effective / effective_hours
                    if effective_hours and effective_hours > 0
                    else None
                ),
                "pct_ralenti": ralenti,
            }
        )
    return out


async def get_combustible_timeseries_monthly(
    db: AsyncSession,
    *,
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fuel_kind: str = FUEL_KIND_LIQUID,
    fleet_ids: list[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict[str, Any]]:
    """Serie mensual reagregada desde el diario con decisiones inmediatas."""
    allowed = fleet_ids
    if allowed == []:
        return []
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return []
    f = FactCombustibleDaily
    is_gas = fuel_kind == FUEL_KIND_GAS
    effective = _effective_distance_expressions(f)
    month_key_expr = cast(f.date_key / 100, Integer)
    sum_hrs = func.coalesce(func.sum(f.hrs_ecm), 0.0)
    sum_hrs_with_fuel = func.coalesce(func.sum(case((f.comb > 0, f.hrs_ecm), else_=None)), 0.0)
    sum_comb = func.coalesce(func.sum(f.comb), 0.0)
    sum_comb_ralenti = func.sum(f.comb_ralenti)
    idle_hours = _idle_hours(f, is_gas)
    stmt = select(
        month_key_expr,
        func.coalesce(func.sum(f.kms_gps), 0.0),
        sum_hrs,
        func.coalesce(func.sum(f.hrs_gps), 0.0),
        sum_hrs_with_fuel,
        sum_comb,
        sum_comb_ralenti,
        func.coalesce(
            func.sum(case((f.comb_ralenti.isnot(None), idle_hours), else_=0.0)),
            0.0,
        ),
        func.coalesce(func.sum(effective["kms"]), 0.0),
        func.coalesce(
            func.sum(
                case(
                    (and_(f.comb > 0, effective["kms"].isnot(None)), effective["kms"]),
                    else_=None,
                )
            ),
            0.0,
        ),
        func.coalesce(
            func.sum(
                case(
                    (and_(f.comb > 0, effective["kms"].isnot(None)), f.comb),
                    else_=None,
                )
            ),
            0.0,
        ),
        func.coalesce(func.sum(effective["hours"]), 0.0),
        _weighted(f.pct_ralenti, _ralenti_weight(f)),
    )
    stmt, _empty_v = _apply_vehicle_scope(stmt, f.vehicle_id, allowed)
    if _empty_v:
        return []
    stmt = stmt.where(_fuel_kind_predicate(f.fuel_kind, fuel_kind))
    stmt = _apply_daily_filters(stmt, vehicle_id=vehicle_id, date_from=date_from, date_to=date_to)
    stmt = stmt.group_by(month_key_expr).order_by(month_key_expr)
    out: list[dict[str, Any]] = []
    for (
        month_key,
        kms_gps,
        hrs_ecm,
        hrs_gps,
        hrs_ecm_with_fuel,
        comb,
        comb_ralenti,
        hrs_ralenti,
        kms_effective,
        kms_effective_with_fuel,
        effective_comb,
        effective_hours,
        ralenti,
    ) in (await db.execute(stmt)).all():
        out.append(
            {
                "fuel_kind": fuel_kind,
                "fuel_unit": "m3" if is_gas else "gal",
                "periodo": month_key,
                "label": _month_label(int(month_key)),
                "kms_ecm": kms_effective,
                "kms_gps": kms_gps,
                "hrs_ecm": hrs_ecm,
                "hrs_gps": hrs_gps,
                "comb": comb,
                "comb_ralenti": comb_ralenti,
                "km_gal": (
                    kms_effective_with_fuel / effective_comb
                    if effective_comb and effective_comb > 0 and not is_gas
                    else None
                ),
                "gal_hr": ((comb / hrs_ecm) if hrs_ecm and hrs_ecm > 0 and not is_gas else None),
                "gal_hr_ralenti": (
                    comb_ralenti / hrs_ralenti
                    if hrs_ralenti and hrs_ralenti > 0 and not is_gas
                    else None
                ),
                "km_m3": (
                    kms_effective_with_fuel / effective_comb
                    if effective_comb and effective_comb > 0 and is_gas
                    else None
                ),
                "m3_hr": (
                    (comb / hrs_ecm_with_fuel)
                    if hrs_ecm_with_fuel and hrs_ecm_with_fuel > 0 and is_gas
                    else None
                ),
                "m3_hr_ralenti": (
                    comb_ralenti / hrs_ralenti
                    if hrs_ralenti and hrs_ralenti > 0 and is_gas
                    else None
                ),
                "velocidad_promedio": (
                    kms_effective / effective_hours
                    if effective_hours and effective_hours > 0
                    else None
                ),
                "pct_ralenti": ralenti,
            }
        )
    return out


async def get_vehicle_ranking(
    db: AsyncSession,
    *,
    metric: str = "comb",
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fuel_kind: str = FUEL_KIND_LIQUID,
    fleet_ids: list[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 10,
    sort_order: str = "desc",
) -> list[dict[str, Any]]:
    """Top vehículos por métrica. km/gal y gal/h son derivados (sum/sum); el resto avg/sum."""
    if metric not in RANKING_METRICS:
        raise ValueError(f"metric inválida: {metric!r}. Válidas: {sorted(RANKING_METRICS)}")
    allowed = fleet_ids
    if allowed == []:
        return []
    if vehicle_id == []:
        return []
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return []
    f = FactCombustibleDaily
    effective = _effective_distance_expressions(f)
    effective_kms_with_fuel = func.coalesce(
        func.sum(
            case(
                (and_(f.comb > 0, effective["kms"].isnot(None)), effective["kms"]),
                else_=None,
            )
        ),
        0.0,
    )
    effective_comb = func.coalesce(
        func.sum(
            case(
                (and_(f.comb > 0, effective["kms"].isnot(None)), f.comb),
                else_=None,
            )
        ),
        0.0,
    )
    value: ColumnElement[float | None]
    if metric == "comb":
        value = func.coalesce(func.sum(f.comb), 0.0)
    elif metric == "kms_ecm":
        value = func.coalesce(func.sum(effective["kms"]), 0.0)
    elif metric in {"km_m3", "km_gal"}:
        value = effective_kms_with_fuel / func.nullif(effective_comb, 0)
    elif metric in {"gal_hr", "m3_hr"}:
        # Misma razón de sumas que `gal_hr` del resumen: combustible total sobre
        # las horas ECM de los días con consumo. Promediar los ratios diarios
        # pesaría igual una jornada de dos horas que una completa.
        hrs_ecm_with_fuel = func.coalesce(
            func.sum(case((f.comb > 0, f.hrs_ecm), else_=None)), 0.0
        )
        value = func.coalesce(func.sum(f.comb), 0.0) / func.nullif(hrs_ecm_with_fuel, 0)
    elif metric == "pct_exceso_rpm":
        value = _weighted(f.pct_exceso_rpm, f.tiempo_total_en_rango)
    else:  # pct_ralenti
        value = _weighted(f.pct_ralenti, _ralenti_weight(f))

    stmt = select(
        f.vehicle_id,
        func.max(DimVehicle.vehicle_label),
        func.max(f.placa),
        value.label("value"),
    ).join(DimVehicle, DimVehicle.vehicle_id == f.vehicle_id, isouter=True)
    stmt, _empty_v = _apply_vehicle_scope(stmt, f.vehicle_id, allowed)
    if _empty_v:
        return []
    stmt = stmt.where(_fuel_kind_predicate(f.fuel_kind, fuel_kind))
    stmt = _apply_daily_filters(stmt, vehicle_id=vehicle_id, date_from=date_from, date_to=date_to)
    stmt = stmt.group_by(f.vehicle_id).order_by(
        func.coalesce(value, 0).asc() if sort_order == "asc" else func.coalesce(value, 0).desc()
    )
    if limit > 0:
        stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return [
        {
            "vehicle_id": vehicle_id,
            "vehicle_label": label,
            "placa": placa,
            "value": float(val) if val is not None else None,
        }
        for vehicle_id, label, placa, val in result.all()
    ]


# ---------------------------------------------------------------------------
# Hábitos operativos (rangos de RPM, ralentí, pedal) — fact_combustible + pedal
# ---------------------------------------------------------------------------


# Bandas de RPM expuestas en operativos: total + sin descenso + descenso.
# pct_ralenti va al final (comparación, no apilado). Las claves coinciden con las
# columnas de analytics.fact_combustible_daily y con el schema OperativoMonthlyPoint.
_OPERATIVO_BANDS: tuple[str, ...] = (
    "pct_rango_bajo",
    "pct_rango_economico",
    "pct_rango_balanceado",
    "pct_rango_potencia",
    "pct_rango_potencia_ineficiente",
    "pct_exceso_rpm",
    "pct_ralenti",
    "pct_rango_bajo_sin_descenso",
    "pct_rango_economico_sin_descenso",
    "pct_rango_balanceado_sin_descenso",
    "pct_rango_potencia_sin_descenso",
    "pct_rango_potencia_ineficiente_sin_descenso",
    "pct_exceso_rpm_sin_descenso",
    "pct_rango_bajo_descenso",
    "pct_rango_economico_descenso",
    "pct_rango_balanceado_descenso",
    "pct_rango_potencia_descenso",
    "pct_rango_potencia_ineficiente_descenso",
    "pct_exceso_rpm_descenso",
)


def _band_weight(f, band: str):
    """Denominador con el que el ETL calculó esa banda.

    Cada variante se divide entre un tiempo distinto (total, sin descenso, de
    descenso), y ralentí no sale del tiempo en rango sino de las horas de motor.
    Reagregar con el peso equivocado desbalancea el apilado igual que no ponderar.
    """
    if band == "pct_ralenti":
        return _ralenti_weight(f)
    if band.endswith("_sin_descenso"):
        return f.tiempo_total_en_rango_sin_descenso
    if band.endswith("_descenso"):
        return f.tiempo_total_en_rango_de_descenso
    return f.tiempo_total_en_rango


async def get_operativos_timeseries(
    db: AsyncSession,
    *,
    granularity: str = "monthly",
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fleet_ids: list[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict[str, Any]]:
    """Promedios de las bandas de RPM y ralentí, agrupados por día o mes.

    Cada banda viene como fracción 0..1; el front las apila a 100% y compara
    rango bajo vs ralentí.
    """
    allowed = fleet_ids
    if allowed == []:
        return []
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return []
    f = FactCombustibleDaily
    h = FactHabitoEvent
    # Bandas en 3 variantes: total (apilado histórico), sin descenso (gráfico
    # principal) y descenso (gráfico aparte). pct_ralenti es métrica de comparación,
    # no banda apilada.
    #
    # Ponderadas por el tiempo que las produjo: promediarlas sin peso hacía que
    # el apilado no llegara a 100 % (76 % en 2026-07), porque los días cortos y
    # los días sin operación pesaban igual que una jornada completa. Ralentí se
    # pondera por horas: se calcula contra horas de motor, no contra el tiempo
    # en rango.
    band_cols = [_weighted(getattr(f, name), _band_weight(f, name)) for name in _OPERATIVO_BANDS]
    rpm_period = h.date_key if granularity == "daily" else cast(h.date_key / 100, Integer)
    load_value = _load_value_expression(h)
    rpm_count_stmt = (
        select(
            rpm_period.label("period_key"),
            func.count(),
            func.sum(
                case(
                    (
                        and_(
                            h.event_type.ilike("%rpm%"),
                            load_value.isnot(None),
                            load_value < 5.0,
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
        )
        .select_from(h)
        .where(h.event_type.ilike("%rpm%"))
    )
    rpm_count_stmt, _empty_rpm = _apply_vehicle_scope(rpm_count_stmt, h.vehicle_id, allowed)
    if _empty_rpm:
        return []
    if vehicle_id is not None:
        rpm_count_stmt = rpm_count_stmt.where(h.vehicle_id.in_(vehicle_id))
    if date_from is not None:
        rpm_count_stmt = rpm_count_stmt.where(h.date_key >= _date_to_key(date_from))
    if date_to is not None:
        rpm_count_stmt = rpm_count_stmt.where(h.date_key <= _date_to_key(date_to))
    rpm_count_stmt = rpm_count_stmt.group_by(rpm_period)
    rpm_counts = {
        int(period_key): {
            "total": int(count),
            "descenso": int(descenso or 0),
        }
        for period_key, count, descenso in (await db.execute(rpm_count_stmt)).all()
        if period_key is not None
    }
    if granularity == "daily":
        stmt = select(f.date_key, func.max(f.fecha), *band_cols)
        stmt, _empty_v = _apply_vehicle_scope(stmt, f.vehicle_id, allowed)
        if _empty_v:
            return []
        stmt = _apply_daily_filters(
            stmt, vehicle_id=vehicle_id, date_from=date_from, date_to=date_to
        )
        stmt = stmt.group_by(f.date_key).order_by(f.date_key)
        out: list[dict[str, Any]] = []
        for date_key, fecha, *vals in (await db.execute(stmt)).all():
            point = {"periodo": date_key, "label": fecha.isoformat() if fecha else str(date_key)}
            point.update(dict(zip(_OPERATIVO_BANDS, vals, strict=True)))
            rpm = rpm_counts.get(int(date_key), {"total": 0, "descenso": 0})
            point["eventos_rpm"] = rpm["total"]
            point["eventos_rpm_descenso"] = rpm["descenso"]
            point["eventos_rpm_sin_descenso"] = max(rpm["total"] - rpm["descenso"], 0)
            out.append(point)
        return out
    else:
        month_key = cast(f.date_key / 100, Integer)
        stmt = select(month_key.label("month_key"), *band_cols)
        stmt, _empty_v = _apply_vehicle_scope(stmt, f.vehicle_id, allowed)
        if _empty_v:
            return []
        stmt = _apply_daily_filters(
            stmt, vehicle_id=vehicle_id, date_from=date_from, date_to=date_to
        )
        stmt = stmt.group_by(month_key).order_by(month_key)
        out = []
        for mk, *vals in (await db.execute(stmt)).all():
            point = {"periodo": mk, "label": _month_label(mk)}
            point.update(dict(zip(_OPERATIVO_BANDS, vals, strict=True)))
            rpm = rpm_counts.get(int(mk), {"total": 0, "descenso": 0})
            point["eventos_rpm"] = rpm["total"]
            point["eventos_rpm_descenso"] = rpm["descenso"]
            point["eventos_rpm_sin_descenso"] = max(rpm["total"] - rpm["descenso"], 0)
            out.append(point)
        return out


async def get_pedal_summary(
    db: AsyncSession,
    *,
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fleet_ids: list[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    """Resumen del % de presión sobre el pedal del acelerador (gauge)."""
    allowed = fleet_ids
    if allowed == []:
        return {"promedio": None, "n_lecturas": 0, "ultima_lectura": None, "unidad": "%"}
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return {"promedio": None, "n_lecturas": 0, "ultima_lectura": None, "unidad": "%"}
    p = FactPedalReading
    stmt = select(
        func.avg(p.valor),
        func.count(p.valor),
        func.max(p.fecha_y_hora),
        func.max(p.unidad_de_medida),
    )
    stmt, _empty_v = _apply_vehicle_scope(stmt, p.vehicle_id, allowed)
    if _empty_v:
        return {"promedio": None, "n_lecturas": 0, "ultima_lectura": None, "unidad": "%"}
    if vehicle_id is not None:
        stmt = stmt.where(p.vehicle_id.in_(vehicle_id))
    if date_from is not None:
        stmt = stmt.where(p.date_key >= _date_to_key(date_from))
    if date_to is not None:
        stmt = stmt.where(p.date_key <= _date_to_key(date_to))
    promedio, n, ultima, unidad = (await db.execute(stmt)).one()
    return {
        "promedio": promedio,
        "n_lecturas": n or 0,
        "ultima_lectura": ultima,
        "unidad": unidad or "%",
    }


async def get_factor_carga(
    db: AsyncSession,
    *,
    granularity: str = "monthly",
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fleet_ids: list[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    """Factor de carga del motor (%): promedio global + tendencia (diaria o mensual)."""
    allowed = fleet_ids
    if allowed == []:
        return {"promedio": None, "n_lecturas": 0, "monthly": []}
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return {"promedio": None, "n_lecturas": 0, "monthly": []}
    f = FactFactorCargaDaily

    def _filtered(stmt):
        if vehicle_id is not None:
            stmt = stmt.where(f.vehicle_id.in_(vehicle_id))
        if date_from is not None:
            stmt = stmt.where(f.date_key >= _date_to_key(date_from))
        if date_to is not None:
            stmt = stmt.where(f.date_key <= _date_to_key(date_to))
        stmt, _empty_v = _apply_vehicle_scope(stmt, f.vehicle_id, allowed)
        if _empty_v:
            return None
        return stmt

    # PENDIENTE: esto sigue siendo promedio de promedios. `factor_de_carga` ya
    # viene agregado por (vehículo, día), así que un día de una hora pesa igual
    # que una jornada completa — el mismo sesgo que se corrigió en las bandas de
    # RPM y el ralentí. No se puede ponderar todavía: `fact_factor_carga_daily`
    # solo trae `factor_de_carga`, sin horas, kms ni número de lecturas. Para
    # arreglarlo hace falta que `streaming/factor_carga.py` exporte ese peso en
    # el fact; hasta entonces se deja explícito para no aparentar exactitud.
    avg_stmt = _filtered(select(func.avg(f.factor_de_carga), func.count(f.factor_de_carga)))
    if avg_stmt is None:
        return {"promedio": None, "n_lecturas": 0, "monthly": []}
    promedio, n = (await db.execute(avg_stmt)).one()

    if granularity == "daily":
        ts_stmt = _filtered(select(f.date_key, func.max(f.fecha), func.avg(f.factor_de_carga)))
        if ts_stmt is None:
            return {"promedio": promedio, "n_lecturas": n or 0, "monthly": []}
        ts_stmt = ts_stmt.group_by(f.date_key).order_by(f.date_key)
        timeseries = [
            {
                "periodo": dk,
                "label": fecha.isoformat() if fecha else str(dk),
                "factor_de_carga": val,
            }
            for dk, fecha, val in (await db.execute(ts_stmt)).all()
        ]
    else:
        month_key = cast(f.date_key / 100, Integer)
        ts_stmt = _filtered(select(month_key.label("month_key"), func.avg(f.factor_de_carga)))
        if ts_stmt is None:
            return {"promedio": promedio, "n_lecturas": n or 0, "monthly": []}
        ts_stmt = ts_stmt.group_by(month_key).order_by(month_key)
        timeseries = [
            {"periodo": mk, "label": _month_label(mk), "factor_de_carga": val}
            for mk, val in (await db.execute(ts_stmt)).all()
        ]
    return {"promedio": promedio, "n_lecturas": n or 0, "monthly": timeseries}


# ---------------------------------------------------------------------------
# Hábitos seguros de conducción (fact_habito_event + dim_rule)
# ---------------------------------------------------------------------------


def _rpm_value_expression(h=FactHabitoEvent):
    """RPM efectivas del evento: columna numérica del ETL, o el regex histórico.

    Desde 2026-08-27 el ETL publica `fact_habito_event.rpm` como float. Es la
    fuente preferida: leerla cuesta lo que cuesta leer una columna, mientras el
    `substring` con regex sobre `observacion_corta` costaba 7-17 s por consulta
    sobre 30 días.

    **La rama del regex NO se puede borrar.** Las ~296 mil filas históricas de
    RPM tienen `rpm` NULL hasta que el ETL reprocese el histórico, así que
    hasta entonces el COALESCE cae al regex y el resultado es idéntico al de
    antes; cuando el backfill llegue, la misma expresión se vuelve rápida sola,
    fila por fila, sin cambio de código ni de contrato.

    El ETL ha producido ambos órdenes en el texto (`2500 RPM` y `RPM: 2500`).
    Cuando no existe lectura por ninguna vía deja `N/A RPM`; en ese caso la
    expresión es NULL y el evento conserva peso base, pero no la agravación por
    RPM altas.
    """
    observation = func.lower(func.coalesce(h.observacion_corta, ""))
    rpm_text = func.coalesce(
        func.substring(
            observation,
            r"rpm\s*[:=]?\s*([0-9]+([.,][0-9]+)?)",
        ),
        func.substring(
            observation,
            r"([0-9]+([.,][0-9]+)?)\s*rpm",
        ),
    )
    return func.coalesce(h.rpm, func.replace(rpm_text, ",", ".").cast(Float))


def _load_value_expression(h=FactHabitoEvent):
    """Carga porcentual asociada al evento RPM.

    Mismo criterio que `_rpm_value_expression`: se prefiere la columna
    numérica `carga_pct` que el ETL publica desde 2026-08-27 y se cae al regex
    sobre ``Observacion Corta`` (``Carga: n%``) mientras el histórico no esté
    reprocesado. La rama del regex no se puede borrar.

    Un evento se considera descenso únicamente cuando la lectura existe y es
    estrictamente menor a 5%; los eventos sin lectura no se fuerzan a esa
    categoría.
    """
    observation = func.lower(func.coalesce(h.observacion_corta, ""))
    load_text = func.coalesce(
        func.substring(
            observation,
            r"carga\s*[:=]?\s*([0-9]+([.,][0-9]+)?)\s*%",
        ),
        func.substring(
            observation,
            r"([0-9]+([.,][0-9]+)?)\s*%\s*carga",
        ),
    )
    return func.coalesce(h.carga_pct, func.replace(load_text, ",", ".").cast(Float))


def _motor_rpm_limit_column(rpm_threshold: str):
    """Columna de `public.motor_catalog` que corresponde al umbral pedido.

    ÚNICA fuente de esa correspondencia. Las dos formas de resolver el límite
    —subconsulta correlacionada y join— la comparten, para que no puedan
    desalinearse: si mañana aparece un tercer umbral se agrega aquí y las dos
    lo heredan.
    """
    if rpm_threshold == "governed":
        return MotorCatalog.governed_speed_rpm
    if rpm_threshold == "overspeed":
        return MotorCatalog.max_overspeed_rpm
    raise ValueError(
        f"rpm_threshold debe ser 'governed' o 'overspeed', no {rpm_threshold!r}"
    )


def _motor_rpm_limit_subquery(rpm_threshold: str, h=FactHabitoEvent):
    """Límite de RPM del motor del vehículo del evento, como subconsulta escalar.

    `fact_habito_event` no trae `motor_type`: se resuelve por
    `analytics.dim_vehicle.motor_type` y de ahí a `public.motor_catalog`
    (misma base física, join válido en una sola sentencia).

    Se devuelve como subconsulta correlacionada **a propósito**: los filtros de
    hábitos se aplican a siete sentencias distintas, cada una con sus propios
    joins; añadir joins aquí rompería alguna. `correlate(h)` fija que lo único
    que se toma del query externo es `fact_habito_event`, de modo que
    `dim_vehicle` y `motor_catalog` nunca se "escapan" al FROM exterior aunque
    la sentencia externa ya los joinee (p. ej. ranking y eventos).

    Es la forma correcta para un WHERE que ya recorta filas, y la equivocada
    para un agregado que evalúa el límite en CADA fila: ahí usar
    `_join_motor_rpm_limit`, que mide igual y cuesta lo mismo que no resolver
    nada (ver su docstring).
    """
    return (
        select(_motor_rpm_limit_column(rpm_threshold))
        .select_from(DimVehicle)
        .join(MotorCatalog, MotorCatalog.motor_type == DimVehicle.motor_type)
        .where(DimVehicle.vehicle_id == h.vehicle_id)
        .limit(1)
        .correlate(h)
        .scalar_subquery()
    )


def _join_motor_rpm_limit(stmt, h=FactHabitoEvent):
    """Añade el camino evento → vehículo → motor → catálogo como LEFT JOIN.

    Mismo destino que `_motor_rpm_limit_subquery` y mismo camino
    (`dim_vehicle.motor_type` → `motor_catalog.motor_type`); la columna del
    umbral sale del mismo `_motor_rpm_limit_column`. Lo que cambia es la forma,
    y con ella el coste: una subconsulta correlacionada se evalúa por fila, así
    que en un agregado sobre la ventana entera de hábitos es cara, mientras el
    join la resuelve una vez por vehículo.

    Medido el 2026-08-27 sobre el agregado mensual de la calificación,
    2026-06-01 a 2026-08-27, 161.378 eventos de RPM y 1.193 grupos, mejor de
    dos corridas: constante fija 15,80 s · subconsulta correlacionada 25,37 s ·
    join 15,75 s, los tres con resultado idéntico (57.739 agravados en las dos
    formas por motor). Con `statement_timeout` en 30 s, la subconsulta dejaba
    `/reportes/calificacion` a 2 s del corte; el join la deja donde estaba.

    **No cambia la cardinalidad**, que es lo que permite seguir contando
    eventos con `func.count()` en la misma sentencia: `dim_vehicle.vehicle_id`
    es PK y `motor_catalog.motor_type` también, así que cada evento produce
    exactamente una fila, y los dos joins son `isouter` para que un vehículo
    sin dimensión o un motor sin catálogo no borre el evento del agregado.
    """
    return stmt.join(
        DimVehicle, DimVehicle.vehicle_id == h.vehicle_id, isouter=True
    ).join(
        MotorCatalog, MotorCatalog.motor_type == DimVehicle.motor_type, isouter=True
    )


def _apply_habito_filters(
    stmt,
    *,
    vehicle_id: list[str] | None,
    date_from: date | None,
    date_to: date | None,
    event_type: str | None,
    rpm_min: float | None = None,
    rpm_threshold: str | None = None,
    categoria: str | None,
    allowed_vehicle_ids: Sequence[uuid.UUID] | None = None,
):
    """Filtros estándar de hábitos. `categoria` filtra vía join a dim_rule.

    Aislamiento (P0): el control de acceso se aplica sobre `h.vehicle_id`,
    no sobre `h.database_name` (database_name puede ser compartido entre
    flotas y no es frontera de seguridad).

    `rpm_min` es el umbral escalar histórico (un solo número para toda la
    flota) y se conserva por compatibilidad del contrato.

    `rpm_threshold` (`"governed"` | `"overspeed"`) es el filtro correcto:
    compara las RPM del evento contra el límite del motor *de ese* vehículo
    (`public.motor_catalog.governed_speed_rpm` o `.max_overspeed_rpm`).

    **Fail-closed, por decisión de diseño:** un motor cuyo límite es NULL
    —el dato no está capturado en Navi Vehículos— queda EXCLUIDO del
    resultado, no incluido sin filtrar. Un exceso sobre un límite que no
    conocemos no es demostrable, e inventarlo sería peor que omitirlo.
    Ejemplo real: el A26 no tiene `max_overspeed_rpm`, así que con
    `rpm_threshold="overspeed"` sus eventos no salen.

    Si llegan `rpm_min` y `rpm_threshold` a la vez, se aplican ambos con AND.
    """
    h = FactHabitoEvent
    stmt, _empty = _apply_vehicle_scope(
        stmt,
        h.vehicle_id,
        allowed_vehicle_ids,
    )
    if _empty:
        return stmt
    if vehicle_id is not None:
        stmt = stmt.where(h.vehicle_id.in_(vehicle_id))
    if date_from is not None:
        stmt = stmt.where(h.date_key >= _date_to_key(date_from))
    if date_to is not None:
        stmt = stmt.where(h.date_key <= _date_to_key(date_to))
    if event_type is not None:
        stmt = stmt.where(h.event_type == event_type)
    # Los dos filtros de RPM omiten a propósito el `IS NOT NULL` que antes
    # acompañaba a cada comparación. Bajo la lógica trivalente de SQL,
    # `x > NULL` y `NULL > x` valen UNKNOWN, y una fila con WHERE en UNKNOWN
    # queda fuera igual que con FALSE: la comparación YA excluye el NULL y la
    # afirmación previa era redundante.
    #
    # No era gratis. Ambos operandos son caros —`_rpm_value_expression` lleva
    # un `substring` con expresión regular sobre `observacion_corta` de todo el
    # rango, y `_motor_rpm_limit_subquery` es una escalar correlacionada— y
    # nombrarlos dos veces hacía que el plan los evaluara dos veces por fila.
    # El fail-closed no se relaja: un motor sin el límite capturado sigue sin
    # producir eventos, ahora porque la comparación da UNKNOWN en vez de porque
    # una segunda evaluación del mismo valor diga que es NULL.
    if rpm_min is not None:
        stmt = stmt.where(
            h.event_type.ilike("%rpm%"),
            _rpm_value_expression(h) > rpm_min,
        )
    if rpm_threshold is not None:
        if rpm_threshold not in ("governed", "overspeed"):
            raise ValueError(
                "rpm_threshold debe ser 'governed' o 'overspeed', "
                f"no {rpm_threshold!r}"
            )
        stmt = stmt.where(
            h.event_type.ilike("%rpm%"),
            _rpm_value_expression(h) > _motor_rpm_limit_subquery(rpm_threshold, h),
        )
    if categoria is not None:
        stmt = stmt.where(DimRule.categoria == categoria)
    return stmt


async def get_habito_summary(
    db: AsyncSession,
    *,
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fleet_ids: list[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    event_type: str | None = None,
    rpm_min: float | None = None,
    rpm_threshold: str | None = None,
) -> dict[str, Any]:
    allowed = fleet_ids
    if allowed == []:
        return {
            "n_eventos": 0,
            "n_vehiculos": 0,
            "n_tipos": 0,
            "duracion_promedio": None,
            "distancia_total_mt": 0.0,
        }
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return {
            "n_eventos": 0,
            "n_vehiculos": 0,
            "n_tipos": 0,
            "duracion_promedio": None,
            "distancia_total_mt": 0.0,
        }

    h = FactHabitoEvent
    stmt = (
        select(
            func.count(),
            func.count(func.distinct(h.vehicle_id)),
            func.count(func.distinct(h.event_type)),
            _avg(h.duracion_evento),
            func.coalesce(func.sum(h.distancia_evento_mt), 0.0),
        )
        .select_from(h)
        .join(DimRule, DimRule.rule_sk == h.rule_sk, isouter=True)
    )
    stmt = _apply_habito_filters(
        stmt,
        vehicle_id=vehicle_id,
        date_from=date_from,
        date_to=date_to,
        event_type=event_type,
        rpm_min=rpm_min,
        rpm_threshold=rpm_threshold,
        categoria=HABITO_CATEGORIA,
        allowed_vehicle_ids=allowed,
    )
    n_eventos, n_vehiculos, n_tipos, dur_avg, dist_total = (await db.execute(stmt)).one()
    return {
        "n_eventos": n_eventos,
        "n_vehiculos": n_vehiculos,
        "n_tipos": n_tipos,
        "duracion_promedio": dur_avg,
        "distancia_total_mt": dist_total,
    }


async def get_habito_by_type(
    db: AsyncSession,
    *,
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fleet_ids: list[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    rpm_min: float | None = None,
    rpm_threshold: str | None = None,
) -> list[dict[str, Any]]:
    """Distribución de eventos por tipo (Exceso Velocidad, Frenada Brusca, etc.)."""
    allowed = fleet_ids
    if allowed == []:
        return []
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return []
    h = FactHabitoEvent
    stmt = (
        select(
            h.event_type,
            func.count(),
            _avg(h.duracion_evento),
            func.coalesce(func.sum(h.distancia_evento_mt), 0.0),
        )
        .select_from(h)
        .join(DimRule, DimRule.rule_sk == h.rule_sk, isouter=True)
    )
    stmt = _apply_habito_filters(
        stmt,
        vehicle_id=vehicle_id,
        date_from=date_from,
        date_to=date_to,
        event_type=None,
        rpm_min=rpm_min,
        rpm_threshold=rpm_threshold,
        categoria=HABITO_CATEGORIA,
        allowed_vehicle_ids=allowed,
    )
    stmt = stmt.group_by(h.event_type).order_by(func.count().desc())
    result = await db.execute(stmt)
    return [
        {
            "event_type": event_type or "—",
            "n_eventos": n,
            "duracion_promedio": dur,
            "distancia_total_mt": dist,
        }
        for event_type, n, dur, dist in result.all()
    ]


async def get_habito_timeseries_daily(
    db: AsyncSession,
    *,
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fleet_ids: list[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    event_type: str | None = None,
    rpm_min: float | None = None,
    rpm_threshold: str | None = None,
) -> list[dict[str, Any]]:
    allowed = fleet_ids
    if allowed == []:
        return []
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return []
    h = FactHabitoEvent
    stmt = (
        select(h.date_key, func.max(h.fecha), func.count())
        .select_from(h)
        .join(DimRule, DimRule.rule_sk == h.rule_sk, isouter=True)
    )
    stmt = _apply_habito_filters(
        stmt,
        vehicle_id=vehicle_id,
        date_from=date_from,
        date_to=date_to,
        event_type=event_type,
        rpm_min=rpm_min,
        rpm_threshold=rpm_threshold,
        categoria=HABITO_CATEGORIA,
        allowed_vehicle_ids=allowed,
    )
    stmt = stmt.group_by(h.date_key).order_by(h.date_key)
    result = await db.execute(stmt)
    return [
        {
            "periodo": date_key,
            "label": fecha.isoformat() if fecha else str(date_key),
            "n_eventos": n,
        }
        for date_key, fecha, n in result.all()
    ]


async def get_habito_timeseries_monthly(
    db: AsyncSession,
    *,
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fleet_ids: list[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    event_type: str | None = None,
    rpm_min: float | None = None,
    rpm_threshold: str | None = None,
) -> list[dict[str, Any]]:
    allowed = fleet_ids
    if allowed == []:
        return []
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return []
    h = FactHabitoEvent
    month_key = cast(h.date_key / 100, Integer)
    stmt = (
        select(month_key.label("month_key"), func.count())
        .select_from(h)
        .join(DimRule, DimRule.rule_sk == h.rule_sk, isouter=True)
    )
    stmt = _apply_habito_filters(
        stmt,
        vehicle_id=vehicle_id,
        date_from=date_from,
        date_to=date_to,
        event_type=event_type,
        rpm_min=rpm_min,
        rpm_threshold=rpm_threshold,
        categoria=HABITO_CATEGORIA,
        allowed_vehicle_ids=allowed,
    )
    stmt = stmt.group_by(month_key).order_by(month_key)
    result = await db.execute(stmt)
    return [
        {
            "periodo": mk,
            "label": _month_label(mk),
            "n_eventos": n,
        }
        for mk, n in result.all()
    ]


async def get_habito_ranking(
    db: AsyncSession,
    *,
    vehicle_id: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    event_type: str | None = None,
    rpm_min: float | None = None,
    rpm_threshold: str | None = None,
    motor_type: list[str] | None = None,
    fleet_ids: list[uuid.UUID] | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Top vehículos por número de eventos de hábito."""
    allowed = fleet_ids
    if allowed == []:
        return []
    if vehicle_id == []:
        return []
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return []
    h = FactHabitoEvent
    stmt = (
        select(
            h.vehicle_id,
            func.max(DimVehicle.vehicle_label),
            func.max(h.placa),
            func.count().label("value"),
        )
        .select_from(h)
        .join(DimRule, DimRule.rule_sk == h.rule_sk, isouter=True)
        .join(DimVehicle, DimVehicle.vehicle_id == h.vehicle_id, isouter=True)
    )
    stmt = _apply_habito_filters(
        stmt,
        vehicle_id=vehicle_id,
        date_from=date_from,
        date_to=date_to,
        event_type=event_type,
        rpm_min=rpm_min,
        rpm_threshold=rpm_threshold,
        categoria=HABITO_CATEGORIA,
        allowed_vehicle_ids=allowed,
    )
    stmt = stmt.group_by(h.vehicle_id).order_by(func.count().desc()).limit(limit)
    result = await db.execute(stmt)
    return [
        {
            "vehicle_id": vehicle_id,
            "vehicle_label": label,
            "placa": placa,
            "value": float(val) if val is not None else None,
        }
        for vehicle_id, label, placa, val in result.all()
    ]


#: Allowlist ESTRICTA de columnas ordenables en `/reportes/habitos/events`.
#: El `sort_by` llega del cliente: sólo estas claves se traducen a una columna
#: y cualquier otra es un ValueError. Nunca interpolar el valor recibido en la
#: sentencia ni hacer `getattr(h, sort_by)`.
HABITO_EVENT_SORT_FIELDS = (
    "fecha",
    "event_value",
    "rpm",
    "velocidad_kmh",
    "g_force",
    "duracion_evento",
    "distancia_evento_mt",
)


def _habito_event_order_by(sort_by: str, sort_dir: str, h=FactHabitoEvent) -> list[Any]:
    """Cláusula ORDER BY total y estable para el listado de eventos de hábitos.

    Tres invariantes, y las tres importan:

    1. **Allowlist.** `sort_by` viene del cliente; se resuelve contra un dict
       cerrado. No hay `getattr` ni interpolación de texto.
    2. **Orden total.** Todo criterio desempata con `h.event_sk`, que es PK.
       Sin ese desempate la paginación por OFFSET repite y omite filas entre
       páginas cuando hay empates (y los hay: `duracion_evento` o `g_force`
       repiten valor con facilidad).
    3. **NULL siempre al final, en ambas direcciones.** No es simetría: quien
       ordena por una métrica lo hace para ver los extremos, y un NULL
       —métrica que el ETL todavía no publicó para esa fila— no es un máximo
       ni un mínimo, es ausencia de dato. Empujarlos al final en `asc` y en
       `desc` mantiene la primera página siempre informativa. En PostgreSQL
       `ASC` ya implica NULLS LAST, pero `DESC` implica NULLS FIRST: por eso
       `nullslast()` se aplica explícitamente en los dos casos.

    `rpm` ordena por el RPM *efectivo* (`_rpm_value_expression`, el COALESCE de
    columna nueva y regex histórico), que es el mismo número que el listado
    devuelve en `rpm_value`. Ordenar por la columna cruda daría hoy un orden
    vacío de significado: está NULL en todo el histórico.
    """
    if sort_dir not in ("asc", "desc"):
        raise ValueError(f"sort_dir debe ser 'asc' o 'desc', no {sort_dir!r}")
    columns: dict[str, Any] = {
        "fecha": h.fecha_y_hora_del_evento,
        "event_value": h.event_value,
        "rpm": _rpm_value_expression(h),
        "velocidad_kmh": h.velocidad_kmh,
        "g_force": h.g_force,
        "duracion_evento": h.duracion_evento,
        "distancia_evento_mt": h.distancia_evento_mt,
    }
    column = columns.get(sort_by)
    if column is None:
        raise ValueError(
            f"sort_by debe ser uno de {', '.join(HABITO_EVENT_SORT_FIELDS)}, "
            f"no {sort_by!r}"
        )
    descending = sort_dir == "desc"
    primary = (column.desc() if descending else column.asc()).nullslast()
    tiebreak = h.event_sk.desc() if descending else h.event_sk.asc()
    return [primary, tiebreak]


async def list_habito_events(
    db: AsyncSession,
    *,
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fleet_ids: list[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    event_type: str | None = None,
    rpm_min: float | None = None,
    rpm_threshold: str | None = None,
    sort_by: str = "fecha",
    sort_dir: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    allowed = fleet_ids
    if allowed == []:
        return [], 0
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return [], 0
    h = FactHabitoEvent
    # `rpm_value` se resuelve en SQL (no con un regex en el cliente) y el
    # contexto de motor sale de dos LEFT OUTER JOIN. Ninguno cambia la
    # cardinalidad: `dim_vehicle.vehicle_id` es PK y `motor_catalog.motor_type`
    # también, así que cada evento sigue produciendo exactamente una fila y el
    # `count_stmt` sobre `base.subquery()` continúa contando eventos.
    base = (
        select(
            h,
            DimRule.rule_name,
            DimRule.categoria,
            _rpm_value_expression(h).label("rpm_value"),
            DimVehicle.motor_type,
            MotorCatalog.governed_speed_rpm,
            MotorCatalog.max_overspeed_rpm,
        )
        .join(DimRule, DimRule.rule_sk == h.rule_sk, isouter=True)
        .join(DimVehicle, DimVehicle.vehicle_id == h.vehicle_id, isouter=True)
        .join(MotorCatalog, MotorCatalog.motor_type == DimVehicle.motor_type, isouter=True)
    )
    base = _apply_habito_filters(
        base,
        vehicle_id=vehicle_id,
        date_from=date_from,
        date_to=date_to,
        event_type=event_type,
        rpm_min=rpm_min,
        rpm_threshold=rpm_threshold,
        categoria=HABITO_CATEGORIA,
        allowed_vehicle_ids=allowed,
    )
    # Aquí el total NO va como ventana. Medido contra la base real, `count(*)
    # OVER ()` deja esta consulta entre un 3 % y un 54 % más lenta: al ser una
    # selección plana con `ORDER BY` respaldado por índice, el planificador
    # resuelve la página por top-N y se detiene en `LIMIT`; la ventana lo
    # obliga a materializar el conjunto completo para poder contarlo. En
    # `list_fault_events` sí conviene, porque allí el `GROUP BY` ya recorre
    # todo y la segunda pasada era gratis de eliminar.
    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        base.order_by(*_habito_event_order_by(sort_by, sort_dir, h))
        .limit(limit)
        .offset(offset)
    )
    rows: list[dict[str, Any]] = []
    for (
        event,
        rule_name,
        categoria,
        rpm_value,
        motor_type_row,
        governed_limit,
        overspeed_limit,
    ) in (await db.execute(stmt)).all():
        rows.append(
            {
                "event_sk": event.event_sk,
                "vehicle_id": event.vehicle_id,
                "placa": event.placa,
                "fecha": event.fecha,
                "fecha_y_hora_del_evento": event.fecha_y_hora_del_evento,
                "event_type": event.event_type,
                "rule_name": rule_name,
                "categoria": categoria,
                "duracion_evento": event.duracion_evento,
                "distancia_evento_mt": event.distancia_evento_mt,
                "observacion_corta": event.observacion_corta,
                "latitud": event.latitud,
                "longitud": event.longitud,
                "motor_type": motor_type_row,
                "rpm_value": rpm_value,
                "rpm_governed_limit": governed_limit,
                "rpm_overspeed_limit": overspeed_limit,
                # Métricas numéricas del ETL. Salen de la entidad, no de un
                # regex: NULL mientras el histórico no esté reprocesado.
                "velocidad_kmh": event.velocidad_kmh,
                "carga_pct": event.carga_pct,
                "g_force": event.g_force,
                "g_axis": event.g_axis,
                "event_value": event.event_value,
                "event_value_unit": event.event_value_unit,
            }
        )
    return rows, total


async def list_habito_map_points(
    db: AsyncSession,
    *,
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fleet_ids: list[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    event_type: str | None = None,
    rpm_min: float | None = None,
    rpm_threshold: str | None = None,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    """Eventos con coordenadas para pintar en el mapa (solo lat/lon no nulos)."""
    allowed = fleet_ids
    if allowed == []:
        return []
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return []
    h = FactHabitoEvent
    stmt = (
        select(
            h.event_sk,
            h.placa,
            h.event_type,
            h.fecha_y_hora_del_evento,
            h.duracion_evento,
            h.observacion_corta,
            h.latitud,
            h.longitud,
        )
        .select_from(h)
        .join(DimRule, DimRule.rule_sk == h.rule_sk, isouter=True)
        .where(h.latitud.isnot(None), h.longitud.isnot(None))
    )
    stmt = _apply_habito_filters(
        stmt,
        vehicle_id=vehicle_id,
        date_from=date_from,
        date_to=date_to,
        event_type=event_type,
        rpm_min=rpm_min,
        rpm_threshold=rpm_threshold,
        categoria=HABITO_CATEGORIA,
        allowed_vehicle_ids=allowed,
    )
    stmt = stmt.order_by(h.fecha_y_hora_del_evento.desc().nullslast()).limit(limit)
    return [
        {
            "event_sk": event_sk,
            "placa": placa,
            "event_type": event_type_,
            "fecha_y_hora_del_evento": fecha,
            "duracion_evento": dur,
            "observacion_corta": observacion,
            "latitud": lat,
            "longitud": lon,
        }
        for event_sk, placa, event_type_, fecha, dur, observacion, lat, lon in (
            await db.execute(stmt)
        ).all()
    ]


# ---------------------------------------------------------------------------
# Fallas / alertas técnicas (fact_fault_event)
# ---------------------------------------------------------------------------


def _normalized_service_model_name_expression(column: Any) -> Any:
    """Versión SQL del normalizador de dateplate usado por Navifault.

    El mapa operativo guarda la forma sin acentos, con espacios colapsados y
    en mayúsculas. Esta expresión permite aplicar el filtro antes de paginar,
    sin traer miles de eventos al frontend. ``translate`` cubre los acentos
    presentes en los nombres de servicio de la flota.
    """
    return func.upper(
        func.regexp_replace(
            func.translate(
                func.btrim(func.coalesce(column, "")),
                "ÁÉÍÓÚÜÑáéíóúüñ",
                "AEIOUUNAEIOUUN",
            ),
            "[[:space:]]+",
            " ",
            "g",
        )
    )


def _navifault_candidate_page_count(fault: Any) -> Any:
    """Cuenta páginas FC distintas que resolvería el endpoint Navifault.

    La correlación conserva la identidad real de una unidad: vehículo histórico
    analytics → database_key + device_id + placa → vehículo del Portal. Luego
    aplica la misma llave de runtime de Navifault (dateplate verificado) y la
    semántica de protocolo J1939/J1708. Un FMI decimal no representa una llave
    válida; se deja con cero candidatas en vez de redondearlo.
    """
    key = NavifaultFaultProtocolKey
    page = NavifaultFaultPage
    dateplate_map = NavifaultDateplateManualMap
    fmi_as_integer = cast(fault.codigo_modo_de_falla, Integer)
    protocol_predicate = or_(
        and_(
            fault.nombre_fuente_diagnostico == "SourceJ1939Id",
            key.protocol == "J1939",
            key.namespace == "SPN",
        ),
        and_(
            fault.nombre_fuente_diagnostico == "SourceJ1708Id",
            key.protocol == "J1708",
            key.namespace.in_(("PID", "SID")),
        ),
    )
    fmi_predicate = or_(
        fault.codigo_modo_de_falla.is_(None),
        and_(
            fault.codigo_modo_de_falla == fmi_as_integer,
            key.fmi == fmi_as_integer,
        ),
    )
    return (
        select(func.count(func.distinct(page.fault_page_id)))
        .select_from(DimVehicle)
        .join(
            GeotabDatabase,
            func.lower(GeotabDatabase.database_key) == func.lower(DimVehicle.database_name),
        )
        .join(
            Vehicle,
            and_(
                Vehicle.geotab_database_id == GeotabDatabase.id,
                Vehicle.geotab_device_id == DimVehicle.device_id,
                Vehicle.plate == fault.movil,
                Vehicle.is_active.is_(True),
            ),
        )
        .join(
            dateplate_map,
            and_(
                dateplate_map.normalized_service_model_name
                == _normalized_service_model_name_expression(Vehicle.service_model_name),
                dateplate_map.mapping_status == "verified",
            ),
        )
        .join(page, page.pub_id == dateplate_map.pub_id)
        .join(key, key.fault_page_id == page.fault_page_id)
        .where(
            DimVehicle.vehicle_id == fault.vehicle_id,
            key.diagnostic_code == fault.codigo_diagnostico,
            protocol_predicate,
            fmi_predicate,
        )
        .correlate(FactFaultEvent)
        .scalar_subquery()
    )


def _apply_navifault_match_review_filter(stmt: Any, states: list[str] | None) -> Any:
    """Filtra por cardinalidad real de candidatas, antes de agrupar/paginar."""
    if not states:
        return stmt
    selected = set(states) & MATCH_REVIEW_VALUES
    if not selected:
        return stmt.where(sqlalchemy.false())

    candidate_count = _navifault_candidate_page_count(FactFaultEvent)
    predicates = []
    if MATCH_REVIEW_DIRECT in selected:
        predicates.append(candidate_count == 1)
    if MATCH_REVIEW_AMBIGUOUS in selected:
        predicates.append(candidate_count > 1)
    if MATCH_REVIEW_NO_MATCH in selected:
        predicates.append(candidate_count == 0)
    return stmt.where(or_(*predicates))


def _apply_fault_filters(
    stmt,
    *,
    vehicle_id: list[str] | None,
    date_from: date | None,
    date_to: date | None,
    severity: str | list[str] | None,
    only_urgent: bool,
    fault_dimension: str | None = None,
    fault_value: str | list[str] | None = None,
    allowed_vehicle_ids: Sequence[uuid.UUID] | None = None,
    match_review: list[str] | None = None,
    exclude_telematics: bool = False,
):
    """Filtros estándar de fallas.

    Aislamiento (P0): el control de acceso se aplica sobre `f.vehicle_id`,
    no sobre `f.database_name` (database_name puede ser compartido entre
    flotas y no es frontera de seguridad).
    """
    f = FactFaultEvent
    # La fuente genera diagnósticos marcadores como "Unknown" o "**Unknown".
    # No representan fallas utilizables en este reporte, así que se excluyen
    # aquí para que no afecten KPIs, distribuciones, tendencias ni detalle.
    normalized_diagnostic = func.lower(
        func.regexp_replace(func.coalesce(f.diagnostico, ""), "^[[:space:]*]+", "")
    )
    stmt = stmt.where(normalized_diagnostic.not_like("unknown%"))
    # Las fallas del DISPOSITIVO TELEMÁTICO las reporta el equipo Geotab sobre
    # sí mismo —reinicios internos, pérdida de energía, desconexión, problemas
    # de comunicación con el motor, cámara, sensor de llanta ausente—. No son
    # fallas del vehículo.
    #
    # El corte es OPT-IN y el default NO excluye nada. Estos endpoints los
    # consumen dos pantallas distintas: el tab de Fallas de reportes, que
    # existe para ver el histórico DEL VEHÍCULO y sí las pide fuera, y el
    # módulo Navifault, que trabaja sobre todo lo que llega. Excluirlas por
    # defecto le quitaba a Navifault el 15,4 % de su ventana de 24 h sin que
    # nadie lo pidiera, así que quien las quiere fuera lo dice.
    #
    # La llave es la FUENTE del diagnóstico y no el nombre del controlador:
    # `SourceGeotabGoId` es el identificador estable de Geotab, mientras que
    # "Telematics device" es un nombre para mostrar y puede cambiar o
    # traducirse. Medido sobre las 1.041.014 filas del hecho, las dos columnas
    # señalan EXACTAMENTE el mismo conjunto: 165.030 filas cada una y cero
    # discrepancias, así que elegir la estable no cuesta nada.
    #
    # `is_distinct_from` y no `!=`: con `!=`, una fila de fuente NULL daría
    # UNKNOWN y quedaría excluida también, y una fuente desconocida no es una
    # falla del dispositivo.
    if exclude_telematics:
        stmt = stmt.where(f.nombre_fuente_diagnostico.is_distinct_from(TELEMATICS_DEVICE_SOURCE))
    stmt, _empty = _apply_vehicle_scope(
        stmt,
        f.vehicle_id,
        allowed_vehicle_ids,
    )
    if _empty:
        return stmt
    if vehicle_id is not None:
        stmt = stmt.where(f.vehicle_id.in_(vehicle_id))
    if date_from is not None:
        stmt = stmt.where(f.date_key >= _date_to_key(date_from))
    if date_to is not None:
        stmt = stmt.where(f.date_key <= _date_to_key(date_to))
    if severity is not None:
        if isinstance(severity, (list, tuple, set)):
            if severity:
                conds = []
                for s in severity:
                    if s == "Sin clasificar":
                        conds.append(func.coalesce(f.tipo_de_atencion, "") == "")
                    else:
                        conds.append(f.tipo_de_atencion == s)
                if conds:
                    stmt = stmt.where(or_(*conds))
        elif severity == "Sin clasificar":
            stmt = stmt.where(func.coalesce(f.tipo_de_atencion, "") == "")
        else:
            stmt = stmt.where(f.tipo_de_atencion == severity)
    if fault_dimension is not None and fault_value is not None:
        dimension_column = FAULT_DIMENSIONS.get(fault_dimension)
        if dimension_column is not None:
            if isinstance(fault_value, (list, tuple, set)):
                if fault_value:
                    stmt = stmt.where(dimension_column.in_(fault_value))
            elif fault_value == "—":
                stmt = stmt.where(func.coalesce(dimension_column, "") == "")
            else:
                stmt = stmt.where(dimension_column == fault_value)
    if only_urgent:
        stmt = stmt.where(f.luz_de_parada_roja.is_(True))
    return _apply_navifault_match_review_filter(stmt, match_review)


def _unique_fault_key_expression(f: Any = FactFaultEvent) -> Any:
    """Llave que identifica UNA falla entre sus repeticiones (eventos).

    Única fuente de la llave distinct: la consumen el summary de fallas y la
    agregación por grupo, y "fallas únicas" tiene que significar lo mismo en
    los dos o los buckets no reconcilian con los KPIs.
    """
    return func.concat(
        f.vehicle_id,
        "|",
        func.coalesce(cast(f.codigo_diagnostico, String), ""),
        "|",
        func.coalesce(cast(f.codigo_modo_de_falla, String), ""),
        "|",
        func.coalesce(cast(f.codigo_controlador, String), ""),
        "|",
        func.coalesce(f.nombre_fuente_diagnostico, ""),
        "|",
        func.coalesce(f.diagnostico, ""),
        "|",
        func.coalesce(f.nombre_de_controlador, ""),
        "|",
        func.coalesce(f.modo_de_falla, ""),
    )


async def get_fault_summary(
    db: AsyncSession,
    *,
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fleet_ids: list[uuid.UUID] | None = None,
    exclude_telematics: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    severity: str | list[str] | None = None,
    fault_dimension: str | None = None,
    fault_value: str | list[str] | None = None,
    match_review: list[str] | None = None,
) -> dict[str, Any]:
    allowed = fleet_ids
    if allowed == []:
        return {"n_fallas": 0, "n_vehiculos": 0, "n_diagnosticos": 0, "n_urgentes": 0}
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return {"n_fallas": 0, "n_vehiculos": 0, "n_diagnosticos": 0, "n_urgentes": 0}
    f = FactFaultEvent
    unique_fault_key = _unique_fault_key_expression(f)
    stmt = select(
        func.count(func.distinct(unique_fault_key)),
        func.count(),
        func.count(func.distinct(f.vehicle_id)),
        func.count(func.distinct(f.diagnostico)),
        func.count(
            func.distinct(
                case(
                    (f.luz_de_parada_roja.is_(True), unique_fault_key),
                    else_=None,
                )
            )
        ),
    )
    stmt = _apply_fault_filters(
        stmt,
        vehicle_id=vehicle_id,
        date_from=date_from,
        date_to=date_to,
        severity=severity,
        only_urgent=False,
        fault_dimension=fault_dimension,
        fault_value=fault_value,
        allowed_vehicle_ids=allowed,
        exclude_telematics=exclude_telematics,
        match_review=match_review,
    )
    n_fallas, n_eventos, n_vehiculos, n_diagnosticos, n_urgentes = (await db.execute(stmt)).one()
    return {
        "n_fallas": n_fallas,
        "n_eventos": n_eventos,
        "n_vehiculos": n_vehiculos,
        "n_diagnosticos": n_diagnosticos,
        "n_urgentes": n_urgentes,
    }


async def get_fault_by_severity(
    db: AsyncSession,
    *,
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fleet_ids: list[uuid.UUID] | None = None,
    exclude_telematics: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    fault_dimension: str | None = None,
    fault_value: str | list[str] | None = None,
    match_review: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Conteo por nivel de atención (Nivel 1 - Urgente, etc.)."""
    allowed = fleet_ids
    if allowed == []:
        return []
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return []
    f = FactFaultEvent
    stmt = select(f.tipo_de_atencion, func.count())
    stmt = _apply_fault_filters(
        stmt,
        vehicle_id=vehicle_id,
        date_from=date_from,
        date_to=date_to,
        severity=None,
        only_urgent=False,
        fault_dimension=fault_dimension,
        fault_value=fault_value,
        allowed_vehicle_ids=allowed,
        exclude_telematics=exclude_telematics,
        match_review=match_review,
    )
    stmt = stmt.group_by(f.tipo_de_atencion).order_by(func.count().desc())
    result = await db.execute(stmt)
    return [{"severity": sev or "Sin clasificar", "n_fallas": n} for sev, n in result.all()]


async def get_fault_pareto(
    db: AsyncSession,
    *,
    dimension: str = "diagnostico",
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fleet_ids: list[uuid.UUID] | None = None,
    exclude_telematics: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    severity: str | list[str] | None = None,
    match_review: list[str] | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Top N por diagnóstico / controlador / modo de falla."""
    allowed = fleet_ids
    if allowed == []:
        return []
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return []
    col = FAULT_DIMENSIONS.get(dimension, FactFaultEvent.diagnostico)
    stmt = select(col, func.count())
    stmt = _apply_fault_filters(
        stmt,
        vehicle_id=vehicle_id,
        date_from=date_from,
        date_to=date_to,
        severity=severity,
        only_urgent=False,
        allowed_vehicle_ids=allowed,
        exclude_telematics=exclude_telematics,
        match_review=match_review,
    )
    stmt = stmt.group_by(col).order_by(func.count().desc()).limit(limit)
    result = await db.execute(stmt)
    return [{"label": label or "—", "n_fallas": n} for label, n in result.all()]


async def get_fault_timeseries_daily(
    db: AsyncSession,
    *,
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fleet_ids: list[uuid.UUID] | None = None,
    exclude_telematics: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    severity: str | list[str] | None = None,
    fault_dimension: str | None = None,
    fault_value: str | list[str] | None = None,
    match_review: list[str] | None = None,
) -> list[dict[str, Any]]:
    allowed = fleet_ids
    if allowed == []:
        return []
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return []
    f = FactFaultEvent
    stmt = select(f.date_key, func.max(f.fecha), func.count())
    stmt = _apply_fault_filters(
        stmt,
        vehicle_id=vehicle_id,
        date_from=date_from,
        date_to=date_to,
        severity=severity,
        only_urgent=False,
        fault_dimension=fault_dimension,
        fault_value=fault_value,
        allowed_vehicle_ids=allowed,
        exclude_telematics=exclude_telematics,
        match_review=match_review,
    )
    stmt = stmt.group_by(f.date_key).order_by(f.date_key)
    result = await db.execute(stmt)
    return [
        {
            "periodo": date_key,
            "label": fecha.isoformat() if fecha else str(date_key),
            "n_fallas": n,
        }
        for date_key, fecha, n in result.all()
    ]


async def get_fault_timeseries_monthly(
    db: AsyncSession,
    *,
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fleet_ids: list[uuid.UUID] | None = None,
    exclude_telematics: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    severity: str | list[str] | None = None,
    fault_dimension: str | None = None,
    fault_value: str | list[str] | None = None,
    match_review: list[str] | None = None,
) -> list[dict[str, Any]]:
    allowed = fleet_ids
    if allowed == []:
        return []
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return []
    f = FactFaultEvent
    month_key = cast(f.date_key / 100, Integer)
    stmt = select(month_key.label("month_key"), func.count())
    stmt = _apply_fault_filters(
        stmt,
        vehicle_id=vehicle_id,
        date_from=date_from,
        date_to=date_to,
        severity=severity,
        only_urgent=False,
        fault_dimension=fault_dimension,
        fault_value=fault_value,
        allowed_vehicle_ids=allowed,
        exclude_telematics=exclude_telematics,
        match_review=match_review,
    )
    stmt = stmt.group_by(month_key).order_by(month_key)
    result = await db.execute(stmt)
    return [
        {
            "periodo": mk,
            "label": _month_label(mk),
            "n_fallas": n,
        }
        for mk, n in result.all()
    ]


async def get_fault_ranking(
    db: AsyncSession,
    *,
    vehicle_id: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    severity: str | list[str] | None = None,
    fault_dimension: str | None = None,
    fault_value: str | list[str] | None = None,
    motor_type: list[str] | None = None,
    fleet_ids: list[uuid.UUID] | None = None,
    exclude_telematics: bool = False,
    match_review: list[str] | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    allowed = fleet_ids
    if allowed == []:
        return []
    if vehicle_id == []:
        return []
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return []
    f = FactFaultEvent
    stmt = select(
        f.vehicle_id,
        func.max(DimVehicle.vehicle_label),
        func.max(f.movil),
        func.count().label("value"),
    ).join(DimVehicle, DimVehicle.vehicle_id == f.vehicle_id, isouter=True)
    stmt = _apply_fault_filters(
        stmt,
        vehicle_id=vehicle_id,
        date_from=date_from,
        date_to=date_to,
        severity=severity,
        only_urgent=False,
        fault_dimension=fault_dimension,
        fault_value=fault_value,
        allowed_vehicle_ids=allowed,
        exclude_telematics=exclude_telematics,
        match_review=match_review,
    )
    stmt = stmt.group_by(f.vehicle_id).order_by(func.count().desc()).limit(limit)
    result = await db.execute(stmt)
    return [
        {
            "vehicle_id": vehicle_id,
            "vehicle_label": label,
            "placa": movil,
            "value": float(val) if val is not None else None,
        }
        for vehicle_id, label, movil, val in result.all()
    ]


async def list_fault_events(
    db: AsyncSession,
    *,
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fleet_ids: list[uuid.UUID] | None = None,
    exclude_telematics: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    severity: str | list[str] | None = None,
    fault_dimension: str | None = None,
    fault_value: str | list[str] | None = None,
    match_review: list[str] | None = None,
    management_state: str | None = None,
    only_urgent: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    allowed = fleet_ids
    if allowed == []:
        return [], 0
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return [], 0
    f = FactFaultEvent
    group_cols = (
        f.vehicle_id,
        f.movil,
        f.codigo_diagnostico,
        f.codigo_modo_de_falla,
        f.codigo_controlador,
        f.nombre_fuente_diagnostico,
        f.diagnostico,
        f.nombre_de_controlador,
        f.modo_de_falla,
    )
    base_count = select(f.vehicle_id).group_by(*group_cols)
    base_count = _apply_fault_filters(
        base_count,
        vehicle_id=vehicle_id,
        date_from=date_from,
        date_to=date_to,
        severity=severity,
        only_urgent=only_urgent,
        fault_dimension=fault_dimension,
        fault_value=fault_value,
        allowed_vehicle_ids=allowed,
        exclude_telematics=exclude_telematics,
        match_review=match_review,
    )

    is_urgent_case = case((f.luz_de_parada_roja.is_(True), 1), else_=0)
    is_amber_case = case((f.luz_de_parada_amber.is_(True), 1), else_=0)
    is_mil_case = case((f.lampara_de_averia.is_(True), 1), else_=0)

    stmt = select(
        func.max(f.row_id).label("row_id"),
        f.vehicle_id,
        f.movil,
        func.max(f.fecha).label("fecha"),
        func.max(f.fecha_de_falla).label("fecha_de_falla"),
        func.min(f.fecha_de_falla).label("primera_fecha_de_falla"),
        f.codigo_diagnostico,
        f.codigo_modo_de_falla,
        f.codigo_controlador,
        f.nombre_fuente_diagnostico,
        f.diagnostico,
        f.nombre_de_controlador,
        f.modo_de_falla,
        func.max(f.estado_de_falla).label("estado_de_falla"),
        func.count().label("recuento_de_fallos"),
        func.max(f.tipo_de_atencion).label("tipo_de_atencion"),
        func.max(is_urgent_case).label("luz_de_parada_roja"),
        func.max(is_amber_case).label("luz_de_parada_amber"),
        func.max(is_mil_case).label("lampara_de_averia"),
    ).group_by(*group_cols)
    stmt = _apply_fault_filters(
        stmt,
        vehicle_id=vehicle_id,
        date_from=date_from,
        date_to=date_to,
        severity=severity,
        only_urgent=only_urgent,
        fault_dimension=fault_dimension,
        fault_value=fault_value,
        allowed_vehicle_ids=allowed,
        exclude_telematics=exclude_telematics,
        match_review=match_review,
    )

    if management_state in (
        "pending",
        "escalada",
        "pendiente_registro",
        "repeated",
        "managed",
    ):
        managed_case = NavifaultManagedFaultCase

        def apply_management_filter(query: Select) -> Select:
            q = (
                query.join(DimVehicle, DimVehicle.vehicle_id == f.vehicle_id)
                .join(
                    GeotabDatabase,
                    func.lower(GeotabDatabase.database_key) == func.lower(DimVehicle.database_name),
                )
                .join(
                    Vehicle,
                    and_(
                        Vehicle.geotab_database_id == GeotabDatabase.id,
                        Vehicle.geotab_device_id == DimVehicle.device_id,
                        Vehicle.plate == f.movil,
                        Vehicle.is_active.is_(True),
                    ),
                )
                .outerjoin(
                    managed_case,
                    and_(*_signature_join_conditions(f, managed_case, Vehicle)),
                )
            )
            first_reapp = _first_reappearance_expression(f, managed_case)
            state_expr = _management_state_expression(managed_case, first_reapp)
            return q.where(state_expr == management_state)

        base_count = apply_management_filter(base_count)
        stmt = apply_management_filter(stmt)

    stmt = (
        _add_window_total(stmt)
        .order_by(
            func.max(f.fecha_de_falla).desc().nullslast(),
        )
        .limit(limit)
        .offset(offset)
    )
    result = (await db.execute(stmt)).all()
    if result:
        total = int(getattr(result[0], _WINDOW_TOTAL_LABEL))
    elif offset:
        fallback = select(func.count()).select_from(base_count.subquery())
        total = (await db.execute(fallback)).scalar_one()
    else:
        total = 0

    rows = [
        {
            "row_id": r.row_id,
            "vehicle_id": r.vehicle_id,
            "movil": r.movil,
            "fecha": r.fecha,
            "fecha_de_falla": r.fecha_de_falla,
            "primera_fecha_de_falla": r.primera_fecha_de_falla,
            "codigo_diagnostico": r.codigo_diagnostico,
            "codigo_modo_de_falla": r.codigo_modo_de_falla,
            "codigo_controlador": r.codigo_controlador,
            "nombre_fuente_diagnostico": r.nombre_fuente_diagnostico,
            "diagnostico": r.diagnostico,
            "nombre_de_controlador": r.nombre_de_controlador,
            "modo_de_falla": r.modo_de_falla,
            "estado_de_falla": r.estado_de_falla,
            "recuento_de_fallos": r.recuento_de_fallos,
            "tipo_de_atencion": r.tipo_de_atencion,
            "luz_de_parada_roja": bool(r.luz_de_parada_roja),
            "luz_de_parada_amber": bool(r.luz_de_parada_amber),
            "lampara_de_averia": bool(r.lampara_de_averia),
        }
        for r in result
    ]
    return rows, total


async def get_fault_timeline(
    db: AsyncSession,
    *,
    vehicle_id: str,
    codigo_diagnostico: int | None = None,
    codigo_modo_de_falla: float | None = None,
    diagnostico: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    fleet_ids: list[uuid.UUID] | None = None,
    exclude_telematics: bool = False,
    limit: int = 15,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    allowed = fleet_ids
    if allowed == []:
        return [], 0
    f = FactFaultEvent
    stmt = select(
        f.row_id,
        f.fecha_de_falla,
        f.estado_de_falla,
        f.recuento_de_fallos,
        f.tipo_de_atencion,
        f.luz_de_parada_roja,
        f.luz_de_parada_amber,
        f.lampara_de_averia,
    )
    count_stmt = select(func.count()).select_from(f)
    stmt = _apply_fault_filters(
        stmt,
        vehicle_id=[vehicle_id],
        date_from=date_from,
        date_to=date_to,
        severity=None,
        only_urgent=False,
        allowed_vehicle_ids=allowed,
        exclude_telematics=exclude_telematics,
    )
    count_stmt = _apply_fault_filters(
        count_stmt,
        vehicle_id=[vehicle_id],
        date_from=date_from,
        date_to=date_to,
        severity=None,
        only_urgent=False,
        allowed_vehicle_ids=allowed,
        exclude_telematics=exclude_telematics,
    )
    if codigo_diagnostico is not None:
        stmt = stmt.where(f.codigo_diagnostico == codigo_diagnostico)
        count_stmt = count_stmt.where(f.codigo_diagnostico == codigo_diagnostico)
    elif diagnostico is not None:
        stmt = stmt.where(f.diagnostico == diagnostico)
        count_stmt = count_stmt.where(f.diagnostico == diagnostico)
    if codigo_modo_de_falla is not None:
        stmt = stmt.where(f.codigo_modo_de_falla == codigo_modo_de_falla)
        count_stmt = count_stmt.where(f.codigo_modo_de_falla == codigo_modo_de_falla)

    stmt = (
        _add_window_total(stmt)
        .order_by(f.fecha_de_falla.desc().nullslast())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).all()
    if rows:
        total = int(getattr(rows[0], _WINDOW_TOTAL_LABEL))
    elif offset:
        total = (await db.execute(count_stmt)).scalar_one()
    else:
        total = 0
    items = [
        {
            "row_id": r[0],
            "fecha_de_falla": r[1],
            "estado_de_falla": r[2],
            "recuento_de_fallos": r[3],
            "tipo_de_atencion": r[4],
            "luz_de_parada_roja": r[5],
            "luz_de_parada_amber": r[6],
            "lampara_de_averia": r[7],
        }
        for r in rows
    ]
    return items, total


# ---------------------------------------------------------------------------
# Calificación (score derivado de hábitos operativos + seguros)
# ---------------------------------------------------------------------------
#
# NOTA: fórmula PROPUESTA y AJUSTABLE. No replica ningún DAX/PowerBI existente;
# es una heurística documentada para que negocio pueda recalibrar SIN tocar
# código y sin cambiar la lógica de agregación.
#
# Desde 2026-08-27 los parámetros ajustables NO son constantes de módulo: viven
# en `CalificacionConfig` (`app/services/calificacion_config.py`) y se resuelven
# por flota en `calificacion_config_service`. `get_calificacion` los resuelve
# UNA vez y los pasa hacia abajo; ninguna función de este bloque puede leer un
# umbral de otro sitio. Lo que sigue siendo constante acá es lo que NO es
# calibración: qué bandas de RPM cuentan como eficientes (`_EFIC_BAND`), la
# exclusión de los excesos de RPM del QHS (`_QHS_EXCLUDED_EVENT_TYPES`) y el
# override por sobrevelocidad (`_QGEN_PENALIZADO`/`_ESTADO_PENALIZADO`), que es
# una regla de integridad mecánica y no una perilla.
#
#   Q.H.Operación (QHO): premia tiempo en rango eficiente, penaliza ralentí y
#     la frecuencia de eventos RPM por 1000 km (comercial) o 100 horas ECM
#     (vocacional). Los eventos sobre 2100 RPM cuentan doble.
#   Q.H.Seguros (QHS): penaliza eventos de seguridad ponderados por severidad,
#     por cada 1000 km. Sin distancia registrada no es evaluable (None).
#   Q General (QGen): promedio ponderado de QHS y QHO (si falta uno, usa el otro).
#     Un solo exceso sobre la sobrevelocidad máxima del motor lo deja en cero
#     por override, no por promedio (ver `_QGEN_PENALIZADO`).
#   Estado: clasifica QGen en No cumple / Cumple / Excede por umbrales.
#
# La cifra de flota SIEMPRE agrega puntajes por vehículo, nunca eventos crudos.
# QHS se pondera por km; QHO y QGen por bloques de exposición equivalentes
# (1000 km comerciales o 100 h vocacionales). Sumar eventos antes
# de aplicar el tope hacía que un solo vehículo con 44 ev/1000 km mandara a cero
# el QHS del mes entero, aunque otro estuviera en 59.6. Ponderar por km evita el
# extremo opuesto: que un vehículo con 200 km pese igual que uno con 4000.
# Meta de rango eficiente y ventana de ralentí: `config.efic_target`,
# `config.ralenti_target` y `config.ralenti_max`. Los defaults (0.70 / 0.10 /
# 0.30) son los que vivían acá y están en `DEFAULT_CALIFICACION_CONFIG`.

# Exceso RPM: se mide como frecuencia, no como porcentaje de tiempo. Cada evento
# cuenta una vez y los que superan la velocidad gobernada del motor DE ESE
# vehículo cuentan doble. La exposición depende del uso: 1000 km para comerciales
# y 100 horas ECM para vocacionales.
#
# El umbral fijo `_RPM_HIGH_THRESHOLD = 2100.0` se retiró el 2026-08-27: 2100 es
# la gobernada del ISG12 y del X13E6 y de ningún otro motor de la flota, así que
# como constante global sobreestimaba el exceso en el X11 (1925) y lo
# subestimaba en el ISD6.7 (2850). Ahora sale de
# `public.motor_catalog.governed_speed_rpm` vía `_motor_rpm_limit_subquery`.
# La agravación y los dos topes de exposición son calibración:
# `config.rpm_high_weight` (2.0), `config.rpm_cap_comercial_1000km` (150) y
# `config.rpm_cap_vocacional_100h` (1400), vía `config.rpm_cap(vocacional=...)`.

# Sobrevelocidad: UN solo evento por encima de `motor_catalog.max_overspeed_rpm`
# del motor del vehículo tumba su calificación. No es otra agravación dentro del
# promedio —un x3 se diluiría a nada en un vehículo con miles de km— sino un
# override explícito: el QGen efectivo pasa a `_QGEN_PENALIZADO` y el estado a
# `_ESTADO_PENALIZADO`, mientras `qgen_base` conserva el puntaje que habría
# tenido y `qhs`/`qho` quedan intactos. Nada de información se destruye: la
# penalización es visible y reversible en la lectura.
#
# El QGen efectivo es el que alimenta TODO lo que consume el puntaje: el donut
# de estado, el `promedio_general` ponderado, el orden de la tabla y la serie
# mensual. Dejar el promedio con `qgen_base` haría que la penalización no
# "tumbara" nada a nivel flota, que es justo lo que se pide.
#
# MISMA ASIMETRÍA FAIL-OPEN QUE LA AGRAVACIÓN x2 — no "unificar" esto después:
# un motor sin `max_overspeed_rpm` capturada NO puede disparar la penalización.
# La comparación contra NULL da NULL, el WHEN no se cumple y el evento no
# penaliza. Es el comportamiento correcto: no se castiga a un vehículo por un
# hueco del maestro, igual que no se lo premia quitándole eventos. El check de
# la tabla garantiza `max_overspeed_rpm >= governed_speed_rpm` cuando existen
# ambos, así que todo evento que penaliza también agrava, nunca al revés. Al
# 2026-08-27, 10 de 14 motores tienen el par completo y A26, S13 y L9 370
# (25 vehículos) no tienen ninguno de los dos.
_QGEN_PENALIZADO = 0.0
_ESTADO_PENALIZADO = "No cumple"

# Tope calibrado sobre la distribución real por vehículo-mes (sin RPM y con los
# pesos de abajo): mediana 1.57, p90 10.57, p95 16.85 ev/1000 km. Con 15, la
# mediana de la flota puntúa ~89.5 —el umbral de "Cumple"— y solo el decil peor
# cae a cero. El valor anterior (20) venía de contar los excesos de RPM.
# Hoy es `config.eventos_cap`, con 15.0 por defecto.

# Severidad relativa de cada evento de seguridad. Los excesos de RPM NO entran:
# ya pesan 20% dentro del QHO y suponían el 99.4% de los eventos registrados
# (78543 de 79019), así que el QHS medía RPM otra vez en vez de conducción
# segura. Los baches dependen del estado de la vía más que del conductor, y la
# aceleración brusca castiga sobre todo consumo, que el QHO ya recoge.
#
# La EXCLUSIÓN sí es constante: es una decisión de diseño sobre qué mide el QHS,
# no una calibración de cuánto pesa cada evento. La severidad relativa de cada
# tipo es `config.qhs_event_weights` / `config.qhs_default_weight`, y los pesos
# de los componentes son `config.peso_eficiente`, `config.peso_ralenti`,
# `config.peso_exceso_rpm`, `config.peso_qhs` y `config.peso_qho`.
_QHS_EXCLUDED_EVENT_TYPES = frozenset({"excesos de rpm"})

# Nombre de cada componente del QHO. Se declara una vez porque lo usan la
# metodología publicada y el detalle por vehículo: si el front los cruza por
# nombre (y lo hace, para casar cada fila con su explicación) no pueden diferir.
_NOMBRE_EFIC = "Tiempo en rango eficiente"
_NOMBRE_RALENTI = "Ralentí"
_NOMBRE_EXCESO = "Excesos de RPM"

# Umbrales de estado: rojo → amarillo → verde, y verde es CUMPLIR.
# Los anteriores (90/97) dejaban a los 16 vehículos en rojo —el QGen real va de
# 40.9 a 75.1—, así que el donut mostraba una sola barra y no servía para
# priorizar. No se bajan hasta la mediana, que sería circular: se anclan a lo
# que significa cada nivel.
#   70 = QHS sano (~98) + QHO ~42, es decir ralentí <35% y eficiente >25%.
#        Debajo de eso la operación es deficiente, no solo mejorable.
#   85 = QHO ~72: ralentí <20% y eficiente >40%, el perfil de la mejor operación
#        observada. Cumplir tiene que costar; hoy no lo alcanza nadie.
# Son `config.umbral_en_riesgo` (70) y `config.umbral_cumple` (85): QGen por
# debajo del primero es rojo, por encima del segundo es verde, en medio amarillo.

# Bandas de RPM que cuentan como "rango eficiente" para el QHO. Las bandas
# económica y balanceada son mutuamente excluyentes, por lo que el porcentaje
# eficiente es la suma de ambas (ver reportes-bandas-rpm).
_EFIC_BAND = func.coalesce(FactCombustibleDaily.pct_rango_economico, 0.0) + func.coalesce(
    FactCombustibleDaily.pct_rango_balanceado, 0.0
)


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _exposicion_base(vocacional: bool) -> str:
    """Unidad de exposición del tipo de uso, en texto ('1000 km' | '100 horas ECM')."""
    return "100 horas ECM" if vocacional else "1000 km"


# ---------------------------------------------------------------------------
# Exposición mínima: cuándo un vehículo NO se puede calificar
# ---------------------------------------------------------------------------
#
# QHS y QHO son TASAS. Con un denominador minúsculo dejan de describir cómo se
# conduce y pasan a describir que el vehículo no se movió: es imposible registrar
# un evento de seguridad en 200 metros, así que el QHS de un vehículo con 0,2 km
# sale 100 por construcción. Ese puntaje no es bueno, es indefinido.
#
# El caso que motivó la regla (Cementos San Marcos, julio de 2026): tres de
# cuatro vehículos penalizados por sobrevelocidad con QGen 0 y el cuarto con
# 93,5 sobre 0,2 km recorridos. El promedio de la flota daba 0,003 y parecía
# ignorar al vehículo sano; en realidad lo estaba ponderando por su exposición,
# que era el 0,003 % de la flota. Las dos lecturas eran confusas y la segunda
# —el 93,5— además no era una medición.
#
# Un vehículo por debajo del mínimo queda con `qgen = None`: sale del promedio y
# del donut, y `qgen_base` conserva lo que habría puntuado. NO se le pone 0: no
# operar no es operar mal, y un 0 lo mezclaría con los penalizados.
#
# El mínimo es POR DÍA del periodo consultado y no un absoluto: la pantalla pide
# tres meses por defecto, un mes al hacer clic en la serie mensual y lo que el
# usuario elija en el filtro. `config.exposicion_minima_dia(vocacional=...)`
# devuelve km/día o h ECM/día según el tipo de uso, y 0 desactiva la regla.


def _dias_en_rango(
    inicio: date,
    fin: date,
    date_from: date | None,
    date_to: date | None,
) -> int:
    """Días de [inicio, fin] que caen dentro del rango consultado, inclusive.

    El recorte importa: el rango por defecto de la pestaña son "tres meses
    atrás → hoy", así que el primer y el último mes de la serie casi siempre
    están cubiertos a medias. Exigirle a un mes de un día la exposición de un
    mes entero dejaría sin calificar a un vehículo que sí operó.
    """
    desde = max(inicio, date_from) if date_from else inicio
    hasta = min(fin, date_to) if date_to else fin
    return max((hasta - desde).days + 1, 0)


def _exposicion_minima(
    *, vocacional: bool, dias: int | None, config: CalificacionConfig
) -> float | None:
    """Exposición mínima del periodo, o None si la regla no aplica.

    Devuelve None —regla apagada— en dos casos distintos que conviene no
    confundir: la flota la desactivó poniendo 0, o el periodo no tiene días
    conocidos. Sin ambos extremos de fecha no hay forma de decir cuánto es
    "poco", y una regla que no se puede enunciar no se aplica.
    """
    if dias is None or dias <= 0:
        return None
    por_dia = config.exposicion_minima_dia(vocacional=vocacional)
    if por_dia <= 0:
        return None
    return por_dia * dias


def _exposicion_suficiente(
    *,
    km: float | None,
    horas: float | None,
    vocacional: bool,
    minimo: float | None,
) -> bool:
    """¿Operó lo bastante como para que su puntaje signifique algo?

    Fail-open: sin mínimo aplicable, todo vehículo se califica. La regla existe
    para evitar publicar un puntaje indefinido, no para esconder vehículos.
    """
    if minimo is None:
        return True
    valor = horas if vocacional else km
    return bool(valor) and valor >= minimo


def _rpm_cap(vocacional: bool, *, config: CalificacionConfig) -> float:
    """Tope de eventos RPM ponderados por bloque de exposición."""
    return config.rpm_cap(vocacional=vocacional)


def _score_rpm(
    eventos_ponderados: float,
    km: float | None,
    horas: float | None,
    vocacional: bool,
    *,
    config: CalificacionConfig,
) -> float | None:
    """Componente RPM según la exposición propia del tipo de operación."""
    exposicion = _rpm_exposure_units(
        km=km,
        horas=horas,
        vocacional=vocacional,
    )
    if exposicion is None:
        return None
    tasa = eventos_ponderados / exposicion
    return 100.0 * _clamp01(1.0 - tasa / _rpm_cap(vocacional, config=config))


def _rpm_exposure_units(
    *,
    km: float | None,
    horas: float | None,
    vocacional: bool,
) -> float | None:
    """Bloques comparables: 1000 km comerciales o 100 h vocacionales."""
    valor = horas if vocacional else km
    divisor = 100.0 if vocacional else 1000.0
    if not valor or valor <= 0:
        return None
    return valor / divisor


def _weighted_rpm_events(
    total: int, sobre_umbral: int, *, config: CalificacionConfig
) -> float:
    """Conteo con agravación: cada evento alto reemplaza x1 por su peso final."""
    return float(total) + float(sobre_umbral) * (config.rpm_high_weight - 1.0)


def _qho_component_scores(
    pct_efic: float | None,
    pct_ralenti: float | None,
    eventos_rpm_ponderados: float,
    km: float | None,
    horas: float | None,
    vocacional: bool,
    *,
    config: CalificacionConfig,
) -> list[tuple[str, float, float | None]]:
    """(nombre, peso, puntos 0..100 | None) de cada componente del QHO.

    ÚNICA fuente de los tres sumandos: `_score_qho` los promedia y el detalle
    por vehículo los publica tal cual. Separarlos era la forma de que el
    desglose que ve el cliente no pudiera desviarse del número que califica.
    `None` significa no evaluable (sin exposición), no cero.
    """
    efic = 100.0 * _clamp01((pct_efic or 0.0) / config.efic_target)
    ralenti = 100.0 * _clamp01(
        (config.ralenti_max - (pct_ralenti or 0.0))
        / (config.ralenti_max - config.ralenti_target)
    )
    rpm = _score_rpm(eventos_rpm_ponderados, km, horas, vocacional, config=config)
    return [
        (_NOMBRE_EFIC, config.peso_eficiente, efic),
        (_NOMBRE_RALENTI, config.peso_ralenti, ralenti),
        (_NOMBRE_EXCESO, config.peso_exceso_rpm, rpm),
    ]


def _score_qho(
    pct_efic: float | None,
    pct_ralenti: float | None,
    eventos_rpm_ponderados: float,
    km: float | None,
    horas: float | None,
    vocacional: bool,
    *,
    config: CalificacionConfig,
) -> float:
    """QHO 0..100: rango eficiente, ralentí y frecuencia de excesos RPM."""
    componentes = [
        (peso, puntos)
        for _, peso, puntos in _qho_component_scores(
            pct_efic,
            pct_ralenti,
            eventos_rpm_ponderados,
            km,
            horas,
            vocacional,
            config=config,
        )
        if puntos is not None
    ]
    peso = sum(p for p, _ in componentes)
    return sum(p * valor for p, valor in componentes) / peso


def _qhs_event_weight(event_type: str | None, *, config: CalificacionConfig) -> float:
    """Severidad del evento; 0 si no debe puntuar en seguridad.

    La exclusión de los excesos de RPM no es calibrable y por eso se decide acá
    y no en `config.event_weight`: un cliente no puede volver a meter RPM en el
    QHS, porque el QHO ya los mide y dominarían la señal de conducción segura.
    """
    if _is_rpm_event(event_type):
        return 0.0
    return config.event_weight(event_type)


def _is_rpm_event(event_type: str | None) -> bool:
    return "rpm" in (event_type or "").strip().lower()


def _score_qhs(
    peso_eventos: float, km: float | None, *, config: CalificacionConfig
) -> float | None:
    """QHS 0..100 sobre eventos ya ponderados; None si no hay km que normalicen."""
    if not km or km <= 0:
        return None
    per_1000 = peso_eventos / (km / 1000.0)
    return 100.0 * _clamp01(1.0 - per_1000 / config.eventos_cap)


def _promedio_ponderado(
    pares: list[tuple[float, float]], *, ponderado: bool = True
) -> float | None:
    """Promedio de (valor, peso) ignorando pesos nulos; None si no queda nada.

    Cae a promedio simple cuando todos los pesos son cero, para no descartar
    vehículos que puntuaron pero no registraron distancia.

    `ponderado=False` es la elección explícita de la flota
    (`config.promedio_ponderado`): un vehículo un voto. Se resuelve aquí y no en
    cada llamador para que la cifra del gauge y la de la serie mensual no puedan
    quedar calculadas con criterios distintos.

    Los porcentajes de operación (`operativos`) NO pasan por esta bandera a
    propósito: son tiempo sobre tiempo, y su peso natural es el tiempo que los
    produjo, no una decisión de presentación.
    """
    validos = [(v, w) for v, w in pares if v is not None]
    if not validos:
        return None
    if not ponderado:
        return sum(v for v, _ in validos) / len(validos)
    total_peso = sum(w for _, w in validos)
    if total_peso <= 0:
        return sum(v for v, _ in validos) / len(validos)
    return sum(v * w for v, w in validos) / total_peso


def _score_qgen(
    qhs: float | None, qho: float | None, *, config: CalificacionConfig
) -> float | None:
    if qhs is None and qho is None:
        return None
    if qhs is None:
        return qho
    if qho is None:
        return qhs
    return config.peso_qhs * qhs + config.peso_qho * qho


def _estado_calificacion(
    qgen: float | None, *, config: CalificacionConfig
) -> str | None:
    if qgen is None:
        return None
    if qgen < config.umbral_en_riesgo:
        return "No cumple"
    if qgen >= config.umbral_cumple:
        return "Cumple"
    return "En riesgo"


def _umbrales_calificacion(config: CalificacionConfig) -> dict[str, float]:
    """Cortes que el front necesita para pintar el gauge con la misma regla."""
    return {"en_riesgo": config.umbral_en_riesgo, "cumple": config.umbral_cumple}


def _pct(valor: float) -> str:
    """0.15 -> '15%' sin decimales sobrantes."""
    return f"{valor * 100:g}%"


def _es_corto(valor: float) -> str:
    """Número en es-CO sin decimales sobrantes: 5 -> '5', 0.5 -> '0,5'.

    `f"{valor:g}"` es lo que usa el resto de la metodología, pero emite el punto
    decimal del inglés: en un texto que el cliente lee, "0.5 horas" está mal
    escrito. No usa `_es`, que fija los decimales y produciría "5,00 km/día".
    """
    return f"{valor:g}".replace(".", ",")


def _es(valor: float, decimales: int = 1) -> str:
    """Número en formato es-CO: coma decimal y punto de miles (1234.5 -> '1.234,5')."""
    crudo = f"{valor:,.{decimales}f}"
    return crudo.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _objetivo_texto(
    nombre: str, vocacional: bool, *, config: CalificacionConfig
) -> str:
    """Meta publicada de un componente del QHO, derivada de su calibración.

    Lee el MISMO `config` que `_metodologia_calificacion()` y que el cálculo: un
    umbral escrito a mano en el detalle sería la forma más rápida de que la
    explicación del cliente y el puntaje se separen.
    """
    if nombre == _NOMBRE_EFIC:
        return f"{_pct(config.efic_target)} o más del tiempo → 100 puntos"
    if nombre == _NOMBRE_RALENTI:
        return (
            f"{_pct(config.ralenti_target)} o menos → 100 puntos; "
            f"{_pct(config.ralenti_max)} o más → 0"
        )
    if nombre == _NOMBRE_EXCESO:
        return (
            f"sin eventos → 100 puntos; {_rpm_cap(vocacional, config=config):g} eventos "
            f"ponderados por {_exposicion_base(vocacional)} → 0"
        )
    raise ValueError(f"componente QHO desconocido: {nombre!r}")


def _valor_texto(
    nombre: str,
    *,
    pct_efic: float | None,
    pct_ralenti: float | None,
    tasa_rpm: float | None,
    vocacional: bool,
) -> str:
    """Lo medido en ese componente, ya formateado en es-CO."""
    if nombre == _NOMBRE_EFIC:
        return f"{_es((pct_efic or 0.0) * 100)} % del tiempo"
    if nombre == _NOMBRE_RALENTI:
        return f"{_es((pct_ralenti or 0.0) * 100)} % de las horas de motor"
    if nombre == _NOMBRE_EXCESO:
        if tasa_rpm is None:
            return f"sin exposición registrada ({_exposicion_base(vocacional)})"
        return f"{_es(tasa_rpm)} eventos ponderados por {_exposicion_base(vocacional)}"
    raise ValueError(f"componente QHO desconocido: {nombre!r}")


def _motivo_texto(
    nombre: str,
    *,
    pct_efic: float | None,
    pct_ralenti: float | None,
    tasa_rpm: float | None,
    vocacional: bool,
    config: CalificacionConfig,
) -> str:
    """Frase legible que explica por qué ese componente no llegó a 100."""
    if nombre == _NOMBRE_EFIC:
        return (
            f"Solo {_es((pct_efic or 0.0) * 100)} % del tiempo en rango eficiente, "
            f"cuando el objetivo es {_pct(config.efic_target)}"
        )
    if nombre == _NOMBRE_RALENTI:
        return (
            f"Ralentí en {_es((pct_ralenti or 0.0) * 100)} %, cuando el objetivo "
            f"es {_pct(config.ralenti_target)} o menos"
        )
    if nombre == _NOMBRE_EXCESO:
        return (
            f"{_es(tasa_rpm or 0.0)} excesos de RPM ponderados por "
            f"{_exposicion_base(vocacional)}, cuando el tope es "
            f"{_rpm_cap(vocacional, config=config):g}"
        )
    raise ValueError(f"componente QHO desconocido: {nombre!r}")


def _motivo_penalizacion(eventos: int) -> str:
    plural = "excesos" if eventos != 1 else "exceso"
    return (
        f"{eventos} {plural} por encima de la sobrevelocidad máxima del motor: "
        f"el puntaje general queda en {_QGEN_PENALIZADO:g}"
    )


def _motivo_exposicion(
    km: float | None,
    horas: float | None,
    vocacional: bool,
    minimo: float,
    config: CalificacionConfig,
) -> str:
    """Por qué el vehículo no recibe puntaje, con el mínimo que le faltó.

    El número sale del `config` efectivo de la flota y no de un literal: si el
    cliente recalibra la exposición mínima, esta frase cambia con él.
    """
    if vocacional:
        return (
            f"No se califica: {_es(horas or 0.0)} horas ECM en el periodo, menos "
            f"del mínimo de {_es(minimo)} h "
            f"({_es(config.exposicion_minima_horas_dia, 2)} h/día). Con tan poca "
            "operación el puntaje mediría la falta de uso, no la conducción"
        )
    return (
        f"No se califica: {_es(km or 0.0)} km en el periodo, menos del mínimo de "
        f"{_es(minimo)} km ({_es(config.exposicion_minima_km_dia, 2)} km/día). Con "
        "tan poca operación el puntaje mediría la falta de uso, no la conducción"
    )


def _detalle_calificacion_vehiculo(
    *,
    pct_efic: float | None,
    pct_ralenti: float | None,
    km: float | None,
    horas: float | None,
    vocacional: bool,
    eventos_rpm_ponderados: float,
    eventos_por_tipo: dict[str, int],
    qhs: float | None,
    eventos_sobrevelocidad: int,
    exposicion_minima: float | None = None,
    exposicion_suficiente: bool = True,
    dias_periodo: int | None = None,
    config: CalificacionConfig,
) -> dict[str, Any]:
    """Desglose del puntaje de UN vehículo: componentes, eventos y motivos.

    Se construye con lo que la calificación ya tiene en memoria (`comb_veh` y
    `hab_veh`): cero consultas adicionales. Los umbrales y pesos salen del MISMO
    `config` que califica, nunca de un número escrito en el string.

    `motivos` va ordenado por puntos perdidos **a escala de QGen** —el peso del
    componente dentro del QHO multiplicado por `config.peso_qho`, y la pérdida
    del QHS por `config.peso_qhs`—, no por orden de aparición: así la primera
    línea es siempre la causa principal. Un componente que puntúa 100 no genera
    motivo; uno no
    evaluable genera su propia frase en vez de un 0 silencioso; y si no hay
    ningún motivo la lista va vacía, que es la forma de decir que el vehículo
    está bien.
    """
    exposicion = _rpm_exposure_units(km=km, horas=horas, vocacional=vocacional)
    tasa_rpm = None if exposicion is None else eventos_rpm_ponderados / exposicion

    componentes: list[dict[str, Any]] = []
    # (orden, texto): el orden es la pérdida a escala de QGen; la penalización
    # va primero con +inf y lo no evaluable al final con -1.
    motivos: list[tuple[float, str]] = []
    if eventos_sobrevelocidad > 0:
        motivos.append((math.inf, _motivo_penalizacion(eventos_sobrevelocidad)))
    # Mutuamente excluyente con la penalización por construcción (ver el bucle
    # de `get_calificacion`): un vehículo penalizado SÍ se califica, con 0, y no
    # se le atribuye la falta de exposición como motivo.
    elif not exposicion_suficiente and exposicion_minima is not None:
        motivos.append(
            (math.inf, _motivo_exposicion(km, horas, vocacional, exposicion_minima, config))
        )

    for nombre, peso, puntos in _qho_component_scores(
        pct_efic,
        pct_ralenti,
        eventos_rpm_ponderados,
        km,
        horas,
        vocacional,
        config=config,
    ):
        perdidos = None if puntos is None else peso * (100.0 - puntos)
        componentes.append(
            {
                "nombre": nombre,
                "valor_texto": _valor_texto(
                    nombre,
                    pct_efic=pct_efic,
                    pct_ralenti=pct_ralenti,
                    tasa_rpm=tasa_rpm,
                    vocacional=vocacional,
                ),
                "objetivo_texto": _objetivo_texto(nombre, vocacional, config=config),
                "puntos": puntos,
                "peso": peso,
                "aporte": None if puntos is None else puntos * peso,
                "puntos_perdidos": perdidos,
            }
        )
        if puntos is None:
            motivos.append(
                (
                    -1.0,
                    f"«{nombre}» no se puede evaluar: sin "
                    f"{_exposicion_base(vocacional)} registrados en el periodo",
                )
            )
        elif perdidos and perdidos > 0:
            motivos.append(
                (
                    perdidos * config.peso_qho,
                    _motivo_texto(
                        nombre,
                        pct_efic=pct_efic,
                        pct_ralenti=pct_ralenti,
                        tasa_rpm=tasa_rpm,
                        vocacional=vocacional,
                        config=config,
                    ),
                )
            )

    eventos_qhs = [
        {
            "event_type": tipo,
            "n_eventos": int(n),
            "peso": _qhs_event_weight(tipo, config=config),
            "aporte_ponderado": _qhs_event_weight(tipo, config=config) * int(n),
        }
        for tipo, n in sorted(
            eventos_por_tipo.items(),
            key=lambda kv: (-_qhs_event_weight(kv[0], config=config) * kv[1], kv[0]),
        )
        if _qhs_event_weight(tipo, config=config) > 0
    ]
    peso_qhs = sum(e["aporte_ponderado"] for e in eventos_qhs)
    por_1000 = None if not km or km <= 0 else peso_qhs / (km / 1000.0)

    if qhs is None:
        motivos.append(
            (
                -1.0,
                "La conducción segura no se puede evaluar: sin kilómetros "
                "registrados en el periodo",
            )
        )
    elif qhs < 100.0:
        motivos.append(
            (
                config.peso_qhs * (100.0 - qhs),
                f"{_es(por_1000 or 0.0)} eventos de seguridad por 1000 km, "
                f"cuando el tope es {config.eventos_cap:g}",
            )
        )

    motivos.sort(key=lambda m: -m[0])
    return {
        "componentes_qho": componentes,
        "eventos_qhs": eventos_qhs,
        "km": km,
        "horas_ecm": horas,
        "exposicion_unidades": exposicion,
        "exposicion_base": _exposicion_base(vocacional),
        "exposicion_minima": exposicion_minima,
        "exposicion_suficiente": exposicion_suficiente,
        "dias_periodo": dias_periodo,
        "eventos_qhs_por_1000km": por_1000,
        "eventos_qhs_tope": config.eventos_cap,
        "motivos": [texto for _, texto in motivos],
    }


# Siglas que `capitalize()` destrozaría ("excesos de rpm" -> "Excesos de rpm").
_SIGLAS_EVENTO = {"rpm": "RPM", "gps": "GPS", "ecm": "ECM"}


def _titulo_evento(clave: str) -> str:
    """Etiqueta legible del tipo de evento, respetando siglas."""
    palabras = [_SIGLAS_EVENTO.get(p, p) for p in clave.split()]
    if palabras and palabras[0] not in _SIGLAS_EVENTO.values():
        palabras[0] = palabras[0].capitalize()
    return " ".join(palabras)


def _metodologia_calificacion(config: CalificacionConfig) -> dict[str, Any]:
    """Explicación de la fórmula derivada del MISMO `config` que calcula.

    Escribirla a mano en el front fue justo lo que descuadró el gauge de los
    umbrales; aquí no puede desfasarse de la calibración vigente. Y desde que la
    calibración es por flota, tampoco puede desfasarse de la flota que se está
    mirando: si una flota sube el ralentí máximo a 40 %, este texto dice 40 %.
    """
    return {
        "peso_qho": config.peso_qho,
        "peso_qhs": config.peso_qhs,
        "componentes_qho": [
            {
                "nombre": _NOMBRE_EFIC,
                "peso": config.peso_eficiente,
                "detalle": (
                    f"0% del tiempo → 0 puntos; {_pct(config.efic_target)} o más → 100. "
                    "Es la suma de las bandas económica y balanceada de RPM."
                ),
            },
            {
                "nombre": _NOMBRE_RALENTI,
                "peso": config.peso_ralenti,
                "detalle": (
                    f"{_pct(config.ralenti_target)} o menos → 100 puntos; "
                    f"{_pct(config.ralenti_max)} o más → 0. Se mide sobre las horas de motor."
                ),
            },
            {
                "nombre": _NOMBRE_EXCESO,
                "peso": config.peso_exceso_rpm,
                "detalle": (
                    "Se usa el conteo según exposición: cada evento cuenta x1 y "
                    f"cuenta x{config.rpm_high_weight:g} si supera la velocidad "
                    "gobernada del motor de ese vehículo, no un umbral fijo "
                    "igual para toda la flota. Un motor cuya gobernada no está "
                    "capturada en Navi Vehículos no recibe la agravación: sus "
                    "eventos cuentan x1, pero ninguno se descarta del puntaje. "
                    f"En comerciales, {config.rpm_cap_comercial_1000km:g} eventos "
                    "ponderados por 1000 km → 0 puntos. En vocacionales se mide "
                    f"por 100 horas ECM y {config.rpm_cap_vocacional_100h:g} "
                    "eventos ponderados por 100 horas → 0. Sin eventos → 100."
                ),
            },
        ],
        "eventos_qhs": [
            {"evento": _titulo_evento(evento), "peso": peso}
            for evento, peso in sorted(
                config.qhs_event_weights.items(), key=lambda kv: (-kv[1], kv[0])
            )
        ],
        "eventos_excluidos": [_titulo_evento(e) for e in sorted(_QHS_EXCLUDED_EVENT_TYPES)],
        "eventos_tope_por_1000km": config.eventos_cap,
        "peso_evento_no_listado": config.qhs_default_weight,
        # El texto refleja el estado EFECTIVO de la flota, no la regla general:
        # el botón de información tiene que describir la calificación que el
        # usuario está viendo, no una que no se le aplica.
        "penalizacion_sobrevelocidad": (
            (
                "Un solo exceso por encima de la sobrevelocidad máxima del motor "
                f"deja el puntaje general en {_QGEN_PENALIZADO:g} y el estado en "
                f"«{_ESTADO_PENALIZADO}», sin importar cuánto sumaran los demás "
                "componentes: se conserva visible el puntaje que habría tenido. "
                "La sobrevelocidad máxima es la de Navi Vehículos para el motor de "
                "ese vehículo, no un valor igual para toda la flota; un motor cuya "
                "sobrevelocidad no está capturada no dispara la penalización, "
                "porque no se castiga a un vehículo por un dato que falta en el "
                "maestro."
            )
            if config.penalizacion_activa(PENALIZACION_SOBREVELOCIDAD_RPM)
            else (
                "Desactivada en la calibración de esta flota: un exceso por encima "
                "de la sobrevelocidad máxima del motor NO anula el puntaje. Los "
                "excesos se siguen contando y mostrando por vehículo, y siguen "
                "agravando los eventos de RPM como cualquier otro exceso sobre la "
                "gobernada."
            )
        ),
        # Refleja el modo EFECTIVO de la flota: el botón de información
        # describe la cifra que el usuario está viendo, no la regla general.
        "agregacion": (
            (
                "La cifra de flota agrega los puntajes por vehículo según "
                "exposición: bloques de 1000 km en comerciales y de 100 horas ECM "
                "en vocacionales. Así, un vehículo con poca operación no pesa "
                "igual que uno intensivo."
            )
            if config.promedio_ponderado
            else (
                "La cifra de flota es el promedio SIMPLE de los puntajes por "
                "vehículo: un vehículo, un voto, sin importar cuánto operó. Es la "
                "opción elegida en la calibración de esta flota; la alternativa "
                "pondera por exposición y describe mejor el conjunto de la "
                "operación cuando los vehículos son muy dispares en kilometraje."
            )
        ),
        # También refleja el estado EFECTIVO de la flota: si el cliente pone la
        # exposición mínima en 0, este texto dice que no hay mínimo.
        "exposicion_minima": (
            (
                f"Un vehículo necesita al menos {_es_corto(config.exposicion_minima_km_dia)} km "
                f"por día del periodo consultado —{_es_corto(config.exposicion_minima_horas_dia)} "
                "horas ECM por día en vocacionales— para recibir puntaje. Por debajo "
                "de ese mínimo no se califica: los puntajes son tasas (eventos por "
                "1000 km, tiempo sobre tiempo) y con muy poca operación medirían la "
                "falta de uso y no la conducción. El vehículo sigue en la tabla, con "
                "el puntaje que habría tenido como referencia, pero no entra en el "
                "promedio ni en el estado de la flota. Un exceso sobre la "
                "sobrevelocidad máxima sí se reporta aunque la exposición sea baja."
            )
            if (
                config.exposicion_minima_km_dia > 0
                or config.exposicion_minima_horas_dia > 0
            )
            else (
                "Sin exposición mínima en la calibración de esta flota: todo "
                "vehículo con algún kilómetro registrado recibe puntaje, incluso si "
                "operó tan poco que la tasa deja de ser representativa."
            )
        ),
    }


def _configuracion_publicada(
    efectivo: calificacion_config_service.EffectiveCalificacionConfig,
) -> dict[str, Any]:
    """La calibración efectiva tal como la publica `/reportes/calificacion`.

    Los parámetros van PLANOS junto a su procedencia, no anidados: quien
    consume esta respuesta ya sabe que está mirando una calificación y sólo
    necesita saber con qué números se calculó. Los valores por defecto no se
    repiten acá —los sirve `GET /reportes/calificacion/config`, que es la
    pantalla donde hacen falta para ofrecer "restablecer"—.

    `origen` es el campo que importa: es lo único que le dice al front que está
    viendo valores por defecto en vez de los de una flota concreta. Con varias
    flotas de calibración distinta en el alcance (`"mixto"`), el puntaje se
    calculó con los defaults y no corresponde a ninguna de ellas; la interfaz
    tiene que poder pedir que se elija una flota, y sin este campo no sabría que
    debe hacerlo.
    """
    return {
        **config_to_mapping(efectivo.config),
        "origen": efectivo.origen,
        "fleet_id": str(efectivo.fleet_id) if efectivo.fleet_id else None,
        "actualizado_en": efectivo.actualizado_en,
        "actualizado_por": efectivo.actualizado_por,
    }


def _empty_calificacion(
    config: CalificacionConfig,
    configuracion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Respuesta vacía que igual publica la calibración vigente.

    Un alcance sin datos no es un alcance sin calibración: el front necesita los
    umbrales y la metodología para pintar el gauge y el botón de información
    aunque no haya un solo vehículo.
    """
    return {
        "promedio_general": None,
        "estado": {"no_cumple": 0, "en_riesgo": 0, "cumple": 0},
        "umbrales": _umbrales_calificacion(config),
        "metodologia": _metodologia_calificacion(config),
        "configuracion": configuracion,
        "evolucion": [],
        "operativos": [],
        "vehiculos": [],
    }


async def get_calificacion(
    db: AsyncSession,
    *,
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fleet_ids: list[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    """Calificación de la flota: gauge general, estado (donut), evolución mensual
    (QHS/QHO/QGen), evolución de hábitos operativos y tabla por vehículo.

    La calibración se resuelve UNA vez acá y se pasa hacia abajo. No se resuelve
    dentro de los bucles por vehículo-mes: son cientos de iteraciones y la
    calibración no cambia entre ellas. Además, resolverla una sola vez es lo que
    garantiza que el puntaje, los umbrales del gauge y el texto de la
    metodología describan exactamente la misma fórmula.
    """
    efectivo = await calificacion_config_service.effective_config(db, fleet_ids)
    config = efectivo.config
    # Se resuelve UNA vez, fuera de todo bucle, igual que el resto de la
    # calibración: la penalización es de la flota, no del vehículo.
    penaliza_sobrevelocidad = config.penalizacion_activa(PENALIZACION_SOBREVELOCIDAD_RPM)
    # Se resuelve UNA vez, fuera de todo bucle, como el resto de la calibración,
    # y se aplica en los DOS agregados —el gauge y la serie mensual—: si sólo
    # entrara en uno, el promedio y la evolución contarían historias distintas.
    ponderado = config.promedio_ponderado
    # Días del periodo consultado: el divisor de la exposición mínima. Sin los
    # dos extremos no se puede enunciar cuánto es "poco", y la regla se apaga
    # (ver `_exposicion_minima`). En la práctica la pantalla siempre manda
    # ambos; un consumidor del API que no los mande conserva el comportamiento
    # anterior en vez de recibir un puntaje recortado por una regla implícita.
    dias_periodo = (
        (date_to - date_from).days + 1
        if date_from is not None and date_to is not None and date_to >= date_from
        else None
    )
    configuracion = _configuracion_publicada(efectivo)
    allowed = fleet_ids
    if allowed == []:
        return _empty_calificacion(config, configuracion)
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return _empty_calificacion(config, configuracion)

    f = FactCombustibleDaily
    h = FactHabitoEvent
    effective = _effective_distance_expressions(f)

    # `vocacional` vive en el maestro transaccional; analytics se enlaza por
    # base física + device_id. Se aplica alcance de flota antes de materializar
    # los IDs.
    vocational_stmt = select(DimVehicle.vehicle_id).where(
        sqlalchemy.exists().where(
            Vehicle.geotab_database_id == GeotabDatabase.id,
            Vehicle.geotab_device_id == DimVehicle.device_id,
            func.lower(GeotabDatabase.database_key)
            == func.lower(DimVehicle.database_name),
            Vehicle.is_active.is_(True),
            Vehicle.vocacional.is_(True),
        )
    )
    vocational_stmt, _voc_empty = _apply_vehicle_scope(
        vocational_stmt,
        DimVehicle.vehicle_id,
        allowed,
    )
    if _voc_empty:
        vocational_ids: set[str] = set()
    else:
        if vehicle_id is not None:
            vocational_stmt = vocational_stmt.where(DimVehicle.vehicle_id.in_(vehicle_id))
        vocational_ids = set((await db.execute(vocational_stmt)).scalars().all())

    # --- Combustible por mes y vehículo: bandas + km recorridos ---
    # El grano es (mes, vehículo) porque el score de un mes es el promedio de los
    # vehículos, no la tasa de la flota entera. Los porcentajes siguen ponderados
    # por el tiempo que los produjo dentro de cada vehículo-mes.
    month_key_f = cast(f.date_key / 100, Integer)
    comb_month_stmt = select(
        month_key_f.label("mk"),
        f.vehicle_id,
        _weighted(_EFIC_BAND, f.tiempo_total_en_rango),
        _weighted(f.pct_ralenti, _ralenti_weight(f)),
        func.coalesce(func.sum(effective["kms"]), 0.0),
        func.coalesce(func.sum(f.hrs_ecm), 0.0),
        func.coalesce(func.sum(f.tiempo_total_en_rango), 0.0),
        func.coalesce(func.sum(_ralenti_weight(f)), 0.0),
    )
    comb_month_stmt, _ev = _apply_vehicle_scope(comb_month_stmt, f.vehicle_id, allowed)
    if _ev:
        return _empty_calificacion(config, configuracion)
    comb_month_stmt = _apply_daily_filters(
        comb_month_stmt, vehicle_id=vehicle_id, date_from=date_from, date_to=date_to
    ).group_by(month_key_f, f.vehicle_id)
    comb_month: dict[int, dict[str, dict[str, Any]]] = {}
    for mk, vid, efic, ral, km, hours, t_rango, t_horas in (
        await db.execute(comb_month_stmt)
    ).all():
        comb_month.setdefault(int(mk), {})[vid] = {
            "efic": efic,
            "ralenti": ral,
            "km": km or 0.0,
            "hours": hours or 0.0,
            "vocacional": vid in vocational_ids,
            "t_rango": t_rango or 0.0,
            "t_horas": t_horas or 0.0,
        }

    # --- Eventos por mes, vehículo y tipo ---
    # Los eventos no RPM alimentan QHS. Los RPM alimentan QHO por conteo y los
    # que superan la velocidad gobernada de SU motor reciben la agravación.
    #
    # Hasta 2026-08-27 el umbral era la constante 2100 RPM. 2100 es la gobernada
    # del ISG12 y del X13E6 y de nadie más: sobreestimaba el exceso en el X11
    # (1925, eventos reales que no contaban doble) y lo subestimaba en el ISD6.7
    # (2850, eventos normales que sí contaban doble). Ahora el umbral sale de
    # `public.motor_catalog.governed_speed_rpm` del motor de cada vehículo,
    # reutilizando el MISMO resolvedor que el filtro de hábitos, en su forma de
    # join (`_join_motor_rpm_limit`): aquí el límite se evalúa en cada una de
    # las 161.378 filas de RPM de la ventana, y la subconsulta correlacionada
    # que usa el filtro costaba 25,4 s contra 15,8 s del join — a 2 s del
    # `statement_timeout` de 30 s, con resultado idéntico.
    #
    # ASIMETRÍA DELIBERADA CONTRA EL FILTRO — no "unificar" esto después:
    # `_apply_habito_filters(rpm_threshold=...)` es fail-closed y EXCLUYE los
    # eventos de un motor sin gobernada capturada, porque un exceso sobre un
    # límite desconocido no es demostrable y no se debe listar. Aquí no puede
    # excluir nada: la calificación es un puntaje y quitarle eventos a un
    # vehículo lo premiaría por un hueco del maestro. Un evento sin límite
    # conocido sigue contando x1 y sólo se queda sin la agravación x2. Eso es
    # exactamente lo que hace el CASE: con `governed_limit` NULL la comparación
    # da NULL, el WHEN no se cumple y cae al `else_=0`, o sea sin agravar,
    # pero la fila ya se contó en el `func.count()` de al lado.
    #
    # Al 2026-08-27 hay 10 de 14 motores con gobernada capturada; A26, S13 y
    # L9 370 no la tienen (25 vehículos), y esos son los que quedan sin x2.
    #
    # El conteo de sobrevelocidad (tercer `sum`) sale del MISMO join: las dos
    # columnas del umbral viven en la misma fila de `motor_catalog`, así que
    # `max_overspeed_rpm` no cuesta ni un join ni una subconsulta más que
    # `governed_speed_rpm`. Ver `_QGEN_PENALIZADO` para la penalización que
    # dispara y por qué su fail-open es el mismo que el de la agravación.
    month_key_h = cast(h.date_key / 100, Integer)
    rpm_value = _rpm_value_expression(h)
    governed_limit = _motor_rpm_limit_column("governed")
    overspeed_limit = _motor_rpm_limit_column("overspeed")
    hab_month_stmt = _join_motor_rpm_limit(
        select(
            month_key_h.label("mk"),
            h.vehicle_id,
            func.max(h.placa),
            h.event_type,
            func.count(),
            func.sum(
                case(
                    (
                        and_(
                            h.event_type.ilike("%rpm%"),
                            rpm_value > governed_limit,
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
            func.sum(
                case(
                    (
                        and_(
                            h.event_type.ilike("%rpm%"),
                            rpm_value > overspeed_limit,
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
        )
        .select_from(h)
        .join(DimRule, DimRule.rule_sk == h.rule_sk, isouter=True),
        h,
    )
    hab_month_stmt = _apply_habito_filters(
        hab_month_stmt,
        vehicle_id=vehicle_id,
        date_from=date_from,
        date_to=date_to,
        event_type=None,
        categoria=HABITO_CATEGORIA,
        allowed_vehicle_ids=allowed,
    ).group_by(month_key_h, h.vehicle_id, h.event_type)
    hab_month: dict[int, dict[str, dict[str, Any]]] = {}
    for mk, vid, placa, event_type, count, high_count, over_count in (
        await db.execute(hab_month_stmt)
    ).all():
        row = hab_month.setdefault(int(mk), {}).setdefault(
            vid,
            {
                "placa": placa,
                "qhs_weight": 0.0,
                "rpm_total": 0.0,
                "rpm_high": 0.0,
                "rpm_over": 0.0,
                # El SQL ya agrupa por `h.event_type`; guardar el conteo por
                # tipo es lo único que hace posible el desglose QHS del detalle
                # sin una sola consulta extra. El bucle lo colapsaba a un
                # escalar y ahí se perdía el "por qué".
                "eventos": {},
            },
        )
        row["qhs_weight"] += _qhs_event_weight(event_type, config=config) * int(count)
        clave_evento = event_type or "(sin tipo)"
        row["eventos"][clave_evento] = row["eventos"].get(clave_evento, 0) + int(count)
        if _is_rpm_event(event_type):
            row["rpm_total"] += int(count)
            row["rpm_high"] += int(high_count or 0)
            row["rpm_over"] += int(over_count or 0)

    evolucion: list[dict[str, Any]] = []
    operativos: list[dict[str, Any]] = []
    for mk in sorted(set(comb_month) | set(hab_month)):
        vehiculos_mes = comb_month.get(mk, {})
        eventos_mes = hab_month.get(mk, {})
        label = _month_label(mk)
        # Días DE ESE MES dentro del rango: el primer y el último mes de la
        # serie suelen venir cubiertos a medias (el rango por defecto es "tres
        # meses atrás → hoy"), y cobrarles la exposición de un mes entero
        # dejaría sin calificar a vehículos que sí operaron.
        anio_mes, mes_mes = divmod(mk, 100)
        inicio_mes = date(anio_mes, mes_mes, 1)
        fin_mes = date(anio_mes + mes_mes // 12, mes_mes % 12 + 1, 1) - timedelta(days=1)
        dias_mes = (
            _dias_en_rango(inicio_mes, fin_mes, date_from, date_to)
            if dias_periodo is not None
            else None
        )

        qho_pairs: list[tuple[float, float]] = []
        qhs_pairs: list[tuple[float, float]] = []
        qgen_pairs: list[tuple[float, float]] = []
        for vid in set(vehiculos_mes) | set(eventos_mes):
            c = vehiculos_mes.get(vid)
            eventos = eventos_mes.get(
                vid,
                {"qhs_weight": 0.0, "rpm_total": 0.0, "rpm_high": 0.0, "rpm_over": 0.0},
            )
            km = c["km"] if c else 0.0
            voc_mes = bool((c or {}).get("vocacional", vid in vocational_ids))
            rpm_weighted = _weighted_rpm_events(
                int(eventos["rpm_total"]),
                int(eventos["rpm_high"]),
                config=config,
            )
            qho_v = (
                _score_qho(
                    c["efic"],
                    c["ralenti"],
                    rpm_weighted,
                    km,
                    c["hours"],
                    c["vocacional"],
                    config=config,
                )
                if c
                else None
            )
            qhs_v = _score_qhs(eventos["qhs_weight"], km, config=config)
            qgen_v = _score_qgen(qhs_v, qho_v, config=config)
            # Mismo override que en la tabla por vehículo: un mes con
            # sobrevelocidad tiene que verse en el QGen de ese vehículo-mes, o
            # el gauge y la serie se contradicen. `qhs`/`qho` de la serie no se
            # tocan: la penalización es del puntaje general.
            #
            # La penalización GANA sobre la exposición insuficiente y ese orden
            # no se debe invertir: el exceso ocurrió, y esconderlo porque el
            # vehículo rodó poco sería premiar la falta de kilómetros. Sólo un
            # vehículo-mes SIN penalización queda sin calificar por exposición.
            if penaliza_sobrevelocidad and int(eventos.get("rpm_over", 0)) > 0:
                qgen_v = _QGEN_PENALIZADO
            elif qgen_v is not None and not _exposicion_suficiente(
                km=km,
                horas=(c or {}).get("hours"),
                vocacional=voc_mes,
                minimo=_exposicion_minima(
                    vocacional=voc_mes, dias=dias_mes, config=config
                ),
            ):
                qgen_v = None
            operation_weight = (
                _rpm_exposure_units(
                    km=km,
                    horas=c["hours"],
                    vocacional=c["vocacional"],
                )
                if c
                else None
            )
            if qho_v is not None:
                qho_pairs.append((qho_v, operation_weight or 0.0))
            if qhs_v is not None:
                qhs_pairs.append((qhs_v, km))
            if qgen_v is not None:
                qgen_pairs.append((qgen_v, operation_weight or 0.0))

        evolucion.append(
            {
                "periodo": mk,
                "label": label,
                "qhs": _promedio_ponderado(qhs_pairs, ponderado=ponderado),
                "qho": _promedio_ponderado(qho_pairs, ponderado=ponderado),
                "qgen": _promedio_ponderado(qgen_pairs, ponderado=ponderado),
            }
        )
        # Los porcentajes del mes sí son de la flota: son tiempo sobre tiempo, y
        # su peso natural es el tiempo, no los kilómetros.
        operativos.append(
            {
                "periodo": mk,
                "label": label,
                "pct_eficiente": _promedio_ponderado(
                    [(v["efic"], v["t_rango"]) for v in vehiculos_mes.values()]
                ),
                "pct_ralenti": _promedio_ponderado(
                    [(v["ralenti"], v["t_horas"]) for v in vehiculos_mes.values()]
                ),
                "eventos_rpm": int(sum(v["rpm_total"] for v in eventos_mes.values())),
                "eventos_rpm_sobre_gobernada": int(sum(v["rpm_high"] for v in eventos_mes.values())),
            }
        )

    # --- Combustible por vehículo (periodo completo) ---
    comb_veh_stmt = select(
        f.vehicle_id,
        func.max(f.placa),
        _weighted(_EFIC_BAND, f.tiempo_total_en_rango),
        _weighted(f.pct_ralenti, _ralenti_weight(f)),
        func.coalesce(func.sum(effective["kms"]), 0.0),
        func.coalesce(func.sum(f.hrs_ecm), 0.0),
    )
    comb_veh_stmt, _ev2 = _apply_vehicle_scope(comb_veh_stmt, f.vehicle_id, allowed)
    if _ev2:
        return _empty_calificacion(config, configuracion)
    comb_veh_stmt = _apply_daily_filters(
        comb_veh_stmt, vehicle_id=vehicle_id, date_from=date_from, date_to=date_to
    ).group_by(f.vehicle_id)
    comb_veh = {
        vid: {
            "placa": placa,
            "efic": efic,
            "ralenti": ral,
            "km": km or 0.0,
            "hours": hours or 0.0,
            "vocacional": vid in vocational_ids,
        }
        for vid, placa, efic, ral, km, hours in (await db.execute(comb_veh_stmt)).all()
    }

    # --- Eventos por vehículo ---
    # Se derivan de la agregación mensual ya consultada para no volver a recorrer
    # toda fact_habito_event ni repetir la extracción regex de RPM.
    hab_veh: dict[str, dict[str, Any]] = {}
    for month in hab_month.values():
        for vid, values in month.items():
            row = hab_veh.setdefault(
                vid,
                {
                    "placa": values["placa"],
                    "qhs_weight": 0.0,
                    "rpm_total": 0,
                    "rpm_high": 0,
                    "rpm_over": 0,
                    "eventos": {},
                },
            )
            row["qhs_weight"] += values["qhs_weight"]
            row["rpm_total"] += int(values["rpm_total"])
            row["rpm_high"] += int(values["rpm_high"])
            row["rpm_over"] += int(values["rpm_over"])
            for tipo, n in values["eventos"].items():
                row["eventos"][tipo] = row["eventos"].get(tipo, 0) + int(n)

    vehiculos: list[dict[str, Any]] = []
    estado = {"no_cumple": 0, "en_riesgo": 0, "cumple": 0}
    qgen_vals: list[tuple[float, float]] = []
    # (fila, peso) en el orden del bucle, para repartir `peso_en_promedio` una
    # vez conocido el total. Guarda la referencia a la misma fila que va en
    # `vehiculos`, así que el orden de la tabla se puede cambiar después.
    pesos: list[tuple[dict[str, Any], float]] = []
    for vid in set(comb_veh) | set(hab_veh):
        c = comb_veh.get(vid)
        hv = hab_veh.get(vid)
        rpm_weighted = _weighted_rpm_events(
            (hv or {}).get("rpm_total", 0),
            (hv or {}).get("rpm_high", 0),
            config=config,
        )
        qho = (
            _score_qho(
                c["efic"],
                c["ralenti"],
                rpm_weighted,
                c["km"],
                c["hours"],
                c["vocacional"],
                config=config,
            )
            if c
            else None
        )
        qhs = _score_qhs(
            (hv or {}).get("qhs_weight", 0.0),
            c["km"] if c else None,
            config=config,
        )
        qgen_base = _score_qgen(qhs, qho, config=config)
        # Override por sobrevelocidad: ver `_QGEN_PENALIZADO`. Se aplica también
        # cuando `qgen_base` es None (vehículo sin combustible ni km): el evento
        # ocurrió y dejarlo "no evaluable" lo esconde justo del donut donde el
        # usuario lo busca. `qgen_base` conserva el None y nada se pierde.
        eventos_sobre = int((hv or {}).get("rpm_over", 0))
        # El conteo de excesos se publica SIEMPRE; lo que la flota puede apagar
        # es que anulen el puntaje. Un exceso ocurrido sigue siendo un hecho
        # aunque el cliente decida que no invalide la calificación.
        penalizado = eventos_sobre > 0 and penaliza_sobrevelocidad
        vocacional = (c or {}).get("vocacional", vid in vocational_ids)
        exposicion_minima = _exposicion_minima(
            vocacional=vocacional, dias=dias_periodo, config=config
        )
        # La penalización GANA sobre la exposición insuficiente, y ese orden no
        # se debe invertir: el exceso ocurrió, y dejar de reportarlo porque el
        # vehículo rodó poco sería premiar la falta de kilómetros. Al revés
        # tampoco: un vehículo sin penalización y sin exposición queda SIN
        # calificar (`None`), no en 0 — no operar no es operar mal, y un 0 lo
        # mezclaría en el donut con los penalizados.
        # `suficiente` es el hecho de la exposición y nada más: se publica tal
        # cual. Que la penalización tenga precedencia se decide abajo, no
        # falseando este booleano — un vehículo penalizado que además rodó poco
        # sigue habiendo rodado poco, y la ficha debe poder decirlo.
        suficiente = _exposicion_suficiente(
            km=(c or {}).get("km"),
            horas=(c or {}).get("hours"),
            vocacional=vocacional,
            minimo=exposicion_minima,
        )
        if penalizado:
            qgen: float | None = _QGEN_PENALIZADO
            est = _ESTADO_PENALIZADO
        elif not suficiente:
            qgen = None
            est = None
        else:
            qgen = qgen_base
            est = _estado_calificacion(qgen, config=config)
        if est == "No cumple":
            estado["no_cumple"] += 1
        elif est == "En riesgo":
            estado["en_riesgo"] += 1
        elif est == "Cumple":
            estado["cumple"] += 1
        peso = (
            _rpm_exposure_units(
                km=(c or {}).get("km"),
                horas=(c or {}).get("hours"),
                vocacional=vocacional,
            )
            or 0.0
        )
        if qgen is not None:
            qgen_vals.append((qgen, peso))
        fila = {
            "vehicle_id": vid,
            "placa": (c or {}).get("placa") or (hv or {}).get("placa") or vid,
            "vocacional": vocacional,
            "eventos_rpm": (hv or {}).get("rpm_total", 0),
            "eventos_rpm_sobre_gobernada": (hv or {}).get("rpm_high", 0),
            "eventos_rpm_sobre_sobrevelocidad": eventos_sobre,
            "penalizado_por_sobrevelocidad": penalizado,
            "exposicion_suficiente": suficiente,
            # Se rellena después del bucle: es una fracción del total y el
            # total no existe hasta haber recorrido todos los vehículos.
            "peso_en_promedio": None,
            "qhs": qhs,
            "qho": qho,
            "qgen": qgen,
            "qgen_base": qgen_base,
            "estado": est,
            "detalle": _detalle_calificacion_vehiculo(
                pct_efic=(c or {}).get("efic"),
                pct_ralenti=(c or {}).get("ralenti"),
                km=(c or {}).get("km"),
                horas=(c or {}).get("hours"),
                vocacional=vocacional,
                eventos_rpm_ponderados=rpm_weighted,
                eventos_por_tipo=(hv or {}).get("eventos", {}),
                qhs=qhs,
                # Solo si de verdad penalizó. Con la penalización apagada,
                # los excesos siguen contando por la vía normal —y se
                # publican en `eventos_rpm_sobre_sobrevelocidad`—, pero no
                # tiene sentido listarlos como el motivo de un puntaje que
                # no anularon.
                eventos_sobrevelocidad=eventos_sobre if penalizado else 0,
                exposicion_minima=exposicion_minima,
                exposicion_suficiente=suficiente,
                dias_periodo=dias_periodo,
                config=config,
            ),
        }
        vehiculos.append(fila)
        pesos.append((fila, peso if qgen is not None else 0.0))

    # Peor primero (los None al final): la tabla prioriza vehículos a intervenir.
    vehiculos.sort(key=lambda r: (r["qgen"] is None, r["qgen"] or 0.0))
    # Mismo criterio que la serie mensual: bloques de exposición por tipo de uso.
    promedio = _promedio_ponderado(qgen_vals, ponderado=ponderado)
    # Cuánto pesa cada vehículo en ese promedio. Se publica porque es la
    # explicación del caso que motivó la exposición mínima: un vehículo con un
    # puntaje alto y una exposición marginal parecía "ignorado" por el gauge
    # cuando en realidad pesaba el 0,003 % de la flota. Con el número a la
    # vista, el promedio se puede auditar desde la propia tabla.
    #
    # El divisor es el MISMO que usa `_promedio_ponderado`, incluida su caída a
    # promedio simple cuando todos los pesos son cero: si publicara otra base,
    # los porcentajes no sumarían el promedio que se está mostrando.
    total_peso = sum(w for _, w in pesos)
    n_evaluados = sum(1 for fila, _ in pesos if fila["qgen"] is not None)
    for fila, w in pesos:
        if fila["qgen"] is None:
            fila["peso_en_promedio"] = 0.0
        elif ponderado and total_peso > 0:
            fila["peso_en_promedio"] = w / total_peso
        elif n_evaluados > 0:
            # Promedio simple —elegido por la flota, o forzado porque nadie
            # registró exposición—: un vehículo un voto.
            fila["peso_en_promedio"] = 1.0 / n_evaluados
        else:
            fila["peso_en_promedio"] = None
    return {
        "promedio_general": promedio,
        "estado": estado,
        "umbrales": _umbrales_calificacion(config),
        "metodologia": _metodologia_calificacion(config),
        "configuracion": configuracion,
        "evolucion": evolucion,
        "operativos": operativos,
        "vehiculos": vehiculos,
    }


# ---------------------------------------------------------------------------
# Agregación por grupo interno de vehículos (fleet_vehicle_groups)
# ---------------------------------------------------------------------------
#
# Los tres endpoints /reportes/*/por-grupo devuelven totales ADITIVOS por el
# grupo HOJA del vehículo (`public.vehicles.vehicle_group_id`). El rollup por
# niveles del árbol y las razones (km/gal, ev/1000km) los calcula el frontend
# DESPUÉS de sumar; aquí no se devuelve ningún ratio.


def _vehicle_group_map_subquery() -> Any:
    """Mapeo `analytics.dim_vehicle.vehicle_id` → `vehicles.vehicle_group_id`.

    Misma identidad compuesta que el resto del módulo (ver
    `_fleet_analytics_vehicle_id_query` y el `group_subq` de `list_vehicles`):
    `geotab_device_id == dim_vehicle.device_id` +
    `lower(database_key) == lower(dim_vehicle.database_name)` con
    `Vehicle.is_active IS TRUE`.

    Se consume como LEFT JOIN del hecho: un hecho cuyo vehículo no mapea al
    maestro —o cuyo vehículo no tiene grupo asignado— cae al bucket
    `group_id IS NULL` en vez de perderse. El `DISTINCT` colapsa pares
    duplicados exactos para que el join no infle los conteos del hecho.
    """
    map_vehicle = aliased(Vehicle, name="vg_map_vehicle")
    map_db = aliased(GeotabDatabase, name="vg_map_db")
    return (
        select(
            DimVehicle.vehicle_id.label("vehicle_id"),
            map_vehicle.vehicle_group_id.label("vehicle_group_id"),
        )
        .join(
            map_vehicle,
            map_vehicle.geotab_device_id == DimVehicle.device_id,
        )
        .join(map_db, map_vehicle.geotab_database_id == map_db.id)
        .where(
            map_vehicle.is_active.is_(True),
            func.lower(map_db.database_key) == func.lower(DimVehicle.database_name),
        )
        .distinct()
        .subquery("vg_map")
    )


async def combustible_por_grupo(
    db: AsyncSession,
    *,
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fuel_kind: str = FUEL_KIND_LIQUID,
    fleet_ids: list[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict[str, Any]]:
    """Totales aditivos de combustible por grupo hoja, en UNA sentencia.

    Cada suma usa exactamente la misma expresión que `get_combustible_summary`
    para que los buckets reconcilien con los KPIs: la distancia es la efectiva
    resuelta (`_effective_distance_expressions`), y `hrs` es la base horaria
    del `gal_hr`/`m3_hr` del summary (para gas, solo horas de días con consumo
    reportado).
    """
    allowed = fleet_ids
    if allowed == []:
        return []
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return []
    f = FactCombustibleDaily
    effective = _effective_distance_expressions(f)
    is_gas = fuel_kind == FUEL_KIND_GAS
    ratio_hours = (
        func.sum(sqlalchemy.case((f.comb > 0, f.hrs_ecm), else_=None))
        if is_gas
        else func.sum(f.hrs_ecm)
    )
    vg_map = _vehicle_group_map_subquery()
    kms_sum = func.coalesce(func.sum(effective["kms"]), 0.0)
    stmt = (
        select(
            vg_map.c.vehicle_group_id.label("group_id"),
            func.count(func.distinct(f.vehicle_id)).label("n_vehiculos"),
            func.count().label("n_registros"),
            kms_sum.label("kms"),
            func.coalesce(func.sum(f.comb), 0.0).label("comb"),
            func.coalesce(ratio_hours, 0.0).label("hrs"),
            func.coalesce(func.sum(f.comb_ralenti), 0.0).label("comb_ralenti"),
        )
        .select_from(f)
        .join(vg_map, vg_map.c.vehicle_id == f.vehicle_id, isouter=True)
    )
    stmt, _empty_v = _apply_vehicle_scope(stmt, f.vehicle_id, allowed)
    if _empty_v:
        return []
    stmt = stmt.where(_fuel_kind_predicate(f.fuel_kind, fuel_kind))
    stmt = _apply_daily_filters(stmt, vehicle_id=vehicle_id, date_from=date_from, date_to=date_to)
    stmt = stmt.group_by(vg_map.c.vehicle_group_id).order_by(
        kms_sum.desc(), vg_map.c.vehicle_group_id
    )
    return [dict(row) for row in (await db.execute(stmt)).mappings().all()]


async def habitos_por_grupo(
    db: AsyncSession,
    *,
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fleet_ids: list[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    """Eventos de hábitos seguros por grupo hoja, más los km para normalizar.

    Dos sentencias a propósito: la de eventos (mismos filtros/joins que
    `get_habito_summary`, categoría Seguridad) y una segunda sobre el hecho de
    combustible con el MISMO alcance, rango y filtro de vehículos —sin
    `fuel_kind`: se suma toda la distancia efectiva— para que el cliente
    calcule ev/1000km tras el rollup. Se mezclan en Python por `group_id`.

    Un grupo con km pero sin eventos también sale (n_eventos=0): omitirlo
    dejaría corto el denominador del rollup de su rama.
    """
    allowed = fleet_ids
    if allowed == []:
        return []
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return []

    h = FactHabitoEvent
    vg_map = _vehicle_group_map_subquery()
    stmt = (
        select(
            vg_map.c.vehicle_group_id.label("group_id"),
            func.count(func.distinct(h.vehicle_id)).label("n_vehiculos"),
            func.count().label("n_eventos"),
        )
        .select_from(h)
        .join(DimRule, DimRule.rule_sk == h.rule_sk, isouter=True)
        .join(vg_map, vg_map.c.vehicle_id == h.vehicle_id, isouter=True)
    )
    stmt = _apply_habito_filters(
        stmt,
        vehicle_id=vehicle_id,
        date_from=date_from,
        date_to=date_to,
        event_type=event_type,
        categoria=HABITO_CATEGORIA,
        allowed_vehicle_ids=allowed,
    )
    stmt = stmt.group_by(vg_map.c.vehicle_group_id)
    event_rows = (await db.execute(stmt)).all()

    f = FactCombustibleDaily
    effective = _effective_distance_expressions(f)
    kms_map_sq = _vehicle_group_map_subquery()
    kms_stmt = (
        select(
            kms_map_sq.c.vehicle_group_id,
            func.coalesce(func.sum(effective["kms"]), 0.0),
        )
        .select_from(f)
        .join(kms_map_sq, kms_map_sq.c.vehicle_id == f.vehicle_id, isouter=True)
    )
    kms_stmt, _empty_k = _apply_vehicle_scope(kms_stmt, f.vehicle_id, allowed)
    kms_by_group: dict[uuid.UUID | None, float] = {}
    if not _empty_k:
        kms_stmt = _apply_daily_filters(
            kms_stmt, vehicle_id=vehicle_id, date_from=date_from, date_to=date_to
        )
        kms_stmt = kms_stmt.group_by(kms_map_sq.c.vehicle_group_id)
        kms_by_group = {
            gid: (kms or 0.0) for gid, kms in (await db.execute(kms_stmt)).all()
        }

    buckets: dict[uuid.UUID | None, dict[str, Any]] = {}
    for gid, n_vehiculos, n_eventos in event_rows:
        buckets[gid] = {
            "group_id": gid,
            "n_vehiculos": n_vehiculos,
            "n_eventos": n_eventos,
            "kms": kms_by_group.get(gid, 0.0),
        }
    for gid, kms in kms_by_group.items():
        if gid not in buckets and kms:
            buckets[gid] = {
                "group_id": gid,
                "n_vehiculos": 0,
                "n_eventos": 0,
                "kms": kms,
            }
    # Orden estable: valor principal DESC, group_id como desempate (None al final).
    return sorted(
        buckets.values(),
        key=lambda b: (-b["n_eventos"], b["group_id"] is None, str(b["group_id"])),
    )


async def fallas_por_grupo(
    db: AsyncSession,
    *,
    vehicle_id: list[str] | None = None,
    motor_type: list[str] | None = None,
    fleet_ids: list[uuid.UUID] | None = None,
    exclude_telematics: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict[str, Any]]:
    """Fallas por grupo hoja, en UNA sentencia.

    `n_fallas` y `n_urgentes` usan la MISMA llave distinct del summary
    (`_unique_fault_key_expression`) y el mismo criterio de urgencia (luz de
    parada roja). `exclude_telematics` conserva el default `False` de los
    demás endpoints de fallas (ver `_apply_fault_filters`).
    """
    allowed = fleet_ids
    if allowed == []:
        return []
    vehicle_id, _empty = await _apply_motor_type_filter(db, vehicle_id, motor_type, fleet_ids)
    if _empty:
        return []
    f = FactFaultEvent
    unique_fault_key = _unique_fault_key_expression(f)
    vg_map = _vehicle_group_map_subquery()
    n_eventos = func.count()
    stmt = (
        select(
            vg_map.c.vehicle_group_id.label("group_id"),
            func.count(func.distinct(f.vehicle_id)).label("n_vehiculos"),
            func.count(func.distinct(unique_fault_key)).label("n_fallas"),
            n_eventos.label("n_eventos"),
            func.count(
                func.distinct(
                    case(
                        (f.luz_de_parada_roja.is_(True), unique_fault_key),
                        else_=None,
                    )
                )
            ).label("n_urgentes"),
        )
        .select_from(f)
        .join(vg_map, vg_map.c.vehicle_id == f.vehicle_id, isouter=True)
    )
    stmt = _apply_fault_filters(
        stmt,
        vehicle_id=vehicle_id,
        date_from=date_from,
        date_to=date_to,
        severity=None,
        only_urgent=False,
        allowed_vehicle_ids=allowed,
        exclude_telematics=exclude_telematics,
    )
    stmt = stmt.group_by(vg_map.c.vehicle_group_id).order_by(
        n_eventos.desc(), vg_map.c.vehicle_group_id
    )
    return [dict(row) for row in (await db.execute(stmt)).mappings().all()]
