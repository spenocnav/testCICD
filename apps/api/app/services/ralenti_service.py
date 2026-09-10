"""Servicio del módulo de reportes "Análisis Ralentí".

Lee `analytics.fact_ralenti_event`, un episodio de ralentí por fila (el ETL
fusiona los eventos crudos consecutivos). Todo lo que este módulo publica se
deriva de tres columnas —`duracion_segundos`, `rpm_promedio` y las
coordenadas— agrupadas por los cortes de duración y de RPM declarados abajo.

Reglas que gobiernan todas las consultas:

- **Alcance de flota en PostgreSQL.** Cada sentencia pasa por
  `analytics_service._apply_vehicle_scope`; un alcance vacío devuelve
  resultados vacíos sin tocar la base (fail-closed).
- **Los cortes viven UNA vez** (`DURATION_BUCKETS`, `RPM_BUCKETS`) y se
  traducen a un único `CASE` SQL. Etiquetas, orden y límites salen de ahí;
  el frontend espeja las claves, no los límites.
- **Un episodio sin duración no existe para el módulo.** Todas las métricas
  son minutos; una fila con `duracion_segundos` NULL no cabe en ningún bucket y
  haría que los porcentajes no sumaran 100. El ETL la calcula siempre a partir
  de `fin - inicio`, así que el filtro es defensivo, no una omisión de datos.
- **Una sentencia por agregado.** El resumen resuelve los 4 buckets de
  duración y los 6 de RPM con agregación condicional, nunca con N consultas.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from sqlalchemy import Float, Numeric, String, case, cast, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from app.models.analytics import DimVehicle, FactRalentiEvent
from app.services import analytics_service


@dataclass(frozen=True)
class RangoBucket:
    """Un corte de una métrica continua.

    `lower` es inclusivo y `upper` exclusivo; `None` en cualquiera de los dos
    significa sin límite por ese lado. Con AMBOS en `None` el bucket recoge los
    valores NULL de la columna (el caso "Sin RPM").
    """

    key: str
    label: str
    lower: float | None
    upper: float | None

    @property
    def is_null_bucket(self) -> bool:
        return self.lower is None and self.upper is None

    def condition(self, column: Any) -> ColumnElement[bool]:
        """Predicado SQL que decide si `column` cae en este bucket."""
        if self.is_null_bucket:
            is_null: ColumnElement[bool] = column.is_(None)
            return is_null
        conditions: list[ColumnElement[bool]] = []
        if self.lower is not None:
            conditions.append(column >= self.lower)
        if self.upper is not None:
            conditions.append(column < self.upper)
        if len(conditions) == 1:
            return conditions[0]
        return conditions[0] & conditions[1]


#: Cortes de duración del episodio (`duracion_segundos`), en orden de menor a
#: mayor. El primero no tiene piso a propósito: una duración por debajo de 0
#: no debería existir, pero si el ETL la produjera es "menos de un minuto",
#: no una fila perdida.
DURATION_BUCKETS: tuple[RangoBucket, ...] = (
    RangoBucket("lt1", "< 1 min", None, 60.0),
    RangoBucket("1_5", "1 – 5 min", 60.0, 300.0),  # noqa: RUF001 (guion largo tipográfico en la etiqueta)
    RangoBucket("5_10", "5 – 10 min", 300.0, 600.0),  # noqa: RUF001 (guion largo tipográfico en la etiqueta)
    RangoBucket("gt10", "> 10 min", 600.0, None),
)

#: Cortes de RPM promedio del episodio (`rpm_promedio`). `sin_rpm` recoge los
#: episodios sin muestras de RPM: el dato falta, no es cero.
RPM_BUCKETS: tuple[RangoBucket, ...] = (
    RangoBucket("lt600", "< 600 RPM", None, 600.0),
    RangoBucket("600_800", "600 – 800 RPM", 600.0, 800.0),  # noqa: RUF001 (guion largo tipográfico en la etiqueta)
    RangoBucket("800_1000", "800 – 1.000 RPM", 800.0, 1000.0),  # noqa: RUF001 (guion largo tipográfico en la etiqueta)
    RangoBucket("1000_1200", "1.000 – 1.200 RPM", 1000.0, 1200.0),  # noqa: RUF001 (guion largo tipográfico en la etiqueta)
    RangoBucket("gt1200", "> 1.200 RPM", 1200.0, None),
    RangoBucket("sin_rpm", "Sin RPM", None, None),
)

DURATION_BUCKET_KEYS: tuple[str, ...] = tuple(b.key for b in DURATION_BUCKETS)
RPM_BUCKET_KEYS: tuple[str, ...] = tuple(b.key for b in RPM_BUCKETS)

#: Campos por los que el listado de episodios se puede ordenar (allowlist).
EVENT_SORT_FIELDS: tuple[str, ...] = ("inicio", "duracion_segundos", "rpm_promedio")

#: Tope de celdas del mapa de calor: más que eso no se pinta, se satura.
HEATMAP_MAX_CELLS = 5000

Granularity = Literal["daily", "monthly"]


def _find_bucket(buckets: Sequence[RangoBucket], key: str) -> RangoBucket:
    for bucket in buckets:
        if bucket.key == key:
            return bucket
    raise ValueError(f"bucket desconocido: {key!r}")


def _bucket_case(column: Any, buckets: Sequence[RangoBucket]) -> ColumnElement[str]:
    """`CASE` que etiqueta cada fila con la clave de su bucket.

    Los buckets cubren la recta completa y el NULL cuando hay un bucket nulo,
    así que no lleva `ELSE`: una fila que no cae en ninguno (imposible salvo
    contrato roto) queda NULL y no se disfraza de otro rango.
    """
    return case(*((b.condition(column), literal(b.key)) for b in buckets))


def duration_bucket_expression(model: type[FactRalentiEvent] = FactRalentiEvent) -> Any:
    return _bucket_case(model.duracion_segundos, DURATION_BUCKETS)


def rpm_bucket_expression(model: type[FactRalentiEvent] = FactRalentiEvent) -> Any:
    return _bucket_case(model.rpm_promedio, RPM_BUCKETS)


def _minutes(column: Any) -> Any:
    return column / 60.0


def _round(value: float | None, digits: int) -> float:
    return round(float(value or 0.0), digits)


def _pct(part: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return round(part / total * 100.0, 1)


def _avg_minutes(minutos: float, eventos: int) -> float | None:
    if eventos <= 0:
        return None
    return round(minutos / eventos, 2)


@dataclass(frozen=True)
class RalentiFilters:
    """Filtros comunes a todos los endpoints del módulo."""

    vehicle_id: list[str] | None = None
    motor_type: list[str] | None = None
    date_from: date | None = None
    date_to: date | None = None
    duration_bucket: str | None = None
    rpm_bucket: str | None = None


async def _scoped_statement(
    db: AsyncSession,
    stmt: Select[Any],
    filters: RalentiFilters,
    fleet_ids: Sequence[uuid.UUID] | None,
) -> tuple[Select[Any], bool]:
    """Aplica alcance de flota y filtros a `stmt`.

    Devuelve `(stmt, empty)`; con `empty=True` el llamador responde vacío sin
    ejecutar nada: el alcance o el filtro de motor no dejan ningún vehículo.
    """
    fleet_list = list(fleet_ids) if fleet_ids is not None else None
    if fleet_list == []:
        return stmt, True
    vehicle_ids, empty = await analytics_service._apply_motor_type_filter(
        db, filters.vehicle_id, filters.motor_type, fleet_list
    )
    if empty:
        return stmt, True
    stmt, empty = analytics_service._apply_vehicle_scope(
        stmt, FactRalentiEvent.vehicle_id, fleet_list
    )
    if empty:
        return stmt, True

    f = FactRalentiEvent
    stmt = stmt.where(f.duracion_segundos.isnot(None))
    if vehicle_ids is not None:
        stmt = stmt.where(f.vehicle_id.in_(vehicle_ids))
    if filters.date_from is not None:
        stmt = stmt.where(f.fecha >= filters.date_from)
    if filters.date_to is not None:
        stmt = stmt.where(f.fecha <= filters.date_to)
    if filters.duration_bucket is not None:
        bucket = _find_bucket(DURATION_BUCKETS, filters.duration_bucket)
        stmt = stmt.where(bucket.condition(f.duracion_segundos))
    if filters.rpm_bucket is not None:
        bucket = _find_bucket(RPM_BUCKETS, filters.rpm_bucket)
        stmt = stmt.where(bucket.condition(f.rpm_promedio))
    return stmt, False


# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------


def _empty_summary() -> dict[str, Any]:
    return {
        "total_eventos": 0,
        "total_minutos": 0.0,
        "duracion_promedio_min": None,
        "vehiculos": 0,
        "por_duracion": [
            {
                "bucket": b.key,
                "label": b.label,
                "eventos": 0,
                "minutos": 0.0,
                "pct_eventos": 0.0,
                "pct_minutos": 0.0,
                "duracion_promedio_min": None,
            }
            for b in DURATION_BUCKETS
        ],
        "por_rpm": [
            {
                "bucket": b.key,
                "label": b.label,
                "eventos": 0,
                "minutos": 0.0,
                "pct_eventos": 0.0,
                "pct_minutos": 0.0,
            }
            for b in RPM_BUCKETS
        ],
    }


def _conditional_count(condition: ColumnElement[bool]) -> Any:
    # `count` ignora NULL: el CASE sin ELSE cuenta sólo las filas del bucket.
    return func.count(case((condition, 1)))


def _conditional_minutes(condition: ColumnElement[bool]) -> Any:
    return func.coalesce(
        func.sum(case((condition, _minutes(FactRalentiEvent.duracion_segundos)), else_=0.0)),
        0.0,
    )


async def get_summary(
    db: AsyncSession,
    *,
    filters: RalentiFilters,
    fleet_ids: Sequence[uuid.UUID] | None,
) -> dict[str, Any]:
    """KPIs y distribución por duración y por RPM en UNA sentencia."""
    f = FactRalentiEvent
    columns: list[Any] = [
        func.count().label("total_eventos"),
        func.coalesce(func.sum(_minutes(f.duracion_segundos)), 0.0).label("total_minutos"),
        func.count(func.distinct(f.vehicle_id)).label("vehiculos"),
    ]
    for b in DURATION_BUCKETS:
        cond = b.condition(f.duracion_segundos)
        columns.append(_conditional_count(cond).label(f"dur_ev_{b.key}"))
        columns.append(_conditional_minutes(cond).label(f"dur_min_{b.key}"))
    for b in RPM_BUCKETS:
        cond = b.condition(f.rpm_promedio)
        columns.append(_conditional_count(cond).label(f"rpm_ev_{b.key}"))
        columns.append(_conditional_minutes(cond).label(f"rpm_min_{b.key}"))

    stmt, empty = await _scoped_statement(db, select(*columns), filters, fleet_ids)
    if empty:
        return _empty_summary()
    row = (await db.execute(stmt)).mappings().one()

    total_eventos = int(row["total_eventos"] or 0)
    total_minutos_raw = float(row["total_minutos"] or 0.0)
    if total_eventos == 0:
        return _empty_summary()

    por_duracion: list[dict[str, Any]] = []
    for b in DURATION_BUCKETS:
        eventos = int(row[f"dur_ev_{b.key}"] or 0)
        minutos_raw = float(row[f"dur_min_{b.key}"] or 0.0)
        por_duracion.append(
            {
                "bucket": b.key,
                "label": b.label,
                "eventos": eventos,
                "minutos": _round(minutos_raw, 2),
                "pct_eventos": _pct(eventos, total_eventos),
                "pct_minutos": _pct(minutos_raw, total_minutos_raw),
                "duracion_promedio_min": _avg_minutes(minutos_raw, eventos),
            }
        )
    por_rpm: list[dict[str, Any]] = []
    for b in RPM_BUCKETS:
        eventos = int(row[f"rpm_ev_{b.key}"] or 0)
        minutos_raw = float(row[f"rpm_min_{b.key}"] or 0.0)
        por_rpm.append(
            {
                "bucket": b.key,
                "label": b.label,
                "eventos": eventos,
                "minutos": _round(minutos_raw, 2),
                "pct_eventos": _pct(eventos, total_eventos),
                "pct_minutos": _pct(minutos_raw, total_minutos_raw),
            }
        )
    return {
        "total_eventos": total_eventos,
        "total_minutos": _round(total_minutos_raw, 2),
        "duracion_promedio_min": _avg_minutes(total_minutos_raw, total_eventos),
        "vehiculos": int(row["vehiculos"] or 0),
        "por_duracion": por_duracion,
        "por_rpm": por_rpm,
    }


# ---------------------------------------------------------------------------
# Por placa
# ---------------------------------------------------------------------------


def dominant_duration_bucket(minutos_por_bucket: dict[str, float]) -> str | None:
    """Clave del bucket con más minutos; en empate gana el más largo.

    Recorre los buckets del más largo al más corto y sólo cambia de candidato
    ante un valor estrictamente mayor, así el empate se queda con el largo. Sin
    minutos en ningún bucket no hay rango dominante.
    """
    best_key: str | None = None
    best_minutes = 0.0
    for bucket in reversed(DURATION_BUCKETS):
        minutes = float(minutos_por_bucket.get(bucket.key) or 0.0)
        if minutes > best_minutes:
            best_key = bucket.key
            best_minutes = minutes
    return best_key


async def list_por_placa(
    db: AsyncSession,
    *,
    filters: RalentiFilters,
    fleet_ids: Sequence[uuid.UUID] | None,
) -> list[dict[str, Any]]:
    """Una fila por vehículo con eventos y minutos por bucket de duración."""
    f = FactRalentiEvent
    columns: list[Any] = [
        f.vehicle_id.label("vehicle_id"),
        func.max(DimVehicle.vehicle_label).label("placa"),
        func.count().label("total_eventos"),
        func.coalesce(func.sum(_minutes(f.duracion_segundos)), 0.0).label("total_minutos"),
    ]
    for b in DURATION_BUCKETS:
        cond = b.condition(f.duracion_segundos)
        columns.append(_conditional_count(cond).label(f"ev_{b.key}"))
        columns.append(_conditional_minutes(cond).label(f"min_{b.key}"))
    stmt = (
        select(*columns)
        .select_from(f)
        .join(DimVehicle, DimVehicle.vehicle_id == f.vehicle_id, isouter=True)
        .where(f.vehicle_id.isnot(None))
        .group_by(f.vehicle_id)
        .order_by(func.count().desc(), f.vehicle_id)
    )
    stmt, empty = await _scoped_statement(db, stmt, filters, fleet_ids)
    if empty:
        return []
    rows = (await db.execute(stmt)).mappings().all()
    result: list[dict[str, Any]] = []
    for row in rows:
        minutos_raw = {b.key: float(row[f"min_{b.key}"] or 0.0) for b in DURATION_BUCKETS}
        item: dict[str, Any] = {
            "vehicle_id": row["vehicle_id"],
            "placa": row["placa"],
            "total_eventos": int(row["total_eventos"] or 0),
            "total_minutos": _round(row["total_minutos"], 2),
            "rango_dominante": dominant_duration_bucket(minutos_raw),
        }
        for b in DURATION_BUCKETS:
            item[f"eventos_{b.key}"] = int(row[f"ev_{b.key}"] or 0)
            item[f"minutos_{b.key}"] = _round(minutos_raw[b.key], 2)
        result.append(item)
    return result


# ---------------------------------------------------------------------------
# Mapa de calor
# ---------------------------------------------------------------------------


async def list_heatmap(
    db: AsyncSession,
    *,
    filters: RalentiFilters,
    fleet_ids: Sequence[uuid.UUID] | None,
    precision: int = 3,
) -> list[dict[str, Any]]:
    """Celdas `(lat, lon)` redondeadas a `precision` decimales, con eventos y minutos.

    `round(double precision, int)` no existe en PostgreSQL: se redondea sobre
    `numeric` y se devuelve como float. Ordena por minutos descendente y
    desempata por coordenada para que el corte en `HEATMAP_MAX_CELLS` sea
    determinista.
    """
    f = FactRalentiEvent
    lat = cast(func.round(cast(f.latitud, Numeric), precision), Float).label("latitud")
    lon = cast(func.round(cast(f.longitud, Numeric), precision), Float).label("longitud")
    minutos = func.coalesce(func.sum(_minutes(f.duracion_segundos)), 0.0).label("minutos")
    stmt = (
        select(lat, lon, func.count().label("eventos"), minutos)
        .where(f.latitud.isnot(None), f.longitud.isnot(None))
        .group_by(lat, lon)
        .order_by(minutos.desc(), lat, lon)
        .limit(HEATMAP_MAX_CELLS)
    )
    stmt, empty = await _scoped_statement(db, stmt, filters, fleet_ids)
    if empty:
        return []
    rows = (await db.execute(stmt)).mappings().all()
    return [
        {
            "latitud": float(row["latitud"]),
            "longitud": float(row["longitud"]),
            "eventos": int(row["eventos"] or 0),
            "minutos": _round(row["minutos"], 2),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Serie temporal
# ---------------------------------------------------------------------------


async def list_timeseries(
    db: AsyncSession,
    *,
    filters: RalentiFilters,
    fleet_ids: Sequence[uuid.UUID] | None,
    granularity: Granularity = "daily",
) -> list[dict[str, Any]]:
    """Eventos y minutos por día (`YYYY-MM-DD`) o por mes (`YYYY-MM`), ascendente."""
    f = FactRalentiEvent
    if granularity == "monthly":
        bucket = func.to_char(f.fecha, "YYYY-MM").label("bucket")
    else:
        bucket = cast(f.fecha, String).label("bucket")
    stmt = (
        select(
            bucket,
            func.count().label("eventos"),
            func.coalesce(func.sum(_minutes(f.duracion_segundos)), 0.0).label("minutos"),
        )
        .where(f.fecha.isnot(None))
        .group_by(bucket)
        .order_by(bucket)
    )
    stmt, empty = await _scoped_statement(db, stmt, filters, fleet_ids)
    if empty:
        return []
    rows = (await db.execute(stmt)).mappings().all()
    return [
        {
            "bucket": str(row["bucket"]),
            "eventos": int(row["eventos"] or 0),
            "minutos": _round(row["minutos"], 2),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Listado de episodios
# ---------------------------------------------------------------------------


def _event_order_by(sort_by: str, sort_dir: str) -> list[Any]:
    """ORDER BY total y estable del listado.

    Misma disciplina que `_habito_event_order_by`: allowlist cerrada, NULL al
    final en las dos direcciones (en PostgreSQL `DESC` implica NULLS FIRST) y
    desempate por `event_sk` (PK) para que la paginación por OFFSET no repita
    ni omita filas cuando dos episodios duran lo mismo.
    """
    if sort_dir not in ("asc", "desc"):
        raise ValueError(f"sort_dir debe ser 'asc' o 'desc', no {sort_dir!r}")
    f = FactRalentiEvent
    columns: dict[str, Any] = {
        "inicio": f.inicio,
        "duracion_segundos": f.duracion_segundos,
        "rpm_promedio": f.rpm_promedio,
    }
    column = columns.get(sort_by)
    if column is None:
        raise ValueError(
            f"sort_by debe ser uno de {', '.join(EVENT_SORT_FIELDS)}, no {sort_by!r}"
        )
    ordered = column.desc().nullslast() if sort_dir == "desc" else column.asc().nullslast()
    tiebreak = f.event_sk.desc() if sort_dir == "desc" else f.event_sk.asc()
    return [ordered, tiebreak]


async def list_events(
    db: AsyncSession,
    *,
    filters: RalentiFilters,
    fleet_ids: Sequence[uuid.UUID] | None,
    sort_by: str = "inicio",
    sort_dir: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Página de episodios con placa y bucket ya resueltos, más el total."""
    f = FactRalentiEvent
    base = select(f.event_sk).select_from(f)
    base, empty = await _scoped_statement(db, base, filters, fleet_ids)
    if empty:
        return [], 0

    # `base` ya tiene alcance y filtros; el total y la página nacen de la misma
    # cláusula WHERE para que nunca cuenten conjuntos distintos.
    where_clause = base.whereclause
    count_stmt = select(func.count()).select_from(f)
    page_stmt = (
        select(
            f.event_sk,
            f.vehicle_id,
            DimVehicle.vehicle_label.label("placa"),
            f.inicio,
            f.fin,
            f.duracion_segundos,
            f.eventos_fuente,
            f.rpm_promedio,
            f.rpm_maximo,
            f.latitud,
            f.longitud,
            duration_bucket_expression().label("duration_bucket"),
            rpm_bucket_expression().label("rpm_bucket"),
        )
        .select_from(f)
        .join(DimVehicle, DimVehicle.vehicle_id == f.vehicle_id, isouter=True)
    )
    if where_clause is not None:
        count_stmt = count_stmt.where(where_clause)
        page_stmt = page_stmt.where(where_clause)

    total = int((await db.execute(count_stmt)).scalar_one())
    rows = (
        await db.execute(
            page_stmt.order_by(*_event_order_by(sort_by, sort_dir)).limit(limit).offset(offset)
        )
    ).mappings().all()
    items = [
        {
            "event_sk": row["event_sk"],
            "vehicle_id": row["vehicle_id"],
            "placa": row["placa"],
            "inicio": row["inicio"],
            "fin": row["fin"],
            "duracion_min": _round(float(row["duracion_segundos"] or 0.0) / 60.0, 2),
            "eventos_fuente": int(row["eventos_fuente"] or 0),
            "rpm_promedio": row["rpm_promedio"],
            "rpm_maximo": row["rpm_maximo"],
            "latitud": row["latitud"],
            "longitud": row["longitud"],
            "duration_bucket": row["duration_bucket"],
            "rpm_bucket": row["rpm_bucket"],
        }
        for row in rows
    ]
    return items, total
