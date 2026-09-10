"""Proyección de tiempos en taller a partir de eventos de etiquetas."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.mantenimiento_service import (
    _local_bound_to_utc,
    _month_label,
    _period_bounds_local,
    _scoped_plates,
)

BOGOTA = ZoneInfo("America/Bogota")

STAGE_DEFINITIONS: tuple[dict[str, str], ...] = (
    {"key": "recepcion", "label": "Recepción e ingreso", "shortLabel": "Recepción"},
    {"key": "diagnostico", "label": "Diagnóstico", "shortLabel": "Diagnóstico"},
    {
        "key": "autorizacion",
        "label": "Espera de autorización",
        "shortLabel": "Autorización",
    },
    {"key": "repuestos", "label": "Espera de repuestos", "shortLabel": "Repuestos"},
    {"key": "reparacion", "label": "Reparación", "shortLabel": "Reparación"},
    {"key": "calidad", "label": "Pruebas y control de calidad", "shortLabel": "Calidad"},
    {"key": "entrega", "label": "Listo para entrega", "shortLabel": "Entrega"},
)
STAGE_KEYS = tuple(stage["key"] for stage in STAGE_DEFINITIONS)

# v1: solo equivalencias operativas explícitas. ``entrega`` no tiene una
# etiqueta CloudFleet equivalente; el tiempo posterior queda no clasificado.
LABEL_STAGE_MAP: dict[int, str] = {
    1: "recepcion",
    2: "recepcion",
    3: "diagnostico",
    4: "diagnostico",
    5: "autorizacion",
    6: "autorizacion",
    7: "autorizacion",
    8: "repuestos",
    9: "reparacion",
    10: "calidad",
}
PRECISE_QUALITIES = {"explicit", "live_window"}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _local_display(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(BOGOTA).replace(tzinfo=None).isoformat(sep=" ", timespec="minutes")


def _nested_name(value: Any) -> str | None:
    if isinstance(value, Mapping):
        candidate = value.get("name") or value.get("code")
        return str(candidate) if candidate is not None else None
    return str(value) if isinstance(value, str) and value.strip() else None


def _order_context(order: Mapping[str, Any], scope: Mapping[str, Any]) -> dict[str, Any]:
    raw = order.get("raw") if isinstance(order.get("raw"), Mapping) else {}
    vendor = _nested_name(raw.get("vendor"))
    city = order.get("city") or scope.get("cd")
    vehicle_parts = [
        str(value)
        for value in (
            order.get("brand_name"),
            order.get("line_name"),
            order.get("type_name"),
        )
        if value
    ]
    return {
        "site": vendor or city or "Sin taller",
        "vehicle": " ".join(vehicle_parts) or str(order.get("vehicle_code") or ""),
    }


def _cycle_bounds(order: Mapping[str, Any], now: datetime) -> tuple[datetime, datetime]:
    start = _aware(
        order.get("workshop_date") or order.get("start_date") or order.get("cf_created_at")
    )
    if start is None:
        start = now
    end = _aware(order.get("final_completion_date") or order.get("technical_completion_date"))
    end = end or now
    return start, max(start, end)


def _segment_quality(start: Mapping[str, Any], end: Mapping[str, Any] | None) -> str:
    start_quality = start.get("event_time_quality")
    end_quality = end.get("event_time_quality") if end else None
    if end is None:
        return str(start_quality or "mixed")
    if start_quality == end_quality and start_quality in {
        "explicit",
        "live_window",
    }:
        return str(start_quality)
    if start_quality == end_quality and start_quality == "historical_date":
        return "historical_date"
    return "mixed"


def _effective_event_at(
    event: Mapping[str, Any],
    start: datetime,
    end: datetime,
) -> datetime | None:
    """Place one event inside the cycle, or reject it.

    CloudFleet often supplies ``trackingDate`` as a date without a time, so the
    baseline event lands at 00:00 Bogotá. An order opened at 08:30 that same day
    would then lose every label of its first day. The event does belong to this
    order — it was fetched per order — only its time is unknown, so it is
    anchored at the opening instead of dropped.

    A *precise* timestamp (explicit comment or live observation window) outside
    the cycle is trusted and rejected: it belongs to another visit.
    """
    event_at = _aware(event.get("event_at"))
    if event_at is None:
        return None
    if start <= event_at < end:
        return event_at
    if event_at < start and event.get("event_time_quality") not in PRECISE_QUALITIES:
        return start
    return None


def build_workshop_order(
    order: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    *,
    scope: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Build one order projection without database or network access."""
    start, end = _cycle_bounds(order, now)
    total_hours = max(0.0, (end - start).total_seconds() / 3600.0)
    stage_hours = dict.fromkeys(STAGE_KEYS, 0.0)
    valid_events: list[dict[str, Any]] = []
    out_of_cycle = 0
    for event in events:
        if event.get("label_id") is None:
            continue
        effective_at = _effective_event_at(event, start, end)
        if effective_at is None:
            if _aware(event.get("event_at")) is not None:
                out_of_cycle += 1
            continue
        valid_events.append({**event, "_at": effective_at})
    valid_events.sort(key=lambda event: (event["_at"], str(event.get("tracking_key") or "")))

    transitions: list[dict[str, Any]] = []
    for event in valid_events:
        if transitions and transitions[-1].get("label_id") == event.get("label_id"):
            continue
        transitions.append(event)

    segments: list[dict[str, Any]] = []
    classified_hours = 0.0
    precise_hours = 0.0
    inferred_hours = 0.0

    # Tramo de entrada: entre la apertura de la OT y su primera etiqueta nadie
    # etiquetó, pero el vehículo ya estaba en el taller. Se imputa a Recepción
    # y se marca `inferred` para no mezclar inferencia con observación.
    # Solo aplica si la OT llegó a entrar al flujo etiquetado: una OT que nunca
    # se etiquetó queda entera sin clasificar, que es la verdad.
    if transitions:
        lead_end = min(transitions[0]["_at"], end)
        if lead_end > start:
            hours = round((lead_end - start).total_seconds() / 3600.0, 2)
            stage_hours["recepcion"] += hours
            classified_hours += hours
            inferred_hours += hours
            segments.append(
                {
                    "labelId": None,
                    "labelName": "Recepción (inferido)",
                    "stageKey": "recepcion",
                    "stageLabel": "Recepción e ingreso",
                    "start": _local_display(start),
                    "end": _local_display(lead_end),
                    "hours": hours,
                    "open": False,
                    "timeQuality": "inferred_lead_in",
                    "precise": False,
                    "inferred": True,
                }
            )

    for index, event in enumerate(transitions):
        event_at = event["_at"]
        next_event = transitions[index + 1] if index + 1 < len(transitions) else None
        next_at = next_event["_at"] if next_event else end
        segment_start = max(start, event_at)
        segment_end = min(end, next_at or end)
        if segment_start >= segment_end:
            continue
        hours = round((segment_end - segment_start).total_seconds() / 3600.0, 2)
        label_id = int(event["label_id"])
        stage_key = LABEL_STAGE_MAP.get(label_id)
        quality = _segment_quality(event, next_event)
        precise = bool(next_event and quality in PRECISE_QUALITIES)
        if stage_key:
            stage_hours[stage_key] += hours
            classified_hours += hours
            if precise:
                precise_hours += hours
        segments.append(
            {
                "labelId": label_id,
                "labelName": event.get("label_name") or f"Etiqueta {label_id}",
                "stageKey": stage_key,
                "stageLabel": next(
                    (stage["label"] for stage in STAGE_DEFINITIONS if stage["key"] == stage_key),
                    None,
                ),
                "start": _local_display(segment_start),
                "end": _local_display(segment_end) if next_event else None,
                "hours": hours,
                "open": next_event is None,
                "timeQuality": quality,
                "precise": precise,
                "inferred": False,
            }
        )

    raw_classified = classified_hours
    classified_hours = min(total_hours, classified_hours)
    if raw_classified > total_hours and raw_classified > 0:
        # Las etapas se escalan junto con `classified_hours`; si no, las barras
        # apiladas suman más del 100% del ciclo.
        factor = total_hours / raw_classified
        stage_hours = {key: value * factor for key, value in stage_hours.items()}
        inferred_hours *= factor
    unclassified_hours = max(0.0, round(total_hours - classified_hours, 2))
    coverage_pct = round(classified_hours / total_hours * 100, 2) if total_hours else 0.0
    current = transitions[-1] if transitions else None
    context = _order_context(order, scope)
    return {
        "number": int(order["number"]),
        "plate": str(order["vehicle_code"]),
        "fleet": scope["fleet_name"],
        "cd": scope["cd"],
        "site": context["site"],
        "vehicle": context["vehicle"],
        "status": order.get("status"),
        "openedAt": _local_display(start),
        "closedAt": (
            _local_display(end)
            if order.get("technical_completion_date") or order.get("final_completion_date")
            else None
        ),
        "totalHours": round(total_hours, 2),
        "classifiedHours": round(classified_hours, 2),
        "unclassifiedHours": unclassified_hours,
        "coveragePct": coverage_pct,
        "preciseHours": round(precise_hours, 2),
        "inferredHours": round(inferred_hours, 2),
        "outOfCycleEventCount": out_of_cycle,
        "stages": {key: round(value, 2) for key, value in stage_hours.items()},
        "trackingCount": len(events),
        "labelEventCount": len(valid_events),
        "currentLabelId": int(current["label_id"]) if current else None,
        "currentLabel": current.get("label_name") if current else None,
        "currentSince": _local_display(_aware(current.get("event_at"))) if current else None,
        "segments": segments,
    }


