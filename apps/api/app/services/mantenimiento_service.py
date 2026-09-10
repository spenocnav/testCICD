"""Servicios de KPI de mantenimiento sobre la replica Cloudfleet.

Replica los calculos del mockup (`kpi_engine.py`) sobre `cloudfleet_work_orders`,
`cloudfleet_maintenance_schedules` y `cloudfleet_vehicles`, cruzando con
`vehicles` y `fleets` para el scope por flota/placa del portal.

Bloques:
- Helpers + scope (`_scoped_plates`) + fetch crudo.
- Disponibilidad interna (mecanica/proyecto) por horas, con merge de intervalos.
- Preventivo (cumplimiento y puntualidad de planes).
- Confiabilidad (fallas, MTTR, MTBF).
- Ordenes (backlog, ciclo tecnico, razones).
- Rankings por flota.

Toda fecha Cloudfleet se guarda en UTC y se convierte a America/Bogota para el
calculo (semantica de negocio en Colombia).
"""

from __future__ import annotations

import statistics
import uuid
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

NON_FAILURE_TYPES = {"Programado", "RTM", "Suministros", "Back-Up", "Alistamiento"}
ACTIVE_STATUSES = {"opened", "onTechnicalCompletion"}
# Planes aún no ejecutados: próximos, en fecha o vencidos (excluye "Ejecutada *").
PENDING_SCHEDULE_STATUSES = {"Próximo", "A tiempo", "Vencido"}
# Planes ya ejecutados (histórico de programaciones).
EXECUTED_SCHEDULE_STATUSES = {"Ejecutada a tiempo", "Ejecutada vencida"}
BOGOTA = ZoneInfo("America/Bogota")
UTC = ZoneInfo("UTC")

_MONTHS_ES = [
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
]


def _is_failure(t: str | None) -> bool:
    return (t or "Sin tipo") not in NON_FAILURE_TYPES


