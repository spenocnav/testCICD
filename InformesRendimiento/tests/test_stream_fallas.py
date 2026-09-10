"""FaultData GetFeed: paridad, checkpoint y catch-up sin red ni DB reales."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone

import pandas as pd
import pytest
from cryptography.fernet import Fernet

from extract.extract_fallas import fault_record_to_raw
from load import db
from transform.transform_fallas import transform_fault_rows
from utils import make_row_id

import streaming.fallas as fallas


EVENT_AT = datetime(2026, 8, 22, 13, 45, tzinfo=timezone.utc)
VEHICLE = fallas.CatalogVehicle(
    database_key="physical_db",
    database_name="Cliente Uno",
    device_id="device-1",
    plate="ABC123",
    motor_type="X15",
    group_key="group-1",
    rpm_class="heavy",
)
VEHICLES = {VEHICLE.device_id: (VEHICLE,)}
SOURCE = fallas.FeedSource(
    database_key="physical_db",
    database_fingerprint="db-fingerprint",
    credential_fingerprint="credential-fingerprint",
    username="account@example.invalid",
    password="not-a-secret",
)


def _record(*, native_id: str = "fault-1", count: int = 2) -> dict:
    return {
        "id": native_id,
        "device": {"id": VEHICLE.device_id},
        "dateTime": EVENT_AT,
        "diagnostic": {"id": "diag-1"},
        "controller": {"id": "ctrl-1"},
        "failureMode": {"id": "mode-1"},
        "faultState": "Active",
        "count": count,
        "amberWarningLamp": True,
        "redStopLamp": False,
        "malfunctionLamp": True,
        "protectWarningLamp": False,
    }


def _reference(raw: pd.DataFrame) -> fallas.ReferenceDimensions:
    row = raw.iloc[0]
    return fallas.ReferenceDimensions(
        diagnostic=pd.DataFrame(
            [
                {
                    "diagnostic_sk": row["diagnostic_sk"],
                    "database_name": VEHICLE.database_name,
                    "diagnostic_id": "diag-1",
                    "name": "Presión",
                    "code": "D-1",
                    "source_id": "source-1",
                }
            ]
        ),
        controller=pd.DataFrame(
            [
                {
                    "controller_sk": row["controller_sk"],
                    "database_name": VEHICLE.database_name,
                    "controller_id": "ctrl-1",
                    "name": "Motor",
                    "code": "C-1",
                }
            ]
        ),
        failure_mode=pd.DataFrame(
            [
                {
                    "failure_mode_sk": row["failure_mode_sk"],
                    "database_name": VEHICLE.database_name,
                    "failure_mode_id": "mode-1",
                    "name": "Alto",
                    "code": "F-1",
                }
            ]
        ),
    )


def test_getfeed_mapping_is_exactly_the_daily_mapping() -> None:
    raw, ignored = fallas.map_feed_records(
        SOURCE.database_key, [_record()], VEHICLES
    )
    expected = pd.DataFrame(
        [fault_record_to_raw(VEHICLE.database_name, VEHICLE.device_id, _record())]
    )

    assert ignored == 0
    pd.testing.assert_frame_equal(
        raw.reset_index(drop=True), expected.reset_index(drop=True), check_like=True
    )


def test_semantic_mapping_is_shared_with_the_daily_transform() -> None:
    raw, _ = fallas.map_feed_records(SOURCE.database_key, [_record()], VEHICLES)
    reference = _reference(raw)
    payload = fallas.prepare_payload(raw, VEHICLES, reference)
    direct = transform_fault_rows(
        raw,
        dim_vehicle=pd.DataFrame(
            [
                {
                    "vehicle_id": VEHICLE.analytics_vehicle_id,
                    "database_name": VEHICLE.database_name,
                    "placa": VEHICLE.plate,
                }
            ]
        ),
        dim_diagnostic=reference.diagnostic,
        dim_controller=reference.controller,
        dim_failure_mode=reference.failure_mode,
    )

    pd.testing.assert_frame_equal(
        payload.facts.reset_index(drop=True), direct.reset_index(drop=True)
    )
    assert payload.facts.iloc[0]["Tipo de Atencion"] == "Nivel 2 - Prioritaria"


def test_native_fault_id_collision_keeps_the_legacy_pk_behavior() -> None:
    """La PK histórica no incluye FaultData.id; cambiarla no cabe en este paso.

    Dos IDs nativos con la misma clave natural colisionan y gana el último. La
    prueba hace explícita esa limitación mientras garantiza paridad con el ETL
    diario y evita crear un segundo esquema de identidad.
    """
    raw, _ = fallas.map_feed_records(
        SOURCE.database_key,
        [_record(native_id="native-a", count=1), _record(native_id="native-b", count=9)],
        VEHICLES,
    )

    assert len(raw) == 1
    assert raw.iloc[0]["count"] == 9
    assert raw.iloc[0]["row_id"] == make_row_id(
        VEHICLE.database_name,
        VEHICLE.device_id,
        EVENT_AT,
        "diag-1",
        "mode-1",
        "ctrl-1",
    )


def test_seed_filters_rows_before_colombia_midnight() -> None:
    before = _record()
    before["dateTime"] = datetime(2026, 8, 22, 4, 59, tzinfo=timezone.utc)
    raw, ignored = fallas.map_feed_records(
        SOURCE.database_key,
        [before, _record()],
        VEHICLES,
        seed_from=datetime(2026, 8, 22, 5, tzinfo=timezone.utc),
    )

    assert len(raw) == 1
    assert ignored == 1


class _FakeAPI:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def call(self, method: str, **kwargs):
        self.calls.append((method, kwargs))
        return self.responses.pop(0)


def _empty_reference() -> fallas.ReferenceDimensions:
    return fallas.ReferenceDimensions(
        diagnostic=pd.DataFrame(
            columns=[
                "diagnostic_sk",
                "database_name",
                "diagnostic_id",
                "name",
                "code",
                "source_id",
            ]
        ),
        controller=pd.DataFrame(
            columns=["controller_sk", "database_name", "controller_id", "name", "code"]
        ),
        failure_mode=pd.DataFrame(
            columns=[
                "failure_mode_sk",
                "database_name",
                "failure_mode_id",
                "name",
                "code",
            ]
        ),
    )


def test_full_page_is_checkpointed_then_drained_without_waiting(monkeypatch) -> None:
    api = _FakeAPI(
        [
            {"data": [{"device": {"id": "other"}}, {"device": {"id": "other"}}], "toVersion": "v1"},
            {"data": [], "toVersion": "v2"},
        ]
    )
    commits: list[dict] = []
    monkeypatch.setattr(fallas, "read_cursor", lambda *_: None)
    monkeypatch.setattr(
        fallas, "commit_page", lambda _engine, **kwargs: commits.append(kwargs)
    )

    result = fallas.drain_source(
        api,
        object(),
        source=SOURCE,
        vehicles_by_device=VEHICLES,
        reference=_empty_reference(),
        results_limit=2,
        now=datetime(2026, 8, 22, 14, tzinfo=timezone.utc),
    )

    assert result["pages"] == 2
    assert [call[1].get("from_version") for call in api.calls] == [None, "v1"]
    assert api.calls[0][1]["search"] == {
        "fromDate": "2026-08-22T05:00:00.000Z"
    }
    assert "search" not in api.calls[1][1]
    assert [(c["expected_cursor"], c["to_version"]) for c in commits] == [
        (None, "v1"),
        ("v1", "v2"),
    ]
    # Una respuesta vacía también confirma el nuevo token y el heartbeat.
    assert commits[-1]["received_rows"] == 0


def test_failed_commit_replays_the_same_seed_and_never_drains_next_page(monkeypatch) -> None:
    monkeypatch.setattr(fallas, "read_cursor", lambda *_: None)
    attempts: list[dict] = []

    def fail_commit(_engine, **kwargs):
        attempts.append(kwargs)
        raise RuntimeError("fact insert failed")

    monkeypatch.setattr(fallas, "commit_page", fail_commit)
    first = _FakeAPI(
        [{"data": [{"device": {"id": "other"}}], "toVersion": "v1"}]
    )
    with pytest.raises(RuntimeError, match="fact insert failed"):
        fallas.drain_source(
            first,
            object(),
            source=SOURCE,
            vehicles_by_device=VEHICLES,
            reference=_empty_reference(),
            results_limit=1,
            now=datetime(2026, 8, 22, 14, tzinfo=timezone.utc),
        )

    second_commits: list[dict] = []
    monkeypatch.setattr(
        fallas, "commit_page", lambda _engine, **kwargs: second_commits.append(kwargs)
    )
    second = _FakeAPI([{"data": [], "toVersion": "v1"}])
    fallas.drain_source(
        second,
        object(),
        source=SOURCE,
        vehicles_by_device=VEHICLES,
        reference=_empty_reference(),
        results_limit=1,
        now=datetime(2026, 8, 22, 14, tzinfo=timezone.utc),
    )

    assert len(first.calls) == 1
    assert first.calls[0][1]["search"] == second.calls[0][1]["search"]
    assert attempts[0]["expected_cursor"] is None
    assert second_commits[0]["expected_cursor"] is None


class _Result:
    def __init__(self, *, detail=None, rowcount: int = 1) -> None:
        self.detail = detail
        self.rowcount = rowcount

    def mappings(self):
        return self

    def one(self):
        return {"detail": self.detail}


class _FakeConnection:
    def __init__(self, detail: dict | None = None, update_rowcount: int = 1) -> None:
        self.detail = detail
        self.update_rowcount = update_rowcount
        self.calls: list[tuple[str, dict | None]] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if "SELECT detail FROM public.sync_state" in sql:
            return _Result(detail=self.detail)
        if "UPDATE public.sync_state" in sql:
            return _Result(rowcount=self.update_rowcount)
        return _Result()


class _FakeEngine:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection
        self.transactions = 0
        self.rolled_back = False

    @contextmanager
    def begin(self):
        self.transactions += 1
        try:
            yield self.connection
        except Exception:
            self.rolled_back = True
            raise


def _atomic_payload() -> fallas.FaultPayload:
    raw, _ = fallas.map_feed_records(SOURCE.database_key, [_record()], VEHICLES)
    return fallas.prepare_payload(raw, VEHICLES, _reference(raw))


def test_fact_dimensions_and_cursor_commit_in_one_transaction(monkeypatch) -> None:
    conn = _FakeConnection(detail={"cursor": "v0", "preserve_me": 7})
    engine = _FakeEngine(conn)
    writes: list[tuple[str, str]] = []
    monkeypatch.setattr(
        fallas.db,
        "upsert_dataframe_conn",
        lambda _conn, spec, _frame: writes.append(("upsert", spec.pg_table)),
    )
    monkeypatch.setattr(
        fallas.db,
        "insert_dataframe_ignore_conn",
        lambda _conn, spec, _frame: writes.append(("ignore", spec.pg_table)),
    )

    fallas.commit_page(
        engine,
        source=SOURCE,
        expected_cursor="v0",
        to_version="v1",
        payload=_atomic_payload(),
        received_rows=1,
        polled_at=datetime(2026, 8, 22, 14, tzinfo=timezone.utc),
    )

    assert engine.transactions == 1
    assert ("ignore", "dim_vehicle") in writes
    assert ("upsert", "fact_fault_event") in writes
    assert not any(
        operation == "upsert"
        and table
        in {
            "dim_vehicle",
            "dim_diagnostic",
            "dim_controller",
            "dim_failure_mode",
        }
        for operation, table in writes
    )
    update_sql, update_params = next(
        call for call in conn.calls if "UPDATE public.sync_state" in call[0]
    )
    assert "IS NOT DISTINCT FROM" in update_sql
    assert update_params["expected"] == "v0"
    detail = json.loads(update_params["detail"])
    assert detail["cursor"] == "v1"
    assert detail["preserve_me"] == 7


def test_fact_failure_rolls_back_without_cursor_update(monkeypatch) -> None:
    conn = _FakeConnection(detail={"cursor": "v0"})
    engine = _FakeEngine(conn)

    def write(_conn, spec, _frame):
        if spec.pg_table == "fact_fault_event":
            raise RuntimeError("fact failed")

    monkeypatch.setattr(fallas.db, "upsert_dataframe_conn", write)
    monkeypatch.setattr(fallas.db, "insert_dataframe_ignore_conn", lambda *_: None)

    with pytest.raises(RuntimeError, match="fact failed"):
        fallas.commit_page(
            engine,
            source=SOURCE,
            expected_cursor="v0",
            to_version="v1",
            payload=_atomic_payload(),
            received_rows=1,
            polled_at=datetime(2026, 8, 22, 14, tzinfo=timezone.utc),
        )

    assert engine.rolled_back
    assert not any("UPDATE public.sync_state" in sql for sql, _ in conn.calls)


def test_stale_cursor_fails_before_any_fact_write(monkeypatch) -> None:
    conn = _FakeConnection(detail={"cursor": "already-advanced"})
    engine = _FakeEngine(conn)
    writes: list[str] = []
    monkeypatch.setattr(
        fallas.db,
        "upsert_dataframe_conn",
        lambda _conn, spec, _frame: writes.append(spec.pg_table),
    )
    monkeypatch.setattr(
        fallas.db,
        "insert_dataframe_ignore_conn",
        lambda _conn, spec, _frame: writes.append(spec.pg_table),
    )

    with pytest.raises(fallas.StaleFeedCursorError):
        fallas.commit_page(
            engine,
            source=SOURCE,
            expected_cursor="v0",
            to_version="v1",
            payload=_atomic_payload(),
            received_rows=1,
            polled_at=datetime(2026, 8, 22, 14, tzinfo=timezone.utc),
        )

    assert writes == []
    assert engine.rolled_back


def test_insert_ignore_never_updates_existing_dimension() -> None:
    conn = _FakeConnection()
    db.insert_dataframe_ignore_conn(
        conn,
        fallas.DIM_DIAGNOSTIC_SPEC,
        pd.DataFrame(
            [
                {
                    "diagnostic_sk": "sk",
                    "database_name": "db",
                    "diagnostic_id": "diag",
                    "name": None,
                    "code": None,
                    "source_id": None,
                }
            ]
        ),
    )

    sql = conn.calls[0][0]
    assert "ON CONFLICT" in sql
    assert "DO NOTHING" in sql
    assert "DO UPDATE" not in sql


def test_source_map_groups_by_physical_database_and_credentials(monkeypatch) -> None:
    key = Fernet.generate_key()
    encrypted_a = Fernet(key).encrypt(b"password-a")
    encrypted_b = Fernet(key).encrypt(b"password-b")
    vehicle_rows = [
        ("physical_db", "Cliente Uno", "device-1", "ABC123", "X15", None, None),
        ("physical_db", "Cliente Dos", "device-2", "DEF456", "X15", None, None),
    ]
    credential_rows = [
        ("physical_db", "one@example.invalid", encrypted_a),
        ("physical_db", "two@example.invalid", encrypted_b),
        # Duplicada en una fila hermana: no crea un tercer cursor.
        ("physical_db", "one@example.invalid", encrypted_a),
    ]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement):
            if "gd.database_name" in str(statement):
                return type("Rows", (), {"all": lambda self: vehicle_rows})()
            return type("Rows", (), {"all": lambda self: credential_rows})()

    class Engine:
        def connect(self):
            return Connection()

        def dispose(self):
            return None

    monkeypatch.setenv("MASTER_DB_URL", "postgresql://ignored/ignored")
    monkeypatch.setenv("MASTER_FERNET_KEY", key.decode())
    monkeypatch.setattr(fallas, "create_engine", lambda *_args, **_kwargs: Engine())

    topology = fallas.load_feed_topology()

    assert len(topology.sources) == 2
    assert set(topology.vehicles_by_database) == {"physical_db"}
    assert set(topology.vehicles_by_database["physical_db"]) == {"device-1", "device-2"}
    assert len({source.state_key for source in topology.sources}) == 2
    for source in topology.sources:
        assert source.username not in source.state_key
        assert source.username not in repr(source)
        assert source.password not in repr(source)


def test_master_source_is_required_without_fallback(monkeypatch) -> None:
    monkeypatch.delenv("MASTER_DB_URL", raising=False)
    monkeypatch.delenv("MASTER_FERNET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="fuente maestra"):
        fallas.load_feed_topology()


def test_cursor_and_facts_must_share_the_same_physical_database(monkeypatch) -> None:
    monkeypatch.setenv(
        "MASTER_DB_URL", "postgresql+psycopg://master:secret@db:5432/portal"
    )
    monkeypatch.setattr(
        fallas.config,
        "ANALYTICS_DB_URL",
        "postgresql+psycopg://analytics:other@db:5432/portal",
    )
    fallas.ensure_atomic_database()

    monkeypatch.setattr(
        fallas.config,
        "ANALYTICS_DB_URL",
        "postgresql+psycopg://analytics:other@db:5432/other",
    )
    with pytest.raises(RuntimeError, match="misma base PostgreSQL"):
        fallas.ensure_atomic_database()


def test_global_advisory_contention_skips_tick_before_reading_cursor(monkeypatch) -> None:
    calls: list[tuple[str, tuple]] = []

    @contextmanager
    def busy_global_lock(*args, **_kwargs):
        calls.append(("lock", args))
        yield False

    monkeypatch.setattr(fallas.master_state, "run_lock", busy_global_lock)
    monkeypatch.setattr(
        fallas,
        "ensure_atomic_database",
        lambda: pytest.fail("no debe cargar configuración al omitir el tick"),
    )
    monkeypatch.setattr(
        fallas,
        "read_cursor",
        lambda *_args, **_kwargs: pytest.fail("el cursor no debe leerse ni avanzar"),
    )

    result = fallas.run_cycle()

    assert result == {
        "sources_ok": 0,
        "sources_failed": 0,
        "pages": 0,
        "fact_rows": 0,
    }
    # Sin nombre explícito: es exactamente el mismo advisory global del pipeline.
    assert calls == [("lock", ())]
