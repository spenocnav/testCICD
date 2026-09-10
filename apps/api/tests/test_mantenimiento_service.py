"""Tests del motor KPI de mantenimiento (app/services/mantenimiento_service.py).

Las funciones núcleo (`_availability_core`, `_preventivo_core`,
`_confiabilidad_core`, helpers) son puras: se prueban con datos sintéticos sin
red ni DB. `_scoped_plates` es integración (requiere PostgreSQL) y va marcada.

Regla de disponibilidad bajo prueba (del mockup KpiCloudfleet):
- intervalo = startDate|workshopDate → technicalCompletionDate;
- OT abierta (opened/onTechnicalCompletion) corta al fin del periodo;
- intervalos se recortan al periodo y se FUSIONAN por placa (sin doble conteo);
- proyecto = solo OTs con affects_vehicle_availability.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.cloudfleet_sync_service import parse_cf_datetime
from app.services.mantenimiento_service import (
    _availability_core,
    _availability_monthly_totals,
    _confiabilidad_core,
    _fetch_work_orders,
    _group_schedules,
    _is_failure,
    _is_open_at_end,
    _iter_months,
    _merge_intervals,
    _preventivo_core,
    _scoped_plates,
    _to_local,
    get_ordenes,
)

UTC = ZoneInfo("UTC")


# ---------------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------------
def test_is_failure_clasifica_por_tipo():
    # Tipos "no falla" del mockup
    for t in ("Programado", "RTM", "Suministros", "Back-Up", "Alistamiento"):
        assert _is_failure(t) is False
    # Todo lo demás cuenta como falla/correctivo, incluido tipo vacío
    assert _is_failure("Correctivo") is True
    assert _is_failure("Desvare") is True
    assert _is_failure(None) is True


def test_to_local_convierte_utc_a_bogota():
    # 15:00 UTC == 10:00 Bogotá (UTC-5, sin DST)
    dt = datetime(2026, 5, 1, 15, 0, tzinfo=UTC)
    loc = _to_local(dt)
    assert loc == datetime(2026, 5, 1, 10, 0)
    assert loc.tzinfo is None
    # naive se asume UTC
    assert _to_local(datetime(2026, 5, 1, 15, 0)) == datetime(2026, 5, 1, 10, 0)
    assert _to_local(None) is None


def test_merge_intervals_fusiona_solapados_y_descarta_invertidos():
    d = datetime
    merged = _merge_intervals(
        [
            (d(2026, 5, 1), d(2026, 5, 3)),
            (d(2026, 5, 2), d(2026, 5, 4)),  # solapa con el anterior
            (d(2026, 5, 10), d(2026, 5, 11)),  # separado
            (d(2026, 5, 9), d(2026, 5, 8)),  # invertido: se descarta
        ]
    )
    assert merged == [
        (d(2026, 5, 1), d(2026, 5, 4)),
        (d(2026, 5, 10), d(2026, 5, 11)),
    ]


def test_iter_months_recorta_al_rango():
    months = _iter_months(date(2026, 1, 15), date(2026, 3, 10))
    assert months == [
        (date(2026, 1, 15), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 28)),
        (date(2026, 3, 1), date(2026, 3, 10)),
    ]


def test_parse_cf_datetime_formatos():
    assert parse_cf_datetime("2026-05-01T10:00:00Z") == datetime(
        2026, 5, 1, 10, 0, tzinfo=UTC
    )
    # microsegundos largos: Python los trunca a 6
    assert parse_cf_datetime("2026-05-01T10:00:00.123456789Z").microsecond == 123456
    assert parse_cf_datetime({"date": "2026-05-01T10:00:00Z"}) == datetime(
        2026, 5, 1, 10, 0, tzinfo=UTC
    )
    assert parse_cf_datetime(None) is None
    assert parse_cf_datetime("") is None
    assert parse_cf_datetime("no-es-fecha") is None


# ---------------------------------------------------------------------------
# Disponibilidad
# ---------------------------------------------------------------------------
def _wo(**kw) -> dict:
    """OT sintética con defaults; fechas en UTC (como la réplica)."""
    base = {
        "number": 1,
        "vehicle_code": "AAA111",
        "status": "closed",
        "type": "Correctivo",
        "reason": None,
        "detected_issue": None,
        "affects_vehicle_availability": True,
        "start_date": None,
        "workshop_date": None,
        "technical_completion_date": None,
        "final_completion_date": None,
        "estimated_finish_date": None,
        "cf_created_at": None,
    }
    base.update(kw)
    return base


_SCOPE = {"AAA111": {"fleet_id": None, "fleet_name": "F1", "cd": "CD1"}}


def test_availability_ot_cerrada_simple():
    # 10:00→20:00 Bogotá el 2026-05-10 = 10h no disponibles
    wos = [
        _wo(
            number=10,
            start_date=datetime(2026, 5, 10, 15, 0, tzinfo=UTC),  # 10:00 local
            technical_completion_date=datetime(2026, 5, 11, 1, 0, tzinfo=UTC),  # 20:00 local
        )
    ]
    c = _availability_core(_SCOPE, wos, date(2026, 5, 1), date(2026, 5, 31))
    assert c["mec_total"] == pytest.approx(10.0)
    assert c["mec_aff"] == 1
    assert c["should"] == pytest.approx(31 * 24.0)
    assert c["pct_mec"] == pytest.approx(round((744 - 10) / 744 * 100, 2))


def test_availability_ots_solapadas_no_doble_conteo():
    # Dos OTs solapadas 10:00→20:00 y 15:00→22:00 => merge = 12h (no 17h)
    wos = [
        _wo(
            number=1,
            start_date=datetime(2026, 5, 10, 15, 0, tzinfo=UTC),
            technical_completion_date=datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
        ),
        _wo(
            number=2,
            start_date=datetime(2026, 5, 10, 20, 0, tzinfo=UTC),
            technical_completion_date=datetime(2026, 5, 11, 3, 0, tzinfo=UTC),
        ),
    ]
    c = _availability_core(_SCOPE, wos, date(2026, 5, 1), date(2026, 5, 31))
    assert c["mec_total"] == pytest.approx(12.0)
    assert c["mec_aff"] == 1
    # pero sí cuenta 2 OTs con solape en el periodo
    assert c["order_count"] == 2


def test_availability_ot_abierta_corta_al_fin_del_periodo():
    # Abierta desde el 30 de mayo 00:00 local, sin cierre: corre hasta fin de mes
    wos = [
        _wo(
            number=3,
            status="opened",
            start_date=datetime(2026, 5, 30, 5, 0, tzinfo=UTC),  # 00:00 local
            technical_completion_date=None,
        )
    ]
    c = _availability_core(_SCOPE, wos, date(2026, 5, 1), date(2026, 5, 31))
    assert c["mec_total"] == pytest.approx(48.0)  # 30 y 31 de mayo completos


def test_availability_ot_cerrada_sin_cierre_tecnico_se_ignora():
    # status closed pero sin technical_completion_date: no genera intervalo
    wos = [
        _wo(
            number=4,
            status="closed",
            start_date=datetime(2026, 5, 10, 15, 0, tzinfo=UTC),
            technical_completion_date=None,
        )
    ]
    c = _availability_core(_SCOPE, wos, date(2026, 5, 1), date(2026, 5, 31))
    assert c["mec_total"] == 0.0
    assert c["order_count"] == 0


def test_availability_cruce_de_mes_recorta():
    # OT del 30-may 00:00 al 2-jun 00:00 local; en mayo solo cuentan 48h
    wos = [
        _wo(
            number=5,
            start_date=datetime(2026, 5, 30, 5, 0, tzinfo=UTC),
            technical_completion_date=datetime(2026, 6, 2, 5, 0, tzinfo=UTC),
        )
    ]
    mayo = _availability_core(_SCOPE, wos, date(2026, 5, 1), date(2026, 5, 31))
    junio = _availability_core(_SCOPE, wos, date(2026, 6, 1), date(2026, 6, 30))
    assert mayo["mec_total"] == pytest.approx(48.0)
    assert junio["mec_total"] == pytest.approx(24.0)  # 1-jun completo


def test_availability_modo_proyecto_filtra_por_flag():
    wos = [
        _wo(
            number=6,
            affects_vehicle_availability=False,
            start_date=datetime(2026, 5, 10, 15, 0, tzinfo=UTC),
            technical_completion_date=datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
        )
    ]
    c = _availability_core(_SCOPE, wos, date(2026, 5, 1), date(2026, 5, 31))
    assert c["mec_total"] == pytest.approx(10.0)  # mecánica cuenta todo
    assert c["proj_total"] == 0.0  # proyecto exige el flag


def test_availability_placa_fuera_de_scope_se_ignora():
    wos = [
        _wo(
            number=7,
            vehicle_code="ZZZ999",  # no está en scope
            start_date=datetime(2026, 5, 10, 15, 0, tzinfo=UTC),
            technical_completion_date=datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
        )
    ]
    c = _availability_core(_SCOPE, wos, date(2026, 5, 1), date(2026, 5, 31))
    assert c["mec_total"] == 0.0


def test_availability_scope_vacio_devuelve_100pct():
    c = _availability_core({}, [], date(2026, 5, 1), date(2026, 5, 31))
    assert c["should"] == 0.0
    assert c["pct_mec"] == 100.0


# ---------------------------------------------------------------------------
# Preventivo
# ---------------------------------------------------------------------------
def test_preventivo_core_estados_mixtos():
    rows = [
        {"status": "Ejecutada a tiempo"},
        {"status": "Ejecutada a tiempo"},
        {"status": "Ejecutada vencida"},
        {"status": "Vencido"},
        {"status": "Próximo"},
        {"status": "A tiempo"},
        {"status": None},  # → "Sin estado"
    ]
    c = _preventivo_core(rows)
    assert c["dueCount"] == 7
    assert c["executedCount"] == 3
    assert c["onTimeCount"] == 2
    assert c["lateExecuted"] == 1
    assert c["pending"] == 3  # Vencido + Próximo + A tiempo
    assert c["overduePending"] == 1
    assert c["executionPct"] == pytest.approx(round(3 / 7 * 100, 2))
    assert c["onTimePct"] == pytest.approx(round(2 / 7 * 100, 2))
    assert c["byStatus"]["Sin estado"] == 1


def test_preventivo_core_vacio_no_divide_por_cero():
    c = _preventivo_core([])
    assert c["dueCount"] == 0
    assert c["executionPct"] == 0.0
    assert c["onTimePct"] == 0.0


# ---------------------------------------------------------------------------
# Confiabilidad
# ---------------------------------------------------------------------------
def test_confiabilidad_mttr_y_mtbf():
    scope = {"AAA111": _SCOPE["AAA111"], "BBB222": {"fleet_id": None, "fleet_name": "F1", "cd": "CD1"}}
    wos = [
        # Falla 1 de AAA111: 1-may 00:00→10:00 local (MTTR 10h)
        _wo(
            number=1,
            start_date=datetime(2026, 5, 1, 5, 0, tzinfo=UTC),
            technical_completion_date=datetime(2026, 5, 1, 15, 0, tzinfo=UTC),
        ),
        # Falla 2 de AAA111: 3-may 00:00 (48h después → MTBF 48h), MTTR 20h
        _wo(
            number=2,
            start_date=datetime(2026, 5, 3, 5, 0, tzinfo=UTC),
            technical_completion_date=datetime(2026, 5, 4, 1, 0, tzinfo=UTC),
        ),
        # Programado (no falla): no cuenta
        _wo(
            number=3,
            type="Programado",
            start_date=datetime(2026, 5, 5, 5, 0, tzinfo=UTC),
            technical_completion_date=datetime(2026, 5, 6, 5, 0, tzinfo=UTC),
        ),
        # Falla de BBB222 sin cierre: cuenta como falla, sin MTTR
        _wo(
            number=4,
            vehicle_code="BBB222",
            status="opened",
            start_date=datetime(2026, 5, 10, 5, 0, tzinfo=UTC),
            technical_completion_date=None,
        ),
    ]
    c = _confiabilidad_core(scope, wos, date(2026, 5, 1), date(2026, 5, 31))
    assert c["failureCount"] == 3
    assert c["vehiclesWithFailures"] == 2
    assert c["mttrSample"] == 2
    assert c["mttrHoursAvg"] == pytest.approx(15.0)  # (10+20)/2
    assert c["mttrHoursMedian"] == pytest.approx(15.0)
    assert c["mtbfSample"] == 1
    assert c["mtbfHoursAvg"] == pytest.approx(48.0)


def test_confiabilidad_fuera_de_periodo_no_cuenta():
    wos = [
        _wo(
            number=1,
            start_date=datetime(2026, 4, 1, 5, 0, tzinfo=UTC),
            technical_completion_date=datetime(2026, 4, 2, 5, 0, tzinfo=UTC),
        )
    ]
    c = _confiabilidad_core(_SCOPE, wos, date(2026, 5, 1), date(2026, 5, 31))
    assert c["failureCount"] == 0
    assert c["mttrHoursAvg"] is None
    assert c["mtbfHoursAvg"] is None


# ---------------------------------------------------------------------------
# Router: validación de rango
# ---------------------------------------------------------------------------
def test_default_range_rechaza_rango_invertido():
    from fastapi import HTTPException

    from app.api.v1.mantenimiento import _default_range

    # rango válido pasa
    start, end = _default_range(date(2026, 5, 1), date(2026, 7, 1))
    assert (start, end) == (date(2026, 5, 1), date(2026, 7, 1))
    # Sin fecha inicial: tres meses calendario, desde el día primero.
    start, end = _default_range(None, date(2026, 7, 17))
    assert (start, end) == (date(2026, 5, 1), date(2026, 7, 17))
    # invertido: 422
    with pytest.raises(HTTPException) as exc:
        _default_range(date(2026, 7, 1), date(2026, 5, 1))
    assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# Scope (integración: requiere PostgreSQL con vehicles/fleets sembrados)
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_scoped_plates_respeta_fleet_ids():
    from sqlalchemy import text

    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text(
                    "SELECT v.fleet_id, count(*) AS n FROM vehicles v "
                    "JOIN cloudfleet_vehicles cv ON cv.code = v.plate "
                    "WHERE v.fleet_id IS NOT NULL AND v.is_active "
                    "AND cv.is_in_master GROUP BY v.fleet_id "
                    "ORDER BY n DESC LIMIT 1"
                )
            )
        ).first()
        if row is None:
            pytest.skip("Sin vehículos con flota en la DB de pruebas")
        fleet_id, n = row

        scoped = await _scoped_plates(db, [fleet_id], None)
        # La expectativa usa la misma intersección portal activo ∩ master.
        assert len(scoped) == n
        assert all(meta["fleet_id"] == fleet_id for meta in scoped.values())

        # Sin scope (admin): incluye al menos lo anterior y no rompe
        all_scope = await _scoped_plates(db, None, None)
        assert len(all_scope) >= n

        # Filtro por placa concreta
        plate = next(iter(scoped))
        only = await _scoped_plates(db, [fleet_id], [plate])
        assert set(only) == {plate}


# ---------------------------------------------------------------------------
# RBAC: reportes siempre usan flotas accesibles, incluso para admin
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_report_fleet_ids_siempre_se_limita_a_flotas_accesibles(monkeypatch):
    import uuid

    import app.services.fleet_service as fleet_service
    from app.core.deps import get_report_fleet_ids

    class _Fleet:
        def __init__(self, fid):
            self.id = fid

    fa, fb, ajena = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async def _accessible(_db, _user):
        return [_Fleet(fa), _Fleet(fb)]

    monkeypatch.setattr(fleet_service, "accessible_fleets", _accessible)

    # Sin selección → todas las accesibles, jamás None (incluido admin).
    res = await get_report_fleet_ids(user=object(), selected_fleets=[], db=None)
    assert res is not None
    assert set(res) == {fa, fb}

    # Selección con una flota ajena → la intersección la descarta.
    res2 = await get_report_fleet_ids(
        user=object(), selected_fleets=[fa, ajena], db=None
    )
    assert set(res2) == {fa}
    assert ajena not in res2


# ---------------------------------------------------------------------------
# Queries y métricas históricas (sesión falsa, sin DB real)
# ---------------------------------------------------------------------------
class _MappingRows:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)

    def all(self) -> list[dict]:
        return self.rows


class _Result:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def mappings(self) -> _MappingRows:
        return _MappingRows(self.rows)


class _Session:
    def __init__(self, *responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None) -> _Result:
        self.calls.append((str(statement), dict(params or {})))
        rows = self.responses.pop(0) if self.responses else []
        return _Result(rows)


def _scope_row(plate: str = "AAA111") -> dict:
    return {
        "plate": plate,
        "fleet_id": "11111111-1111-1111-1111-111111111111",
        "fleet_name": "F1",
        "cc": "CD1",
        "city": "Bogotá",
        "vehicle_group_id": None,
    }


@pytest.mark.asyncio
async def test_scoped_plates_exige_portal_activo_y_cloudfleet_master():
    db = _Session([_scope_row()])
    result = await _scoped_plates(db, [_scope_row()["fleet_id"]], ["AAA111"])

    sql, params = db.calls[0]
    assert set(result) == {"AAA111"}
    assert "JOIN cloudfleet_vehicles cv ON cv.code = v.plate" in sql
    assert "v.is_active" in sql
    assert "cv.is_in_master" in sql
    assert params["plates"] == ["AAA111"]


@pytest.mark.asyncio
async def test_scoped_plates_admin_incluye_master_sin_portal_activo():
    db = _Session(
        [], [{"plate": "CF999", "cc": None, "city": "Cali", "vehicle_group_id": None}]
    )
    result = await _scoped_plates(db, None, None)

    sql, _params = db.calls[1]
    assert result["CF999"] == {
        "fleet_id": None,
        "fleet_name": "Sin flota",
        "cd": "Cali",
        "vehicle_group_id": None,
    }
    assert "v.plate = cv.code AND v.is_active" in sql
    assert "cv.is_in_master" in sql


@pytest.mark.asyncio
async def test_fetch_work_orders_filtra_overlap_con_limites_utc():
    db = _Session([])
    await _fetch_work_orders(
        db,
        {"AAA111"},
        datetime(2026, 5, 1),
        datetime(2026, 6, 1),
    )

    sql, params = db.calls[0]
    assert "start_date < :end_dt" in sql
    assert "workshop_date < :end_dt" in sql
    assert "cf_created_at < :end_dt" in sql
    assert "technical_completion_date > :start_dt" in sql
    assert params["start_dt"] == datetime(2026, 5, 1, 5, tzinfo=UTC)
    assert params["end_dt"] == datetime(2026, 6, 1, 5, tzinfo=UTC)


def test_is_open_at_end_usa_fechas_y_limite_exclusivo():
    p_end = datetime(2026, 6, 1)
    base = datetime(2026, 5, 1, 5, tzinfo=UTC)

    assert _is_open_at_end(
        _wo(status="closed", start_date=base, technical_completion_date=None), p_end
    )
    assert not _is_open_at_end(
        _wo(
            status="opened",
            start_date=base,
            technical_completion_date=datetime(2026, 6, 1, 5, tzinfo=UTC),
        ),
        p_end,
    )
    assert not _is_open_at_end(
        _wo(start_date=datetime(2026, 6, 1, 5, tzinfo=UTC)), p_end
    )


@pytest.mark.asyncio
async def test_get_ordenes_open_at_end_es_historico_no_status_actual():
    rows = [
        _wo(
            number=1,
            status="closed",
            start_date=datetime(2026, 5, 10, 5, tzinfo=UTC),
        ),
        _wo(
            number=2,
            status="opened",
            start_date=datetime(2026, 5, 10, 5, tzinfo=UTC),
            technical_completion_date=datetime(2026, 5, 20, 5, tzinfo=UTC),
        ),
        _wo(
            number=3,
            status="closed",
            start_date=datetime(2026, 5, 10, 5, tzinfo=UTC),
            technical_completion_date=datetime(2026, 6, 2, 5, tzinfo=UTC),
        ),
    ]
    db = _Session([_scope_row()], rows)
    result = await get_ordenes(
        db,
        [_scope_row()["fleet_id"]],
        None,
        date(2026, 5, 1),
        date(2026, 5, 31),
    )

    assert result["openAtEnd"] == 2
    assert result["currentlyOpen"] == 1
    assert result["currentlyOpenOrders"][0]["number"] == 2


@pytest.mark.asyncio
async def test_get_ordenes_open_orders_publican_tipo_y_estado_de_plazo():
    """El monitor del modo TV juzga el plazo contra el RELOJ, no contra el fin
    del periodo: sin fecha estimada el estado es desconocido (None), que no es
    lo mismo que estar en plazo."""
    start = datetime(2026, 5, 10, 5, tzinfo=UTC)
    rows = [
        _wo(number=1, status="opened", start_date=start, estimated_finish_date=None),
        _wo(
            number=2,
            status="opened",
            type="Preventivo",
            start_date=start,
            estimated_finish_date=datetime(2000, 1, 1, 5, tzinfo=UTC),
        ),
        _wo(
            number=3,
            status="opened",
            start_date=start,
            estimated_finish_date=datetime(2999, 1, 1, 5, tzinfo=UTC),
        ),
    ]
    db = _Session([_scope_row()], rows)
    result = await get_ordenes(
        db,
        [_scope_row()["fleet_id"]],
        None,
        date(2026, 5, 1),
        date(2026, 5, 31),
    )

    by_number = {row["number"]: row for row in result["currentlyOpenOrders"]}
    assert by_number[1]["overdue"] is None
    assert by_number[1]["estimatedFinishDate"] is None
    assert by_number[1]["type"] == "Correctivo"
    assert by_number[2]["overdue"] is True
    assert by_number[2]["type"] == "Preventivo"
    assert by_number[3]["overdue"] is False
    assert by_number[3]["estimatedFinishDate"] is not None


def test_availability_monthly_optimized_equivale_al_core_por_mes():
    wos = [
        _wo(
            number=1,
            start_date=datetime(2026, 5, 30, 5, tzinfo=UTC),
            technical_completion_date=datetime(2026, 6, 2, 5, tzinfo=UTC),
        ),
        _wo(
            number=2,
            start_date=datetime(2026, 5, 31, 5, tzinfo=UTC),
            technical_completion_date=datetime(2026, 6, 3, 5, tzinfo=UTC),
        ),
    ]
    totals = _availability_monthly_totals(
        _SCOPE,
        wos,
        date(2026, 5, 1),
        date(2026, 6, 30),
        project_only=False,
    )

    for month_start, month_end in _iter_months(
        date(2026, 5, 1), date(2026, 6, 30)
    ):
        expected = _availability_core(_SCOPE, wos, month_start, month_end)["mec_total"]
        assert totals[(month_start.year, month_start.month)] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# _group_schedules: una fila por ocurrencia de rutina, tareas agregadas
# ---------------------------------------------------------------------------
def _sched_row(
    task: str,
    routine: str | None = "R1 650Hr",
    wo_number: int | None = None,
    **raw_extra,
) -> dict:
    raw = {
        "odometerToExecute": None,
        "odometerToExecuteDiff": None,
        "hourmeterToExecute": 4982.0,
        "hourmeterToExecuteDiff": -87.8,
        "vehicle": {
            "currentOdometer": 41196.08,
            "currentHourmeter": 5069.8,
            "avgOdometerDay": 37.0,
            "avgHourmeterDay": 5.0,
        },
    }
    raw.update(raw_extra)
    return {
        "vehicle_code": "AAA111",
        "status": "Vencido",
        "task_name": task,
        "routine_name": routine,
        "schedule_type": "Rutina Secuencial",
        "schedule_source": "Auto",
        "date_to_execute": datetime(2026, 9, 5, 13, 51, tzinfo=UTC),
        "wo_number": wo_number,
        "wo_execution_date": None,
        "raw": raw,
    }


def test_group_schedules_agrupa_tareas_de_una_rutina():
    rows = [_sched_row("Tarea A"), _sched_row("Tarea B"), _sched_row("Tarea C")]
    out = _group_schedules(rows, _SCOPE, date(2026, 7, 16))

    assert len(out) == 1
    g = out[0]
    assert g["routine"] == "R1 650Hr"
    assert g["tasks"] == ["Tarea A", "Tarea B", "Tarea C"]
    assert g["hourmeterToExecute"] == 4982.0
    # ETA horómetro: diff negativo no produce ETA (ya se pasó el objetivo)
    assert g["etaHourmeterDays"] is None
    assert g["daysToExecute"] == (date(2026, 9, 5) - date(2026, 7, 16)).days


def test_group_schedules_sin_rutina_agrupa_por_tarea():
    rows = [
        _sched_row("Trabajo suelto 1", routine=None),
        _sched_row("Trabajo suelto 2", routine=None),
    ]
    out = _group_schedules(rows, _SCOPE, date(2026, 7, 16))
    assert len(out) == 2
    assert {g["routine"] for g in out} == {"Trabajo suelto 1", "Trabajo suelto 2"}


def test_group_schedules_toma_primera_ot_no_nula():
    rows = [
        _sched_row("Tarea A", wo_number=None),
        _sched_row("Tarea B", wo_number=4688),
    ]
    out = _group_schedules(rows, _SCOPE, date(2026, 7, 16))
    assert len(out) == 1
    assert out[0]["woNumber"] == 4688


def test_group_schedules_eta_por_odometro():
    rows = [
        _sched_row(
            "Tarea A",
            odometerToExecute=42000.0,
            odometerToExecuteDiff=740.0,
        )
    ]
    out = _group_schedules(rows, _SCOPE, date(2026, 7, 16))
    # 740 km faltantes / 37 km/día = 20 días
    assert out[0]["etaOdometerDays"] == 20.0