def _num(value) -> float | None:
    """Metros de Cloudfleet llegan como float o null; normaliza a float|None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _to_local(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(BOGOTA).replace(tzinfo=None)


def _merge_intervals(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    if not intervals:
        return []
    norm: list[tuple[datetime, datetime]] = []
    for a, b in intervals:
        if a < b:
            norm.append((a, b))
    norm.sort(key=lambda x: x[0])
    merged: list[tuple[datetime, datetime]] = []
    for a, b in norm:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _period_bounds_local(df: date, dt: date) -> tuple[datetime, datetime]:
    p_start = datetime.combine(df, time.min)
    p_end = datetime.combine(dt + timedelta(days=1), time.min)
    return p_start, p_end


def _overlap_hours(
    a0: datetime, a1: datetime, b0: datetime, b1: datetime
) -> float:
    delta = min(a1, b1) - max(a0, b0)
    secs = delta.total_seconds()
    return max(0.0, secs / 3600.0)


def _iter_months(df: date, dt: date) -> list[tuple[date, date]]:
    if df > dt:
        return []
    months: list[tuple[date, date]] = []
    cursor = df.replace(day=1)
    while cursor <= dt:
        if cursor.month == 12:
            nxt = cursor.replace(year=cursor.year + 1, month=1)
        else:
            nxt = cursor.replace(month=cursor.month + 1)
        last = nxt - timedelta(days=1)
        m0 = max(df, cursor)
        m1 = min(dt, last)
        months.append((m0, m1))
        cursor = nxt
    return months


def _month_label(d: date) -> str:
    return _MONTHS_ES[d.month - 1]


def _is_open_at_end(w: dict, p_end: datetime) -> bool:
    """Foto histórica inferida sólo desde inicio y cierre técnico.

    ``p_end`` es el límite exclusivo del periodo. No se usa el estado actual,
    porque no permite reconstruir cómo estaba la OT en una fecha pasada.
    """
    s_raw = w.get("start_date") or w.get("workshop_date") or w.get("cf_created_at")
    if s_raw is None:
        return False
    s = _to_local(s_raw)
    tc = _to_local(w.get("technical_completion_date"))
    return s is not None and s < p_end and (tc is None or tc > p_end)


def _local_bound_to_utc(value: datetime) -> datetime:
    """Convierte un límite naive de negocio (Bogotá) a UTC aware para SQL."""
    return value.replace(tzinfo=BOGOTA).astimezone(UTC)


async def _scoped_plates(
    db: AsyncSession,
    fleet_ids: list[uuid.UUID] | None,
    plates: list[str] | None,
) -> dict[str, dict]:
    """Resuelve las placas que entran en el alcance de un reporte.

    Semántica:
    - ``fleet_ids`` concretos: intersección de vehículos **activos** del portal
      que pertenecen a esas flotas (``vehicles.is_active``) **y** placas
      vigentes en el maestro de Cloudfleet (``cloudfleet_vehicles.is_in_master``).
      Placas pedidas explícitamente que no estén en esa intersección quedan
      **excluidas** (p. ej. placas inactivas o ya no master) — coherente con la
      semántica de ``analytics_service.scoped_fleet_ids``.
    - ``fleet_ids is None``: semántica **administrativa** preservada del
      contrato existente: además de la unión anterior, se añaden las placas
      que sólo existen en Cloudfleet (sin fila en ``vehicles`` del portal),
      etiquetadas ``fleet_name = "Sin flota"``. En ambos casos se descartan
      filas ``cloudfleet_vehicles.is_in_master = false`` (histórico, no
      maestro vigente), para evitar que una placa retirada del maestro
      contamine KPIs en curso.

    Devuelve ``{plate: {fleet_id, fleet_name, cd}}``. Placas inválidas/no
    master nunca aparecen aunque estén en ``plates``.
    """
    no_fleet = fleet_ids is None
    no_plates = plates is None
    params = {
        "no_fleet": no_fleet,
        "no_plates": no_plates,
        "fleet_ids": fleet_ids or [],
        "plates": plates or [],
    }
    q1 = text(
        "SELECT v.plate AS plate, v.fleet_id AS fleet_id, f.name AS fleet_name, "
        "v.vehicle_group_id AS vehicle_group_id, "
        "cv.cost_center AS cc, cv.city AS city "
        "FROM vehicles v "
        "JOIN fleets f ON f.id = v.fleet_id "
        "JOIN cloudfleet_vehicles cv ON cv.code = v.plate "
        "WHERE v.is_active "
        "AND cv.is_in_master "
        "AND (:no_fleet OR v.fleet_id = ANY(:fleet_ids)) "
        "AND (:no_plates OR v.plate = ANY(:plates))"
    )
    out: dict[str, dict] = {}
    for row in (await db.execute(q1, params)).mappings():
        out[row["plate"]] = {
            "fleet_id": row["fleet_id"],
            "fleet_name": row["fleet_name"] or "Sin flota",
            "cd": row["cc"] or row["city"] or "Sin CD",
            "vehicle_group_id": row["vehicle_group_id"],
        }

    if no_fleet:
        q2 = text(
            "SELECT cv.code AS plate, cv.cost_center AS cc, cv.city AS city "
            "FROM cloudfleet_vehicles cv "
            "LEFT JOIN vehicles v ON v.plate = cv.code AND v.is_active "
            "WHERE v.plate IS NULL "
            "AND cv.is_in_master "
            "AND (:no_plates OR cv.code = ANY(:plates))"
        )
        for row in (await db.execute(q2, params)).mappings():
            out[row["plate"]] = {
                "fleet_id": None,
                "fleet_name": "Sin flota",
                "cd": row["cc"] or row["city"] or "Sin CD",
                "vehicle_group_id": None,
            }
    return out


async def _fetch_work_orders(
    db: AsyncSession,
    plate_set: set[str],
    start: datetime | None = None,
    end: datetime | None = None,
    include_statuses: set[str] | None = None,
) -> list[dict]:
    """Carga sólo OTs que pueden solapar el rango local half-open ``[start, end)``.

    Se comprueban las tres fechas de inicio usadas por los distintos KPI para
    no descartar una OT que un consumidor pueda recuperar mediante fallback.
    Los parámetros se convierten a UTC aware antes de compararlos con columnas
    ``timestamptz``.
    """
    if not plate_set:
        return []
    params: dict = {"p": list(plate_set)}
    overlap_sql = ""
    if start is not None and end is not None:
        overlap_conditions = (
            "("
            "start_date < :end_dt"
            " OR workshop_date < :end_dt"
            " OR cf_created_at < :end_dt"
            " OR (start_date IS NULL AND workshop_date IS NULL AND cf_created_at IS NULL)"
            ") AND (technical_completion_date IS NULL OR technical_completion_date > :start_dt)"
        )
        if include_statuses:
            overlap_sql = (
                " AND ("
                f"({overlap_conditions})"
                " OR status = ANY(:include_statuses)"
                ")"
            )
            params["include_statuses"] = list(include_statuses)
        else:
            overlap_sql = f" AND ({overlap_conditions})"
        params["start_dt"] = _local_bound_to_utc(start)
        params["end_dt"] = _local_bound_to_utc(end)
    rows = (
        await db.execute(
            text(
                "SELECT number, vehicle_code, status, type, reason, detected_issue, "
                "affects_vehicle_availability, start_date, workshop_date, "
                "technical_completion_date, final_completion_date, "
                "estimated_finish_date, cf_created_at "
                "FROM cloudfleet_work_orders "
                "WHERE vehicle_code = ANY(:p)" + overlap_sql
            ),
            params,
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def _availability_core(
    scope: dict[str, dict], wos: list[dict], df: date, dt: date
) -> dict:
    p_start, p_end = _period_bounds_local(df, dt)
    period_hours = (p_end - p_start).total_seconds() / 3600.0

    def compute(project_only: bool):
        intervals: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
        orders_by_plate: dict[str, list[dict]] = defaultdict(list)
        order_count = 0
        for w in wos:
            code = w["vehicle_code"]
            if code not in scope:
                continue
            if project_only and not w["affects_vehicle_availability"]:
                continue
            s = _to_local(w["start_date"] or w["workshop_date"])
            if s is None:
                continue
            raw_end = w["technical_completion_date"]
            if raw_end is None:
                if w["status"] in ACTIVE_STATUSES:
                    e = p_end
                else:
                    continue
            else:
                converted_end = _to_local(raw_end)
                assert converted_end is not None
                e = converted_end
            s2 = max(s, p_start)
            e2 = min(e, p_end)
            if s2 >= e2:
                continue
            order_count += 1
            intervals[code].append((s2, e2))
            orders_by_plate[code].append(
                {
                    "number": w["number"],
                    "type": w["type"],
                    "status": w["status"],
                    "reason": w["reason"],
                    "start": s.isoformat(),
                    "technicalCompletion": e.isoformat() if raw_end else None,
                    "overlapHours": round(
                        _overlap_hours(s2, e2, p_start, p_end), 2
                    ),
                }
            )
        per_plate: dict[str, dict] = {}
        total = 0.0
        affected = 0
        for code in scope:
            merged = _merge_intervals(intervals.get(code, []))
            h = sum((b - a).total_seconds() / 3600.0 for a, b in merged)
            total += h
            if h > 0:
                affected += 1
            per_plate[code] = {
                "hours": round(h, 2),
                "intervals": [
                    {"start": a.isoformat(), "end": b.isoformat()} for a, b in merged
                ],
                "orders": orders_by_plate.get(code, []),
            }
        return total, affected, per_plate, order_count

    mec_total, mec_aff, mec_plate, order_count = compute(False)
    proj_total, proj_aff, proj_plate, _ = compute(True)
    should = len(scope) * period_hours

    def pct(un: float) -> float:
        return round((should - un) / should * 100, 2) if should else 100.0

    return {
        "period_hours": period_hours,
        "should": should,
        "mec_total": mec_total,
        "proj_total": proj_total,
        "mec_aff": mec_aff,
        "proj_aff": proj_aff,
        "mec_plate": mec_plate,
        "proj_plate": proj_plate,
        "order_count": order_count,
        "pct_mec": pct(mec_total),
        "pct_proj": pct(proj_total),
    }


def _availability_monthly_totals(
    scope: dict[str, dict],
    wos: list[dict],
    date_from: date,
    date_to: date,
    *,
    project_only: bool,
) -> dict[tuple[int, int], float]:
    """Fusiona intervalos una vez y reparte sus horas entre meses.

    La unión global por placa es equivalente a unir de nuevo después de
    recortar cada mes, y evita volver a recorrer todas las OTs por cada punto.
    """
    months = _iter_months(date_from, date_to)
    bounds = {
        (m0.year, m0.month): _period_bounds_local(m0, m1) for m0, m1 in months
    }
    overall_start, overall_end = _period_bounds_local(date_from, date_to)
    intervals: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    for w in wos:
        code = w["vehicle_code"]
        if code not in scope:
            continue
        if project_only and not w["affects_vehicle_availability"]:
            continue
        start = _to_local(w["start_date"] or w["workshop_date"])
        if start is None:
            continue
        raw_end = w["technical_completion_date"]
        if raw_end is None:
            if w["status"] not in ACTIVE_STATUSES:
                continue
            end = overall_end
        else:
            converted_end = _to_local(raw_end)
            assert converted_end is not None
            end = converted_end
        clipped_start = max(start, overall_start)
        clipped_end = min(end, overall_end)
        if clipped_start < clipped_end:
            intervals[code].append((clipped_start, clipped_end))

    totals: dict[tuple[int, int], float] = defaultdict(float)
    for plate_intervals in intervals.values():
        for start, end in _merge_intervals(plate_intervals):
            cursor = date(start.year, start.month, 1)
            while datetime.combine(cursor, time.min) < end:
                key = (cursor.year, cursor.month)
                if key in bounds:
                    month_start, month_end = bounds[key]
                    totals[key] += _overlap_hours(start, end, month_start, month_end)
                cursor = (
                    cursor.replace(year=cursor.year + 1, month=1)
                    if cursor.month == 12
                    else cursor.replace(month=cursor.month + 1)
                )
    return totals


async def get_disponibilidad(
    db: AsyncSession,
    fleet_ids: list[uuid.UUID] | None,
    plates: list[str] | None,
    date_from: date,
    date_to: date,
) -> dict:
    scope = await _scoped_plates(db, fleet_ids, plates)
    p_start, p_end = _period_bounds_local(date_from, date_to)
    wos = await _fetch_work_orders(db, set(scope), p_start, p_end)
    c = _availability_core(scope, wos, date_from, date_to)

    top = [
        {
            "plate": code,
            "hours": d["hours"],
            "availabilityPct": round(
                (c["period_hours"] - d["hours"]) / c["period_hours"] * 100,
                2,
            )
            if c["period_hours"]
            else 100.0,
            "cd": scope[code]["cd"],
            "fleet": scope[code]["fleet_name"],
            "intervals": d["intervals"],
            "orders": d["orders"],
        }
        for code, d in c["mec_plate"].items()
        if d["hours"] > 0
    ]
    top.sort(key=lambda x: x["hours"], reverse=True)

    def group(keyfn):
        agg: dict[str, dict] = defaultdict(lambda: {"un": 0.0, "veh": 0})
        for code in scope:
            k = keyfn(code)
            agg[k]["veh"] += 1
            agg[k]["un"] += c["mec_plate"][code]["hours"]
        res = []
        for k, v in agg.items():
            sh = v["veh"] * c["period_hours"]
            res.append(
                {
                    "name": k,
                    "availabilityPct": round((sh - v["un"]) / sh * 100, 2)
                    if sh
                    else 100.0,
                    "unavailableHours": round(v["un"], 2),
                    "vehicles": v["veh"],
                }
            )
        return res

    por_flota = sorted(
        group(lambda code: scope[code]["fleet_name"]),
        key=lambda x: x["availabilityPct"],
    )
    por_cd = sorted(
        group(lambda code: scope[code]["cd"]),
        key=lambda x: x["availabilityPct"],
    )

    return {
        "summary": {
            "placas": len(scope),
            "shouldHours": round(c["should"], 2),
            "unavailableHoursMec": round(c["mec_total"], 2),
            "unavailableHoursProj": round(c["proj_total"], 2),
            "availabilityPctMec": c["pct_mec"],
            "availabilityPctProj": c["pct_proj"],
            "affectedVehiclesMec": c["mec_aff"],
            "affectedVehiclesProj": c["proj_aff"],
            "orderCount": c["order_count"],
        },
        "topPlacas": top,
        "porFlota": por_flota,
        "porCd": por_cd,
    }


async def get_disponibilidad_timeseries(
    db: AsyncSession,
    fleet_ids: list[uuid.UUID] | None,
    plates: list[str] | None,
    date_from: date,
    date_to: date,
) -> list[dict]:
    scope = await _scoped_plates(db, fleet_ids, plates)
    p_start, p_end = _period_bounds_local(date_from, date_to)
    wos = await _fetch_work_orders(db, set(scope), p_start, p_end)
    mec_totals = _availability_monthly_totals(
        scope, wos, date_from, date_to, project_only=False
    )
    project_totals = _availability_monthly_totals(
        scope, wos, date_from, date_to, project_only=True
    )
    out: list[dict] = []
    for m0, m1 in _iter_months(date_from, date_to):
        key = (m0.year, m0.month)
        period_start, period_end = _period_bounds_local(m0, m1)
        should = len(scope) * (period_end - period_start).total_seconds() / 3600
        mec = mec_totals[key]
        project = project_totals[key]
        mec_pct = round((should - mec) / should * 100, 2) if should else 100.0
        project_pct = (
            round((should - project) / should * 100, 2) if should else 100.0
        )

        out.append(
            {
                "month": f"{m0.year}-{m0.month:02d}",
                "label": _month_label(m0),
                "mecanica": mec_pct,
                "proyecto": project_pct,
                "unavailableHours": round(mec, 2),
            }
        )
    return out


def _group_disponibilidad_rollup(
    scope: Mapping[str, Mapping],
    per_plate_hours: Mapping[str, float],
    period_hours: float,
) -> list[dict]:
    """Rollup por grupo HOJA (``vehicle_group_id`` exacto; None = sin grupo).

    Función pura sobre el mapa de ``_scoped_plates`` y el detalle por placa
    que ya calcula ``_availability_core`` (no duplica la fórmula). El rollup
    por niveles del árbol y los porcentajes los deriva el frontend.
    Orden estable: downtime DESC, ``group_id`` como desempate (None primero).
    """
    buckets: dict[uuid.UUID | None, dict] = {}
    for code, meta in scope.items():
        group_id = meta.get("vehicle_group_id")
        bucket = buckets.setdefault(
            group_id,
            {
                "group_id": group_id,
                "placas": 0,
                "should_hours": 0.0,
                "downtime_hours": 0.0,
            },
        )
        bucket["placas"] += 1
        bucket["should_hours"] += period_hours
        bucket["downtime_hours"] += float(per_plate_hours.get(code, 0.0))
    out = list(buckets.values())
    out.sort(
        key=lambda b: (
            -b["downtime_hours"],
            b["group_id"] is not None,
            str(b["group_id"] or ""),
        )
    )
    for bucket in out:
        bucket["should_hours"] = round(bucket["should_hours"], 2)
        bucket["downtime_hours"] = round(bucket["downtime_hours"], 2)
    return out


async def get_disponibilidad_por_grupo(
    db: AsyncSession,
    fleet_ids: list[uuid.UUID] | None,
    plates: list[str] | None,
    date_from: date,
    date_to: date,
) -> list[dict]:
    """Should/downtime agregados por grupo interno del cliente.

    Reutiliza el mismo cálculo por placa del summary (``_availability_core``,
    variante mecánica: todas las OTs) y sólo hace el rollup en Python con el
    ``vehicle_group_id`` que ya trae ``_scoped_plates``.
    """
    scope = await _scoped_plates(db, fleet_ids, plates)
    p_start, p_end = _period_bounds_local(date_from, date_to)
    wos = await _fetch_work_orders(db, set(scope), p_start, p_end)
    core = _availability_core(scope, wos, date_from, date_to)
    per_plate_hours = {code: d["hours"] for code, d in core["mec_plate"].items()}
    return _group_disponibilidad_rollup(scope, per_plate_hours, core["period_hours"])


async def list_scope_placas(
    db: AsyncSession, fleet_ids: list[uuid.UUID] | None
) -> list[dict]:
    scope = await _scoped_plates(db, fleet_ids, None)
    return sorted(
        (
            {
                "plate": k,
                "fleet": v["fleet_name"],
                "cd": v["cd"],
                # Grupo interno del cliente; la UI resuelve nombre/árbol con
                # GET /vehicles/groups y traduce grupo -> placas al filtrar.
                "vehicle_group_id": v.get("vehicle_group_id"),
            }
            for k, v in scope.items()
        ),
        key=lambda x: x["plate"],
    )


# ---------------------------------------------------------------------------
# Preventivo
# ---------------------------------------------------------------------------
async def _fetch_schedules(
    db: AsyncSession, plate_set: set[str], date_from: date, date_to: date
) -> list[dict]:
    if not plate_set:
        return []
    lo = datetime.combine(date_from, time.min)
    hi = datetime.combine(date_to + timedelta(days=1), time.min)
    rows = (
        await db.execute(
            text(
                "SELECT vehicle_code, status, date_to_execute, schedule_type, task_name "
                "FROM cloudfleet_maintenance_schedules "
                "WHERE vehicle_code = ANY(:p) "
                "AND date_to_execute >= :lo AND date_to_execute < :hi"
            ),
            {"p": list(plate_set), "lo": lo, "hi": hi},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def _preventivo_core(rows: list[dict]) -> dict:
    c = Counter(r["status"] or "Sin estado" for r in rows)
    executed = c["Ejecutada a tiempo"] + c["Ejecutada vencida"]
    on_time = c["Ejecutada a tiempo"]
    due = len(rows)
    pending = c["Próximo"] + c["Vencido"] + c["A tiempo"]
    overdue = c["Vencido"]
    return {
        "dueCount": due,
        "executedCount": executed,
        "onTimeCount": on_time,
        "lateExecuted": c["Ejecutada vencida"],
        "pending": pending,
        "overduePending": overdue,
        "executionPct": round(executed / due * 100, 2) if due else 0.0,
        "onTimePct": round(on_time / due * 100, 2) if due else 0.0,
        "byStatus": dict(c),
    }


async def get_preventivo(
    db: AsyncSession,
    fleet_ids: list[uuid.UUID] | None,
    plates: list[str] | None,
    date_from: date,
    date_to: date,
) -> dict:
    scope = await _scoped_plates(db, fleet_ids, plates)
    rows = await _fetch_schedules(db, set(scope), date_from, date_to)
    return _preventivo_core(rows)


async def get_preventivo_timeseries(
    db: AsyncSession,
    fleet_ids: list[uuid.UUID] | None,
    plates: list[str] | None,
    date_from: date,
    date_to: date,
) -> list[dict]:
    scope = await _scoped_plates(db, fleet_ids, plates)
    rows = await _fetch_schedules(db, set(scope), date_from, date_to)
    months = _iter_months(date_from, date_to)
    buckets: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for r in rows:
        loc = _to_local(r["date_to_execute"])
        if loc is not None:
            buckets[(loc.year, loc.month)].append(r)
    out: list[dict] = []
    for m0, _m1 in months:
        c = _preventivo_core(buckets[(m0.year, m0.month)])
        out.append(
            {
                "month": f"{m0.year}-{m0.month:02d}",
                "label": _month_label(m0),
                "ejecucion": c["executionPct"],
                "puntualidad": c["onTimePct"],
            }
        )
    return out


# ---------------------------------------------------------------------------
# Programaciones (pendientes e histórico), agrupadas por rutina
# ---------------------------------------------------------------------------
def _group_schedules(
    rows: Sequence[Mapping[Any, Any]],
    scope: Mapping[str, Mapping[str, Any]],
    today: date,
) -> list[dict[str, Any]]:
    """Agrupa filas de cronograma por (placa, rutina, fecha) en una ocurrencia.

    Cloudfleet trae una fila por tarea; los objetivos (odómetro/horómetro,
    diffs, estado) son uniformes dentro de la misma ocurrencia de rutina, así
    que se toman de la primera fila y las tareas se listan aparte. Planes sin
    rutina (`Trabajo` manual) agrupan por tarea, quedando fila propia.
    """
    groups: dict[tuple[str, str | None, date | None], dict[str, Any]] = {}
    for r in rows:
        raw = r["raw"] or {}
        veh = raw.get("vehicle") or {}
        due_local = _to_local(r["date_to_execute"])
        due_date = due_local.date() if due_local else None
        key = (r["vehicle_code"], r["routine_name"] or r["task_name"], due_date)
        g = groups.get(key)
        if g is None:
            odo_diff = _num(raw.get("odometerToExecuteDiff"))
            avg_odo = _num(veh.get("avgOdometerDay"))
            horo_diff = _num(raw.get("hourmeterToExecuteDiff"))
            avg_horo = _num(veh.get("avgHourmeterDay"))
            # ETA solo con diff positivo: objetivo ya alcanzado no tiene ETA.
            eta_odo = (
                round(odo_diff / avg_odo, 1)
                if odo_diff is not None and odo_diff > 0 and avg_odo and avg_odo > 0
                else None
            )
            eta_horo = (
                round(horo_diff / avg_horo, 1)
                if horo_diff is not None and horo_diff > 0 and avg_horo and avg_horo > 0
                else None
            )
            exec_local = _to_local(r["wo_execution_date"])
            meta = scope[r["vehicle_code"]]
            g = groups[key] = {
                "plate": r["vehicle_code"],
                "fleet": meta["fleet_name"],
                "cd": meta["cd"],
                "routine": r["routine_name"] or r["task_name"],
                "tasks": [],
                "tipo": r["schedule_type"],
                "source": r["schedule_source"],
                "dateToExecute": due_date.isoformat() if due_date else None,
                "daysToExecute": (due_date - today).days if due_date else None,
                "status": r["status"],
                "odometerToExecute": _num(raw.get("odometerToExecute")),
                "currentOdometer": _num(veh.get("currentOdometer")),
                "odometerDiff": odo_diff,
                "avgOdometerDay": avg_odo,
                "etaOdometerDays": eta_odo,
                "hourmeterToExecute": _num(raw.get("hourmeterToExecute")),
                "currentHourmeter": _num(veh.get("currentHourmeter")),
                "hourmeterDiff": horo_diff,
                "avgHourmeterDay": avg_horo,
                "etaHourmeterDays": eta_horo,
                "woNumber": r["wo_number"],
                "woExecutionDate": (
                    exec_local.date().isoformat() if exec_local else None
                ),
            }
        if r["task_name"] and r["task_name"] not in g["tasks"]:
            g["tasks"].append(r["task_name"])
        if g["woNumber"] is None and r["wo_number"] is not None:
            g["woNumber"] = r["wo_number"]
    return list(groups.values())


async def get_proximas_programaciones(
    db: AsyncSession,
    fleet_ids: list[uuid.UUID] | None,
    plates: list[str] | None,
    horizon_days: int = 90,
    limit: int = 200,
) -> list[dict]:
    """Rutinas de mantenimiento aún no ejecutadas, ordenadas por fecha de ingreso.

    La fecha manda: incluye vencidos pendientes (fecha pasada, sin ejecutar) y
    los próximos hasta `horizon_days` adelante. El odómetro/horómetro se expone
    como guía (leído de `raw`), no gobierna el orden — la ejecución real la
    define el equipo técnico. Una fila por ocurrencia de rutina con sus tareas.
    """
    scope = await _scoped_plates(db, fleet_ids, plates)
    if not scope:
        return []
    today = date.today()
    hi = datetime.combine(today + timedelta(days=horizon_days + 1), time.min)
    rows = (
        await db.execute(
            text(
                "SELECT vehicle_code, status, task_name, routine_name, schedule_type, "
                "schedule_source, date_to_execute, wo_number, wo_execution_date, raw "
                "FROM cloudfleet_maintenance_schedules "
                "WHERE vehicle_code = ANY(:p) AND status = ANY(:st) "
                "AND date_to_execute < :hi "
                "ORDER BY date_to_execute ASC LIMIT :lim"
            ),
            {
                "p": list(scope),
                "st": list(PENDING_SCHEDULE_STATUSES),
                "hi": hi,
                "lim": limit,
            },
        )
    ).mappings().all()
    return _group_schedules(rows, scope, today)


async def get_historico_programaciones(
    db: AsyncSession,
    fleet_ids: list[uuid.UUID] | None,
    plates: list[str] | None,
    date_from: date,
    date_to: date,
    limit: int = 500,
) -> list[dict]:
    """Rutinas ya ejecutadas en la ventana (por fecha planificada de ingreso).

    Mismo agrupado por ocurrencia de rutina que las próximas; incluye la OT de
    ejecución y su fecha. Orden descendente: lo más reciente primero.
    """
    scope = await _scoped_plates(db, fleet_ids, plates)
    if not scope:
        return []
    lo = datetime.combine(date_from, time.min)
    hi = datetime.combine(date_to + timedelta(days=1), time.min)
    rows = (
        await db.execute(
            text(
                "SELECT vehicle_code, status, task_name, routine_name, schedule_type, "
                "schedule_source, date_to_execute, wo_number, wo_execution_date, raw "
                "FROM cloudfleet_maintenance_schedules "
                "WHERE vehicle_code = ANY(:p) AND status = ANY(:st) "
                "AND date_to_execute >= :lo AND date_to_execute < :hi "
                "ORDER BY date_to_execute DESC LIMIT :lim"
            ),
            {
                "p": list(scope),
                "st": list(EXECUTED_SCHEDULE_STATUSES),
                "lo": lo,
                "hi": hi,
                "lim": limit,
            },
        )
    ).mappings().all()
    return _group_schedules(rows, scope, date.today())


# ---------------------------------------------------------------------------
# Confiabilidad (fallas, MTTR, MTBF)
# ---------------------------------------------------------------------------
def _avg(x: list[float]) -> float | None:
    return round(statistics.mean(x), 2) if x else None


def _med(x: list[float]) -> float | None:
    return round(statistics.median(x), 2) if x else None


def _confiabilidad_core(
    scope: dict[str, dict], wos: list[dict], date_from: date, date_to: date
) -> dict:
    p_start, p_end = _period_bounds_local(date_from, date_to)
    failures: list[dict] = []
    by_plate: dict[str, list[datetime]] = defaultdict(list)
    mttr: list[float] = []
    for w in wos:
        if w["vehicle_code"] not in scope:
            continue
        if not _is_failure(w["type"]):
            continue
        s = _to_local(w["start_date"] or w["cf_created_at"])
        if s is None or not (p_start <= s < p_end):
            continue
        failures.append(w)
        by_plate[w["vehicle_code"]].append(s)
        tc = _to_local(w["technical_completion_date"])
        if tc and tc > s:
            mttr.append((tc - s).total_seconds() / 3600.0)
    mtbf: list[float] = []
    for starts in by_plate.values():
        starts.sort()
        for a, b in pairwise(starts):
            d = (b - a).total_seconds() / 3600.0
            if d > 0:
                mtbf.append(d)
    return {
        "failureCount": len(failures),
        "vehiclesWithFailures": len([p for p, v in by_plate.items() if v]),
        "mttrHoursAvg": _avg(mttr),
        "mttrHoursMedian": _med(mttr),
        "mttrSample": len(mttr),
        "mtbfHoursAvg": _avg(mtbf),
        "mtbfHoursMedian": _med(mtbf),
        "mtbfSample": len(mtbf),
    }


async def get_confiabilidad(
    db: AsyncSession,
    fleet_ids: list[uuid.UUID] | None,
    plates: list[str] | None,
    date_from: date,
    date_to: date,
) -> dict:
    scope = await _scoped_plates(db, fleet_ids, plates)
    p_start, p_end = _period_bounds_local(date_from, date_to)
    wos = await _fetch_work_orders(db, set(scope), p_start, p_end)
    return _confiabilidad_core(scope, wos, date_from, date_to)


async def get_confiabilidad_timeseries(
    db: AsyncSession,
    fleet_ids: list[uuid.UUID] | None,
    plates: list[str] | None,
    date_from: date,
    date_to: date,
) -> list[dict]:
    scope = await _scoped_plates(db, fleet_ids, plates)
    p_start, p_end = _period_bounds_local(date_from, date_to)
    wos = await _fetch_work_orders(db, set(scope), p_start, p_end)
    months = _iter_months(date_from, date_to)
    fail_count: Counter[tuple[int, int]] = Counter()
    mttr: dict[tuple[int, int], list[float]] = defaultdict(list)
    mtbf: dict[tuple[int, int], list[float]] = defaultdict(list)
    failure_starts_by_plate: dict[str, list[datetime]] = defaultdict(list)
    for w in wos:
        if w["vehicle_code"] not in scope or not _is_failure(w["type"]):
            continue
        s = _to_local(w["start_date"] or w["cf_created_at"])
        if s is None:
            continue
        if not (p_start <= s < p_end):
            continue
        key = (s.year, s.month)
        fail_count[key] += 1
        failure_starts_by_plate[w["vehicle_code"]].append(s)
        tc = _to_local(w["technical_completion_date"])
        if tc and tc > s:
            mttr[key].append((tc - s).total_seconds() / 3600.0)
    for starts in failure_starts_by_plate.values():
        starts.sort()
        for previous, current in pairwise(starts):
            gap = (current - previous).total_seconds() / 3600.0
            if gap > 0:
                mtbf[(current.year, current.month)].append(gap)
    out: list[dict] = []
    for m0, _m1 in months:
        key = (m0.year, m0.month)
        out.append(
            {
                "month": f"{m0.year}-{m0.month:02d}",
                "label": _month_label(m0),
                "fallas": fail_count[key],
                # Sin fallas cerradas técnicamente no existe una muestra de
                # MTTR; no devolver 0 porque eso significaría reparación
                # instantánea y ocultaría que las OTs siguen abiertas.
                "mttr": _avg(mttr[key]),
                # Un solo fallo tampoco permite calcular MTBF: hace falta el
                # intervalo entre dos fallos consecutivos del mismo vehículo.
                "mtbf": _avg(mtbf[key]),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Ordenes
# ---------------------------------------------------------------------------
async def get_ordenes(
    db: AsyncSession,
    fleet_ids: list[uuid.UUID] | None,
    plates: list[str] | None,
    date_from: date,
    date_to: date,
) -> dict:
    scope = await _scoped_plates(db, fleet_ids, plates)
    p_start, p_end = _period_bounds_local(date_from, date_to)
    wos = await _fetch_work_orders(
        db, set(scope), p_start, p_end, include_statuses=ACTIVE_STATUSES
    )

    in_period: list[dict] = []
    cycles: list[float] = []
    final_lag: list[float] = []
    open_end = 0
    overdue_open = 0
    currently_open = 0
    currently_open_orders: list[dict[str, Any]] = []
    now_local = datetime.now(BOGOTA).replace(tzinfo=None)
    for w in wos:
        if w["vehicle_code"] not in scope:
            continue
        if w["status"] in ACTIVE_STATUSES:
            currently_open += 1
            opened_at = _to_local(
                w["start_date"] or w["workshop_date"] or w["cf_created_at"]
            )
            if opened_at is not None:
                # La OT sigue abierta AHORA, así que su plazo se juzga contra el
                # reloj y no contra el fin del periodo (que es el criterio de
                # `overdueOpen`, una foto histórica). Sin fecha estimada el
                # estado es desconocido, no "en plazo": va como None.
                est_finish = _to_local(w["estimated_finish_date"])
                currently_open_orders.append(
                    {
                        "number": w["number"],
                        "plate": w["vehicle_code"],
                        "fleet": scope[w["vehicle_code"]]["fleet_name"],
                        "type": w["type"],
                        "status": w["status"],
                        "openHours": round(
                            max(0.0, (now_local - opened_at).total_seconds() / 3600.0),
                            2,
                        ),
                        "estimatedFinishDate": (
                            est_finish.isoformat() if est_finish is not None else None
                        ),
                        "overdue": (est_finish < now_local) if est_finish else None,
                    }
                )
        s = _to_local(w["start_date"] or w["cf_created_at"])
        if s and p_start <= s < p_end:
            in_period.append(w)
            tc = _to_local(w["technical_completion_date"])
            if tc and tc > s:
                cycles.append((tc - s).total_seconds() / 3600.0)
            fc = _to_local(w["final_completion_date"])
            if tc and fc and fc > tc:
                final_lag.append((fc - tc).total_seconds() / 3600.0)
        if _is_open_at_end(w, p_end):
            open_end += 1
            est = _to_local(w["estimated_finish_date"])
            if est and est < p_end:
                overdue_open += 1

    by_status = Counter(w["status"] or "Sin estado" for w in in_period)
    by_type = Counter(w["type"] or "Sin tipo" for w in in_period)
    by_fleet = Counter(scope[w["vehicle_code"]]["fleet_name"] for w in in_period)
    by_cd = Counter(scope[w["vehicle_code"]]["cd"] for w in in_period)
    currently_open_orders.sort(key=lambda row: row["openHours"], reverse=True)

    return {
        "createdOrStarted": len(in_period),
        "openAtEnd": open_end,
        "overdueOpen": overdue_open,
        "currentlyOpen": currently_open,
        "currentlyOpenOrders": currently_open_orders,
        "avgTechnicalCycleHours": _avg(cycles),
        "medianTechnicalCycleHours": _med(cycles),
        "avgFinalClosureLagHours": _avg(final_lag),
        "byStatus": dict(by_status),
        "byType": dict(by_type),
        "byFleet": dict(by_fleet),
        "byCd": dict(by_cd),
    }


async def get_ordenes_timeseries(
    db: AsyncSession,
    fleet_ids: list[uuid.UUID] | None,
    plates: list[str] | None,
    date_from: date,
    date_to: date,
) -> list[dict[str, Any]]:
    """OTs iniciadas/creadas por mes, agrupadas por tipo (para barras apiladas).

    Mismo criterio "in period" que `get_ordenes` (start_date o cf_created_at
    dentro del rango): un conteo por tipo consistente con el `byType` del
    resumen.
    """
    scope = await _scoped_plates(db, fleet_ids, plates)
    p_start, p_end = _period_bounds_local(date_from, date_to)
    wos = await _fetch_work_orders(db, set(scope), p_start, p_end)
    buckets: dict[tuple[int, int], Counter[str]] = defaultdict(Counter)
    for w in wos:
        if w["vehicle_code"] not in scope:
            continue
        s = _to_local(w["start_date"] or w["cf_created_at"])
        if s is None or not (p_start <= s < p_end):
            continue
        buckets[(s.year, s.month)][w["type"] or "Sin tipo"] += 1

    out: list[dict[str, Any]] = []
    for m0, _m1 in _iter_months(date_from, date_to):
        c = buckets[(m0.year, m0.month)]
        out.append(
            {
                "month": f"{m0.year}-{m0.month:02d}",
                "label": _month_label(m0),
                "counts": dict(c),
            }
        )
    return out


async def get_ordenes_list(
    db: AsyncSession,
    fleet_ids: list[uuid.UUID] | None,
    plates: list[str] | None,
    date_from: date,
    date_to: date,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Detalle de OTs iniciadas/creadas en el periodo, más recientes primero.

    Mismo criterio "in period" que `get_ordenes`; trae campos que `_fetch_work_orders`
    no carga (costo, taller, centro de costo) porque solo se necesitan aquí.
    """
    scope = await _scoped_plates(db, fleet_ids, plates)
    if not scope:
        return []
    p_start, p_end = _period_bounds_local(date_from, date_to)
    rows = (
        await db.execute(
            text(
                "SELECT number, vehicle_code, status, type, reason, detected_issue, "
                "affects_vehicle_availability, warranty, start_date, "
                "technical_completion_date, final_completion_date, "
                "estimated_finish_date, cost_center, city, total_cost, cf_created_at, raw "
                "FROM cloudfleet_work_orders "
                "WHERE vehicle_code = ANY(:p) "
                "AND ("
                "  start_date < :end_dt"
                "  OR (start_date IS NULL AND cf_created_at < :end_dt)"
                ") "
                "AND ("
                "  start_date >= :start_dt"
                "  OR (start_date IS NULL AND cf_created_at >= :start_dt)"
                ") "
                "ORDER BY start_date DESC NULLS LAST, cf_created_at DESC NULLS LAST "
                "LIMIT :lim"
            ),
            {
                "p": list(scope),
                "start_dt": _local_bound_to_utc(p_start),
                "end_dt": _local_bound_to_utc(p_end),
                "lim": limit,
            },
        )
    ).mappings().all()

    out: list[dict[str, Any]] = []
    for r in rows:
        if r["vehicle_code"] not in scope:
            continue
        raw = r["raw"] or {}
        vendor = (raw.get("vendor") or {}).get("name")
        meta = scope[r["vehicle_code"]]
        start_local = _to_local(r["start_date"] or r["cf_created_at"])
        tc_local = _to_local(r["technical_completion_date"])
        fc_local = _to_local(r["final_completion_date"])
        est_local = _to_local(r["estimated_finish_date"])
        out.append(
            {
                "number": r["number"],
                "plate": r["vehicle_code"],
                "fleet": meta["fleet_name"],
                "cd": meta["cd"],
                "type": r["type"],
                "status": r["status"],
                "reason": r["reason"],
                "detectedIssue": r["detected_issue"],
                "startDate": start_local.isoformat() if start_local else None,
                "technicalCompletionDate": tc_local.isoformat() if tc_local else None,
                "finalCompletionDate": fc_local.isoformat() if fc_local else None,
                "estimatedFinishDate": est_local.isoformat() if est_local else None,
                "costCenter": r["cost_center"],
                "city": r["city"],
                "totalCost": float(r["total_cost"]) if r["total_cost"] is not None else None,
                "vendor": vendor,
                "affectsAvailability": r["affects_vehicle_availability"],
                "warranty": r["warranty"],
            }
        )
    return out


