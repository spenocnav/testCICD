from __future__ import annotations

import contextlib
import uuid
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from extract import extract_factor_carga
from load import microbatch
from transform.transform_factor_carga import (
    DIAGNOSTIC_ID,
    OUTPUT_COLUMNS,
    transform_factor_carga,
)

ANALYTICS_VEHICLE_ID = "2c59907e34b12fa3f5b705682668cb43"
EXPECTED_FACT_ID = "08a6e95a4a9f31a428b2e0469c3c0494"


def _raw(rows: list[tuple[str, float, str | None, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "database_name": "alion",
                "vehicle_id": ANALYTICS_VEHICLE_ID,
                "device_id": "dev-a",
                "diagnostic_id": DIAGNOSTIC_ID,
                "dateTime": pd.Timestamp(timestamp),
                "data": value,
                "event_id": event_id,
                "fecha_colombia": None,
                "row_id": row_id,
            }
            for timestamp, value, event_id, row_id in rows
        ]
    )


def _dim() -> pd.DataFrame:
    return pd.DataFrame([{"vehicle_id": ANALYTICS_VEHICLE_ID, "placa": "TEST001"}])


def test_factor_carga_matches_frozen_golden() -> None:
    raw = _raw(
        [
            ("2026-07-22T06:00:00Z", 10.0, None, "r1"),
            ("2026-07-22T07:00:00Z", 30.0, None, "r2"),
            ("2026-07-22T08:00:00Z", 999.0, "event", "r3"),
            ("2026-07-22T09:00:00Z", 0.0, None, "r4"),
        ]
    )

    actual = transform_factor_carga(raw, _dim())
    expected = pd.DataFrame(
        [
            {
                "fact_row_id": EXPECTED_FACT_ID,
                "vehicle_id": ANALYTICS_VEHICLE_ID,
                "database_name": "alion",
                "date_key": 20260722,
                "Fecha": date(2026, 7, 22),
                "Placa": "TEST001",
                "Factor de Carga": 20.0,
            }
        ],
        columns=OUTPUT_COLUMNS,
    )
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)


def test_factor_carga_colombia_boundary_and_dedup() -> None:
    raw = _raw(
        [
            ("2026-07-22T04:59:59.999Z", 5.0, None, "previous"),
            ("2026-07-22T05:00:00Z", 10.0, None, "duplicate"),
            ("2026-07-22T05:00:00Z", 100.0, None, "duplicate"),
            ("2026-07-22T06:00:00Z", 30.0, None, "current"),
        ]
    )

    actual = transform_factor_carga(raw, _dim())
    assert list(actual["Fecha"]) == [date(2026, 7, 21), date(2026, 7, 22)]
    assert list(actual["Factor de Carga"]) == [5.0, 20.0]


def test_factor_carga_empty_has_stable_contract() -> None:
    actual = transform_factor_carga(pd.DataFrame(), _dim())
    assert list(actual.columns) == OUTPUT_COLUMNS
    assert actual.empty


def test_clip_drops_previous_reading_echoed_by_geotab() -> None:
    from streaming.factor_carga import clip_to_window

    window_start = datetime(2026, 7, 23, 5, 0, tzinfo=timezone.utc)
    window_end = datetime(2026, 7, 24, 5, 0, tzinfo=timezone.utc)
    # Día sin lecturas: Geotab responde con el último valor vigente, anterior
    # a fromDate. Ese eco no puede convertirse en un hecho del día pedido.
    raw = _raw([("2026-07-22T18:13:04.013Z", 29.0, None, "eco")])

    clipped = clip_to_window(raw, window_start, window_end)

    assert clipped.empty
    assert transform_factor_carga(clipped, _dim()).empty


