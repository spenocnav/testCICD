from __future__ import annotations

from datetime import UTC, datetime

from app.services.tiempos_taller_service import (
    build_workshop_order,
    build_workshop_projection,
)

SCOPE = {"fleet_name": "Flota Uno", "cd": "CD Bogotá"}


def _order(**overrides: object) -> dict:
    value = {
        "number": 1001,
        "vehicle_code": "ABC123",
        "status": "closed",
        "start_date": datetime(2026, 8, 1, 5, tzinfo=UTC),
        "workshop_date": None,
        "technical_completion_date": datetime(2026, 8, 2, 5, tzinfo=UTC),
        "final_completion_date": datetime(2026, 8, 2, 7, tzinfo=UTC),
        "cf_created_at": None,
        "city": "Bogotá",
        "raw": {"vendor": {"name": "Taller Uno"}},
        "brand_name": "Marca",
        "line_name": "Linea",
        "type_name": "Camión",
    }
    value.update(overrides)
    return value


def test_build_workshop_order_infers_lead_in_and_merges_reaffirmation() -> None:
    row = build_workshop_order(
        _order(),
        [
            {
                "tracking_key": "id:1",
                "label_id": 3,
                "label_name": "En diagnóstico",
                "event_at": datetime(2026, 8, 1, 6, tzinfo=UTC),
                "event_time_quality": "live_window",
            },
            {
                "tracking_key": "id:2",
                "label_id": 3,
                "label_name": "En diagnóstico",
                "event_at": datetime(2026, 8, 1, 7, tzinfo=UTC),
                "event_time_quality": "live_window",
            },
            {
                "tracking_key": "id:3",
                "label_id": 8,
                "label_name": "Pendiente de repuestos",
                "event_at": datetime(2026, 8, 1, 15, tzinfo=UTC),
                "event_time_quality": "explicit",
            },
        ],
        scope=SCOPE,
        now=datetime(2026, 8, 3, tzinfo=UTC),
    )

    assert row["totalHours"] == 26.0
    assert row["stages"]["diagnostico"] == 9.0
    assert row["stages"]["repuestos"] == 16.0
    # La hora entre la apertura (05:00) y la primera etiqueta (06:00) ya no
    # queda sin clasificar: se imputa a Recepción y se declara inferida.
    assert row["stages"]["recepcion"] == 1.0
    assert row["inferredHours"] == 1.0
    assert row["unclassifiedHours"] == 0.0
    assert row["coveragePct"] == 100.0
    # Dos tramos observados (diagnóstico, repuestos) + el de entrada inferido.
    assert len(row["segments"]) == 3
    assert sum(1 for segment in row["segments"] if segment["inferred"]) == 1


def test_build_workshop_order_without_events_is_not_falsely_classified() -> None:
    row = build_workshop_order(
        _order(number=1002, final_completion_date=None, status="opened"),
        [],
        scope=SCOPE,
        now=datetime(2026, 8, 1, 12, tzinfo=UTC),
    )

    assert row["labelEventCount"] == 0
    assert row["classifiedHours"] == 0.0
    assert row["coveragePct"] == 0.0
    assert row["unclassifiedHours"] == row["totalHours"]


def test_build_workshop_order_ignores_events_after_cycle_end() -> None:
    row = build_workshop_order(
        _order(),
        [
            {
                "tracking_key": "id:late",
                "label_id": 9,
                "label_name": "En intervención",
                "event_at": datetime(2026, 8, 2, 8, tzinfo=UTC),
                "event_time_quality": "live_window",
            }
        ],
        scope=SCOPE,
        now=datetime(2026, 8, 3, tzinfo=UTC),
    )

    assert row["labelEventCount"] == 0
    assert row["currentLabel"] is None
    assert row["classifiedHours"] == 0.0


def test_build_workshop_projection_groups_monthly_averages() -> None:
    projection = build_workshop_projection(
        [_order(), _order(number=1003, start_date=datetime(2026, 8, 3, 5, tzinfo=UTC))],
        {},
        scopes={"ABC123": SCOPE},
        now=datetime(2026, 8, 10, tzinfo=UTC),
    )

    assert projection["totalOrders"] == 2
    assert projection["monthly"][0]["month"] == "2026-08"
    assert projection["monthly"][0]["orders"] == 2