# ---------------------------------------------------------------------------
# Rankings (placa primaria; flota secundaria si el scope tiene >=2 flotas)
# ---------------------------------------------------------------------------
def _rank_group(
    punt: list[dict], compl: list[dict], fails: list[dict], mttr_rows: list[dict]
) -> dict:
    """Ordena las 5 listas de un ranking (peor puntualidad, menor cumplimiento,
    más fallas, MTTR más largo/corto). `name` = placa o flota según el llamador."""
    return {
        "peorPuntualidad": sorted(punt, key=lambda x: x["value"])[:10],
        "menorCumplimiento": sorted(compl, key=lambda x: x["value"])[:10],
        "masFallas": sorted(fails, key=lambda x: x["value"], reverse=True)[:10],
        "mttrMasLargo": sorted(mttr_rows, key=lambda x: x["value"], reverse=True)[:10],
        "mttrMasCorto": sorted(mttr_rows, key=lambda x: x["value"])[:10],
    }


def _failures_per_key(
    scope: dict[str, dict],
    wos: list[dict],
    date_from: date,
    date_to: date,
    keyfn,
) -> tuple[dict[str, int], dict[str, float], dict[str, int]]:
    """Un solo barrido de OTs → fallas y MTTR (suma/n) agregados por `keyfn(code)`."""
    p_start, p_end = _period_bounds_local(date_from, date_to)
    fail_count: dict[str, int] = defaultdict(int)
    mttr_sum: dict[str, float] = defaultdict(float)
    mttr_n: dict[str, int] = defaultdict(int)
    for w in wos:
        code = w["vehicle_code"]
        if code not in scope or not _is_failure(w["type"]):
            continue
        s = _to_local(w["start_date"] or w["cf_created_at"])
        if s is None or not (p_start <= s < p_end):
            continue
        k = keyfn(code)
        fail_count[k] += 1
        tc = _to_local(w["technical_completion_date"])
        if tc and tc > s:
            mttr_sum[k] += (tc - s).total_seconds() / 3600.0
            mttr_n[k] += 1
    return fail_count, mttr_sum, mttr_n