def test_clip_keeps_both_edges_of_the_colombia_day() -> None:
    from streaming.factor_carga import clip_to_window

    window_start = datetime(2026, 7, 23, 5, 0, tzinfo=timezone.utc)
    window_end = datetime(2026, 7, 24, 5, 0, tzinfo=timezone.utc)
    raw = _raw(
        [
            ("2026-07-23T04:59:59.999Z", 5.0, None, "anterior"),
            ("2026-07-23T05:00:00.000Z", 10.0, None, "inicio"),
            ("2026-07-24T04:59:59.999Z", 30.0, None, "fin"),
            ("2026-07-24T05:00:00.000Z", 99.0, None, "siguiente"),
        ]
    )

    clipped = clip_to_window(raw, window_start, window_end)

    assert list(clipped["row_id"]) == ["inicio", "fin"]
    facts = transform_factor_carga(clipped, _dim())
    assert list(facts["Fecha"]) == [date(2026, 7, 23)]
    assert list(facts["Factor de Carga"]) == [20.0]


def test_extract_rejects_truncated_multicall(monkeypatch) -> None:
    monkeypatch.setattr(extract_factor_carga, "safe_multi_call", lambda *_: [[]])
    try:
        extract_factor_carga.extract_chunk(
            object(),
            ["dev-a", "dev-b"],
            "2026-07-22T05:00:00.000Z",
            "2026-07-23T04:59:59.999Z",
            "alion",
        )
    except RuntimeError as exc:
        assert "respuesta multi_call incompleta" in str(exc)
    else:
        raise AssertionError("una respuesta truncada no debe considerarse exitosa")


def _plans(count: int) -> list:
    from streaming.factor_carga import VehiclePlan

    day = datetime(2026, 7, 22, 5, tzinfo=timezone.utc)
    return [
        VehiclePlan(
            portal_vehicle_id=f"11111111-1111-4111-8111-{index:012d}",
            fleet_id="fleet",
            database_name="alion",
            device_id=f"dev-{index}",
            plate=f"PL{index:04d}",
            motor_type=None,
            group_key=None,
            rpm_class=None,
            analytics_vehicle_id=ANALYTICS_VEHICLE_ID,
            cursor=day,
            version=1,
            to_date=day + timedelta(days=1),
        )
        for index in range(count)
    ]


def _stub_run(monkeypatch, plans: list, failing: set[str]) -> list[str]:
    """Aísla `run()` de Geotab y de PostgreSQL: cada plan entrega un día OK o
    un fallo, según `failing`."""
    import types

    from streaming import factor_carga as stream

    marked: list[str] = []

    class _FakeEngine:
        def dispose(self) -> None:
            pass

    def _extract_plan(_api, _credentials, plan, output, cancel) -> None:
        if plan.portal_vehicle_id in failing:
            output.put(
                stream.ExtractedBatch(
                    error=ValueError("el hecho no pertenece al día del micro-lote"),
                    portal_vehicle_id=plan.portal_vehicle_id,
                    database_name=plan.database_name,
                )
            )
            return
        output.put(
            stream.ExtractedBatch(batch=types.SimpleNamespace(comparison=None))
        )

    monkeypatch.setattr(stream, "ensure_single_transaction_database", lambda: None)
    monkeypatch.setattr(stream, "build_plans", lambda **_: plans)
    monkeypatch.setattr(
        stream,
        "build_assignments",
        lambda p: [stream.ProducerAssignment("alion", {}, tuple(p))],
    )
    monkeypatch.setattr(stream.db, "get_engine", lambda: _FakeEngine())
    monkeypatch.setattr(stream, "_require_schema", lambda _engine: None)
    monkeypatch.setattr(stream, "authenticate", lambda *_: object())
    monkeypatch.setattr(stream, "_extract_plan", _extract_plan)
    monkeypatch.setattr(
        stream,
        "commit_microbatch",
        lambda _engine, _batch: microbatch.CommitResult(
            batch_id=uuid.UUID(int=0),
            output_sha256="0" * 64,
            fact_rows=1,
            previous_ledger_entry=False,
        ),
    )
    monkeypatch.setattr(
        stream.master_state,
        "mark_error",
        lambda vehicle_id, _dataset, _error: marked.append(vehicle_id),
    )
    return marked


def test_failure_budget_has_floor_and_scales() -> None:
    from streaming.factor_carga import failure_budget

    assert failure_budget(0) == 5
    assert failure_budget(20) == 5
    assert failure_budget(383) == 38