def test_date_only_baseline_before_opening_is_anchored_not_dropped() -> None:
    """CloudFleet entrega `trackingDate` sin hora: el baseline cae a 00:00 Bogotá.

    Una OT abierta a las 08:30 de ese mismo día perdía todas las etiquetas de su
    primer día. El evento sí es de esta OT (se pidió por OT); lo desconocido es
    la hora, así que se ancla en la apertura.
    """
    order = _order(
        start_date=datetime(2026, 8, 1, 13, 30, tzinfo=UTC),  # 08:30 Bogotá
        technical_completion_date=datetime(2026, 8, 2, 13, 30, tzinfo=UTC),
        final_completion_date=None,
    )
    events = [
        {
            "label_id": 3,
            "label_name": "En diagnóstico",
            "event_at": datetime(2026, 8, 1, 5, tzinfo=UTC),  # 00:00 Bogotá
            "event_time_quality": "historical_date",
            "tracking_key": "id:1",
        }
    ]

    row = build_workshop_order(order, events, scope=SCOPE, now=datetime(2026, 8, 3, tzinfo=UTC))

    assert row["labelEventCount"] == 1
    assert row["outOfCycleEventCount"] == 0
    assert row["stages"]["diagnostico"] > 0


def test_precise_event_outside_the_cycle_is_still_rejected() -> None:
    """Si la hora es confiable y cae fuera del ciclo, es de otra visita."""
    order = _order(
        start_date=datetime(2026, 8, 1, 13, 30, tzinfo=UTC),
        technical_completion_date=datetime(2026, 8, 2, 13, 30, tzinfo=UTC),
        final_completion_date=None,
    )
    events = [
        {
            "label_id": 3,
            "label_name": "En diagnóstico",
            "event_at": datetime(2026, 7, 20, 12, tzinfo=UTC),
            "event_time_quality": "explicit",
            "tracking_key": "id:1",
        }
    ]

    row = build_workshop_order(order, events, scope=SCOPE, now=datetime(2026, 8, 3, tzinfo=UTC))

    assert row["labelEventCount"] == 0
    assert row["outOfCycleEventCount"] == 1


def test_lead_in_before_the_first_label_counts_as_inferred_reception() -> None:
    order = _order(
        start_date=datetime(2026, 8, 1, 12, tzinfo=UTC),
        technical_completion_date=datetime(2026, 8, 1, 20, tzinfo=UTC),
        final_completion_date=None,
    )
    events = [
        {
            "label_id": 3,
            "label_name": "En diagnóstico",
            "event_at": datetime(2026, 8, 1, 14, tzinfo=UTC),
            "event_time_quality": "live_window",
            "tracking_key": "id:1",
        }
    ]

    row = build_workshop_order(order, events, scope=SCOPE, now=datetime(2026, 8, 3, tzinfo=UTC))

    # 12:00 -> 14:00 se imputa a recepción, marcado como inferido.
    assert row["stages"]["recepcion"] == 2.0
    assert row["inferredHours"] == 2.0
    assert row["unclassifiedHours"] == 0.0
    lead = next(segment for segment in row["segments"] if segment["inferred"])
    assert lead["labelId"] is None
    assert lead["stageKey"] == "recepcion"


def test_an_order_that_was_never_labelled_stays_fully_unclassified() -> None:
    """El caso que domina hoy el informe.

    Sin ninguna etiqueta no se infiere nada: imputar el ciclo entero a Recepción
    convertiría "no sabemos" en un dato inventado.
    """
    order = _order(
        start_date=datetime(2026, 8, 1, 12, tzinfo=UTC),
        technical_completion_date=datetime(2026, 8, 1, 20, tzinfo=UTC),
        final_completion_date=None,
    )

    row = build_workshop_order(order, [], scope=SCOPE, now=datetime(2026, 8, 3, tzinfo=UTC))

    assert row["stages"]["recepcion"] == 0.0
    assert row["inferredHours"] == 0.0
    assert row["classifiedHours"] == 0.0
    assert row["unclassifiedHours"] == row["totalHours"] > 0


def test_stage_hours_never_exceed_the_cycle() -> None:
    """`classifiedHours` ya se topaba a `totalHours`, las etapas no: las barras
    apiladas podían pasar del 100%."""
    order = _order(
        start_date=datetime(2026, 8, 1, 12, tzinfo=UTC),
        technical_completion_date=datetime(2026, 8, 1, 14, tzinfo=UTC),
        final_completion_date=None,
    )
    events = [
        {
            "label_id": 9,
            "label_name": "En intervención",
            "event_at": datetime(2026, 8, 1, 13, tzinfo=UTC),
            "event_time_quality": "live_window",
            "tracking_key": "id:1",
        }
    ]

    row = build_workshop_order(order, events, scope=SCOPE, now=datetime(2026, 8, 3, tzinfo=UTC))

    assert sum(row["stages"].values()) <= row["totalHours"] + 0.01