async def get_rankings(
    db: AsyncSession,
    fleet_ids: list[uuid.UUID] | None,
    plates: list[str] | None,
    date_from: date,
    date_to: date,
) -> dict:
    scope = await _scoped_plates(db, fleet_ids, plates)
    p_start, p_end = _period_bounds_local(date_from, date_to)
    wos = await _fetch_work_orders(db, set(scope), p_start, p_end)
    sch = await _fetch_schedules(db, set(scope), date_from, date_to)

    # --- Por placa (dimensión primaria: la propia flota del cliente) ---
    sch_by_plate: dict[str, list[dict]] = defaultdict(list)
    for r in sch:
        if r["vehicle_code"] in scope:
            sch_by_plate[r["vehicle_code"]].append(r)
    punt_p: list[dict] = []
    compl_p: list[dict] = []
    for code, rows in sch_by_plate.items():
        pv = _preventivo_core(rows)
        if pv["dueCount"] > 0:
            punt_p.append({"name": code, "value": pv["onTimePct"]})
            compl_p.append({"name": code, "value": pv["executionPct"]})
    fc_p, ms_p, mn_p = _failures_per_key(scope, wos, date_from, date_to, lambda c: c)
    fails_p = [{"name": c, "value": n} for c, n in fc_p.items()]
    mttr_p = [
        {"name": c, "value": round(ms_p[c] / mn_p[c], 2)} for c in mn_p if mn_p[c] > 0
    ]
    by_placa = _rank_group(punt_p, compl_p, fails_p, mttr_p)

    # --- Por flota (dimensión secundaria; el front la muestra si fleetCount>=2) ---
    fleets: dict[str, list[str]] = defaultdict(list)
    for code, meta in scope.items():
        fleets[meta["fleet_name"]].append(code)
    punt_f: list[dict] = []
    compl_f: list[dict] = []
    for fname, codes in fleets.items():
        pv = _preventivo_core([r for r in sch if r["vehicle_code"] in set(codes)])
        if pv["dueCount"] > 0:
            punt_f.append({"name": fname, "value": pv["onTimePct"]})
            compl_f.append({"name": fname, "value": pv["executionPct"]})
    fc_f, ms_f, mn_f = _failures_per_key(
        scope, wos, date_from, date_to, lambda c: scope[c]["fleet_name"]
    )
    fails_f = [{"name": k, "value": n} for k, n in fc_f.items()]
    mttr_f = [
        {"name": k, "value": round(ms_f[k] / mn_f[k], 2)} for k in mn_f if mn_f[k] > 0
    ]
    by_fleet = _rank_group(punt_f, compl_f, fails_f, mttr_f)

    return {
        "byPlaca": by_placa,
        "byFleet": by_fleet,
        "fleetCount": len(fleets),
    }