def test_run_tolerates_failed_vehicles_within_budget(monkeypatch) -> None:
    from streaming.factor_carga import run

    plans = _plans(10)
    failing = {plans[0].portal_vehicle_id, plans[1].portal_vehicle_id}
    marked = _stub_run(monkeypatch, plans, failing)

    result = run(compare_existing=False)

    assert result["failed_vehicles"] == 2
    assert result["committed_days"] == 8
    assert sorted(marked) == sorted(failing)


def test_run_fails_when_failures_exceed_budget(monkeypatch) -> None:
    from streaming.factor_carga import run

    plans = _plans(10)
    failing = {plan.portal_vehicle_id for plan in plans[:6]}
    _stub_run(monkeypatch, plans, failing)

    try:
        run(compare_existing=False)
    except RuntimeError as exc:
        assert "superan el presupuesto" in str(exc)
    else:
        raise AssertionError("6 vehículos fallidos sobre 10 no son un caso aislado")


class _Result:
    def __init__(self, *, state=None, scalar=None, rowcount=0):
        self._state = state
        self._scalar = scalar
        self.rowcount = rowcount

    def mappings(self):
        return self

    def one_or_none(self):
        return self._state

    def scalar(self):
        return self._scalar


class _Connection:
    def __init__(self):
        self.statements: list[str] = []

    def execute(self, statement, _params=None):
        sql = str(statement)
        self.statements.append(sql)
        if "SELECT version, watermark" in sql:
            return _Result(
                state={
                    "version": 3,
                    "watermark": None,
                    "backfill_from": datetime(2026, 7, 22, 5, tzinfo=timezone.utc),
                }
            )
        if "SELECT 1 FROM etl_microbatch_commit" in sql:
            return _Result(scalar=None)
        if "UPDATE vehicle_extraction_state" in sql:
            return _Result(rowcount=1)
        return _Result()


class _Engine:
    def __init__(self):
        self.connection = _Connection()

    @contextlib.contextmanager
    def begin(self):
        yield self.connection


def test_fact_ledger_and_watermark_share_one_connection(monkeypatch) -> None:
    facts = transform_factor_carga(
        _raw(
            [
                ("2026-07-22T06:00:00Z", 10.0, None, "r1"),
                ("2026-07-22T07:00:00Z", 30.0, None, "r2"),
            ]
        ),
        _dim(),
    )
    batch = microbatch.MicroBatch(
        dataset="factor_carga",
        portal_vehicle_id="11111111-1111-4111-8111-111111111111",
        analytics_vehicle_id=ANALYTICS_VEHICLE_ID,
        expected_version=3,
        expected_cursor=datetime(2026, 7, 22, 5, tzinfo=timezone.utc),
        window_start=datetime(2026, 7, 22, 5, tzinfo=timezone.utc),
        window_end=datetime(2026, 7, 23, 5, tzinfo=timezone.utc),
        vehicle_dim=pd.DataFrame(
            [
                {
                    "vehicle_id": ANALYTICS_VEHICLE_ID,
                    "database_name": "alion",
                    "device_id": "dev-a",
                    "vehicle_label": "TEST001",
                    "motor_type": None,
                    "group_key": None,
                    "rpm_class": None,
                    "is_active": True,
                }
            ]
        ),
        date_dim=microbatch.build_date_dimension(date(2026, 7, 22)),
        facts=facts,
        raw_row_count=2,
    )
    engine = _Engine()
    connections = []

    monkeypatch.setattr(microbatch, "ensure_single_transaction_database", lambda: None)
    monkeypatch.setattr(
        microbatch.db,
        "upsert_dataframe_conn",
        lambda conn, *_args, **_kwargs: connections.append(conn),
    )

    result = microbatch.commit_microbatch(engine, batch)
    assert result.fact_rows == 1
    assert connections == [engine.connection, engine.connection, engine.connection]
    assert any(
        "INSERT INTO etl_microbatch_commit" in s for s in engine.connection.statements
    )
    assert any(
        "UPDATE vehicle_extraction_state" in s for s in engine.connection.statements
    )