def build_workshop_projection(
    orders: Sequence[Mapping[str, Any]],
    events_by_order: Mapping[int, Sequence[Mapping[str, Any]]],
    *,
    scopes: Mapping[str, Mapping[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    rows = [
        build_workshop_order(
            order,
            events_by_order.get(int(order["number"]), []),
            scope=scopes[str(order["vehicle_code"])],
            now=now,
        )
        for order in orders
        if str(order["vehicle_code"]) in scopes
    ]
    stage_totals = {key: round(sum(row["stages"][key] for row in rows), 2) for key in STAGE_KEYS}
    monthly_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        month = str(row["openedAt"] or "")[:7]
        if month:
            monthly_rows[month].append(row)
    monthly: list[dict[str, Any]] = []
    for month, month_orders in sorted(monthly_rows.items()):
        values = {
            key: round(sum(row["stages"][key] for row in month_orders) / len(month_orders), 2)
            for key in STAGE_KEYS
        }
        values.update(
            {
                "month": month,
                "label": _month_label(date.fromisoformat(f"{month}-01")),
                "orders": len(month_orders),
                "total": round(
                    sum(row["totalHours"] for row in month_orders) / len(month_orders), 2
                ),
            }
        )
        monthly.append(values)
    total_hours = round(sum(row["totalHours"] for row in rows), 2)
    classified_hours = round(sum(row["classifiedHours"] for row in rows), 2)
    return {
        "stageTotals": stage_totals,
        "monthly": monthly,
        "totalOrders": len(rows),
        "ordersWithLabels": sum(1 for row in rows if row["labelEventCount"]),
        "totalHours": total_hours,
        "classifiedHours": classified_hours,
        "unclassifiedHours": round(total_hours - classified_hours, 2),
        "coveragePct": round(classified_hours / total_hours * 100, 2) if total_hours else 0.0,
        "preciseHours": round(sum(row["preciseHours"] for row in rows), 2),
        "inferredHours": round(sum(row["inferredHours"] for row in rows), 2),
        "outOfCycleEventCount": sum(row["outOfCycleEventCount"] for row in rows),
        "items": rows,
    }


async def get_tiempos_taller(
    db: AsyncSession,
    fleet_ids: list[Any] | None,
    plates: list[str] | None,
    date_from: date,
    date_to: date,
    *,
    order_number: int | None = None,
    limit: int = 500,
    offset: int = 0,
) -> tuple[dict[str, Any], int]:
    scopes = await _scoped_plates(db, fleet_ids, plates)
    if not scopes:
        projection = build_workshop_projection([], {}, scopes={}, now=datetime.now(UTC))
        return projection, 0

    p_start, p_end = _period_bounds_local(date_from, date_to)
    start_dt = _local_bound_to_utc(p_start)
    end_dt = _local_bound_to_utc(p_end)
    order_number_filter = "AND wo.number = :order_number " if order_number is not None else ""
    params = {
        "plates": list(scopes),
        "start_dt": start_dt,
        "end_dt": end_dt,
        "limit": limit,
        "offset": offset,
    }
    if order_number is not None:
        params["order_number"] = order_number
    order_rows = (
        (
            await db.execute(
                text(
                    "SELECT wo.number, wo.vehicle_code, wo.status, wo.start_date, "
                    "wo.workshop_date, wo.technical_completion_date, wo.final_completion_date, "
                    "wo.cf_created_at, wo.city, wo.raw, "
                    "cv.brand_name, cv.line_name, cv.type_name "
                    "FROM cloudfleet_work_orders wo "
                    "LEFT JOIN cloudfleet_vehicles cv ON cv.code = wo.vehicle_code "
                    "WHERE wo.vehicle_code = ANY(:plates) "
                    "AND COALESCE(wo.start_date, wo.workshop_date, wo.cf_created_at) >= :start_dt "
                    "AND COALESCE(wo.start_date, wo.workshop_date, wo.cf_created_at) < :end_dt "
                    + order_number_filter
                    + "AND LOWER(BTRIM(COALESCE(wo.status, ''))) NOT IN "
                    "('voided', 'void', 'anulada', 'anulado', 'cancelada', 'cancelado', 'cancelled', 'canceled') "
                    "ORDER BY COALESCE(wo.start_date, wo.workshop_date, wo.cf_created_at) "
                    "DESC NULLS LAST, wo.number DESC "
                    "LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    total = int(
        (
            await db.execute(
                text(
                    "SELECT count(*) FROM cloudfleet_work_orders wo "
                    "WHERE wo.vehicle_code = ANY(:plates) "
                    "AND COALESCE(wo.start_date, wo.workshop_date, wo.cf_created_at) >= :start_dt "
                    "AND COALESCE(wo.start_date, wo.workshop_date, wo.cf_created_at) < :end_dt "
                    + order_number_filter
                    + "AND LOWER(BTRIM(COALESCE(wo.status, ''))) NOT IN "
                    "('voided', 'void', 'anulada', 'anulado', 'cancelada', 'cancelado', 'cancelled', 'canceled')"
                ),
                {
                    "plates": list(scopes),
                    "start_dt": start_dt,
                    "end_dt": end_dt,
                    **({"order_number": order_number} if order_number is not None else {}),
                },
            )
        ).scalar_one()
    )
    orders = [dict(row) for row in order_rows]
    if not orders:
        projection = build_workshop_projection([], {}, scopes=scopes, now=datetime.now(UTC))
        return projection, total

    numbers = [int(order["number"]) for order in orders]
    event_rows = (
        (
            await db.execute(
                text(
                    "SELECT work_order_number, tracking_key, label_id, label_name, event_at, "
                    "event_time_quality FROM cloudfleet_tracking_events "
                    "WHERE work_order_number = ANY(:numbers) "
                    "ORDER BY work_order_number, event_at NULLS LAST, tracking_key"
                ),
                {"numbers": numbers},
            )
        )
        .mappings()
        .all()
    )
    events_by_order: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in event_rows:
        events_by_order[int(event["work_order_number"])].append(dict(event))
    projection = build_workshop_projection(
        orders,
        events_by_order,
        scopes=scopes,
        now=datetime.now(UTC),
    )
    return projection, total
