"""Resiliencia del worker: un dominio caído no puede dejar sin reportes al resto."""

from __future__ import annotations

import contextlib
import threading

import pytest

import worker


class _FakeEngine:
    pass


@pytest.fixture
def recorded(monkeypatch):
    """Aísla `run_pipeline` de la DB y captura la fila de auditoría."""
    rows: list[dict] = []

    @contextlib.contextmanager
    def _lock(*_args, **_kwargs):
        yield True

    monkeypatch.setattr(worker.master_state, "run_lock", _lock)
    monkeypatch.setattr(
        worker, "_fact_counts", lambda _engine: dict.fromkeys(worker.FACT_TABLES, 0)
    )
    monkeypatch.setattr(worker, "_extraction_health", lambda _engine: {})
    monkeypatch.setattr(
        worker,
        "_record_sync_run",
        lambda _engine, **kwargs: rows.append(kwargs),
    )
    return rows


def _steps(
    monkeypatch,
    failing: set[str],
    partial: set[str] | None = None,
) -> list[str]:
    executed: list[str] = []
    partial = partial or set()

    def _run_step(rel_path: str) -> str | None:
        executed.append(rel_path)
        if rel_path in failing:
            raise RuntimeError(f"{rel_path} salió con código 1")
        return "sin: transform.transform_combustible" if rel_path in partial else None

    monkeypatch.setattr(worker, "_run_step", _run_step)
    return executed


def test_non_critical_step_leaves_the_run_partial(monkeypatch, recorded) -> None:
    executed = _steps(monkeypatch, {"extract/extract_pedal.py"})

    status = worker.run_pipeline(_FakeEngine(), trigger="manual")

    assert status == "partial"
    # El modelo semántico igual se carga: los demás dominios sí tienen datos.
    assert worker.SEMANTIC_STEP in executed
    assert recorded[0]["status"] == "partial"
    assert recorded[0]["result"]["pasos_fallidos"] == ["extract/extract_pedal.py"]


def test_critical_step_aborts_the_run(monkeypatch, recorded) -> None:
    executed = _steps(monkeypatch, {"extract/extract_dimensions.py"})

    with pytest.raises(RuntimeError):
        worker.run_pipeline(_FakeEngine(), trigger="manual")

    # Sin dimensiones no hay integridad referencial: no se carga nada.
    assert worker.SEMANTIC_STEP not in executed
    assert recorded[0]["status"] == "error"


def test_clean_run_reports_success(monkeypatch, recorded) -> None:
    executed = _steps(monkeypatch, set())

    status = worker.run_pipeline(_FakeEngine(), trigger="worker")

    assert status == "success"
    assert executed[0] == "extract/extract_dimensions.py"
    assert executed[-1] == worker.SEMANTIC_STEP
    assert recorded[0]["error"] is None


def test_pipeline_takes_local_mutex_before_single_global_advisory(
    monkeypatch, recorded
) -> None:
    events: list[str] = []

    class Mutex:
        def __enter__(self):
            events.append("local-enter")

        def __exit__(self, *_args):
            events.append("local-exit")

    @contextlib.contextmanager
    def global_lock(*_args, **_kwargs):
        events.append("global-enter")
        yield True
        events.append("global-exit")

    monkeypatch.setattr(worker, "_etl_execution_mutex", Mutex())
    monkeypatch.setattr(worker.master_state, "run_lock", global_lock)
    _steps(monkeypatch, set())

    assert worker.run_pipeline(_FakeEngine(), trigger="worker") == "success"
    assert events == [
        "local-enter",
        "global-enter",
        "global-exit",
        "local-exit",
    ]


def test_pipeline_waits_for_active_fault_feed(monkeypatch, recorded) -> None:
    import streaming.fallas as fault_feed

    monkeypatch.setattr(worker, "_etl_execution_mutex", threading.Lock())
    feed_active = threading.Event()
    release_feed = threading.Event()
    pipeline_attempted = threading.Event()
    pipeline_step = threading.Event()
    errors: list[BaseException] = []

    def fake_feed(**_kwargs):
        feed_active.set()
        if not release_feed.wait(2):
            raise RuntimeError("test timeout")
        return {"sources_ok": 1, "sources_failed": 0, "pages": 1, "fact_rows": 0}

    def step(_rel_path: str) -> None:
        pipeline_step.set()

    def run_and_capture(callable_, *args, **kwargs) -> None:
        try:
            callable_(*args, **kwargs)
        except BaseException as exc:  # pragma: no cover - evidencia de thread
            errors.append(exc)

    def run_pipeline_after_attempt() -> None:
        pipeline_attempted.set()
        run_and_capture(worker.run_pipeline, _FakeEngine(), trigger="worker")

    monkeypatch.setattr(fault_feed, "run_cycle", fake_feed)
    monkeypatch.setattr(worker, "_run_step", step)
    feed_thread = threading.Thread(
        target=run_and_capture, args=(worker._run_faults_realtime_cycle,)
    )
    feed_thread.start()
    assert feed_active.wait(1)

    pipeline_thread = threading.Thread(
        target=run_pipeline_after_attempt,
    )
    pipeline_thread.start()
    assert pipeline_attempted.wait(1)
    assert not pipeline_step.wait(0.1)

    release_feed.set()
    feed_thread.join(2)
    pipeline_thread.join(2)
    assert pipeline_step.is_set()
    assert not feed_thread.is_alive()
    assert not pipeline_thread.is_alive()
    assert errors == []


def test_fault_feed_waits_for_active_pipeline(monkeypatch, recorded) -> None:
    import streaming.fallas as fault_feed

    monkeypatch.setattr(worker, "_etl_execution_mutex", threading.Lock())
    pipeline_active = threading.Event()
    release_pipeline = threading.Event()
    feed_attempted = threading.Event()
    feed_called = threading.Event()
    errors: list[BaseException] = []

    def step(_rel_path: str) -> None:
        if not pipeline_active.is_set():
            pipeline_active.set()
            if not release_pipeline.wait(2):
                raise RuntimeError("test timeout")

    def fake_feed(**_kwargs):
        feed_called.set()
        return {"sources_ok": 1, "sources_failed": 0, "pages": 1, "fact_rows": 0}

    def run_and_capture(callable_, *args, **kwargs) -> None:
        try:
            callable_(*args, **kwargs)
        except BaseException as exc:  # pragma: no cover - evidencia de thread
            errors.append(exc)

    def run_feed_after_attempt() -> None:
        feed_attempted.set()
        run_and_capture(worker._run_faults_realtime_cycle)

    monkeypatch.setattr(worker, "_run_step", step)
    monkeypatch.setattr(fault_feed, "run_cycle", fake_feed)
    pipeline_thread = threading.Thread(
        target=run_and_capture,
        args=(worker.run_pipeline, _FakeEngine()),
        kwargs={"trigger": "worker"},
    )
    pipeline_thread.start()
    assert pipeline_active.wait(1)

    feed_thread = threading.Thread(
        target=run_feed_after_attempt,
    )
    feed_thread.start()
    assert feed_attempted.wait(1)
    assert not feed_called.wait(0.1)

    release_pipeline.set()
    pipeline_thread.join(2)
    feed_thread.join(2)
    assert feed_called.is_set()
    assert not pipeline_thread.is_alive()
    assert not feed_thread.is_alive()
    assert errors == []


def test_manual_trigger_closes_as_error_when_pipeline_returns_error(monkeypatch) -> None:
    closed: list[dict] = []
    monkeypatch.setattr(worker, "run_pipeline", lambda *_args, **_kwargs: "error")
    monkeypatch.setattr(
        worker,
        "_close_trigger",
        lambda _engine, request_id, **kwargs: closed.append(
            {"id": request_id, **kwargs}
        ),
    )

    worker._run_claimed_trigger(
        _FakeEngine(),
        {"id": "request-1", "requested_by_email": "user@example.invalid"},
    )

    assert closed == [{"id": "request-1", "status": "error"}]


def test_vehicle_extraction_error_makes_run_partial(monkeypatch, recorded) -> None:
    _steps(monkeypatch, set())
    monkeypatch.setattr(worker, "_extraction_health", lambda _engine: {"datasets_error": 2})

    status = worker.run_pipeline(_FakeEngine(), trigger="worker")

    assert status == "partial"
    assert recorded[0]["result"]["pasos_fallidos"] == ["vehicle_extraction_state"]
    assert "2 estado(s) en error" in recorded[0]["error"]


def test_partial_semantic_load_is_reported(monkeypatch, recorded) -> None:
    _steps(monkeypatch, set(), partial={worker.SEMANTIC_STEP})

    status = worker.run_pipeline(_FakeEngine(), trigger="worker")

    assert status == "partial"
    assert recorded[0]["result"]["pasos_fallidos"] == [worker.SEMANTIC_STEP]
    assert "transform_combustible" in recorded[0]["error"]


def test_hung_step_is_cancelled_by_the_deadline(monkeypatch) -> None:
    monkeypatch.setattr(worker, "STEP_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(
        worker.os.path,
        "join",
        lambda *_: "-c",
    )
    monkeypatch.setattr(
        worker.subprocess,
        "run",
        _raise_timeout,
    )

    with pytest.raises(RuntimeError) as exc:
        worker._run_step("extract/extract_combustible.py")

    assert "superó el límite" in str(exc.value)


def _semantic_with(monkeypatch, failing: set[str]) -> list[str]:
    import types

    import run_semantic_all

    executed: list[str] = []

    def _import(module_name: str):
        executed.append(module_name)

        def _main() -> None:
            if module_name in failing:
                raise RuntimeError("parquet corrupto")

        return types.SimpleNamespace(main=_main)

    monkeypatch.setattr(run_semantic_all.importlib, "import_module", _import)
    return executed


def test_semantic_tolerates_a_domain_transform(monkeypatch) -> None:
    import run_semantic_all

    executed = _semantic_with(monkeypatch, {"transform.transform_combustible"})

    assert run_semantic_all.main() == run_semantic_all.PARTIAL_EXIT_CODE
    # El resto del modelo sí se carga: un dominio roto no bloquea los demás.
    assert "load.load_semantic" in executed


def test_semantic_aborts_when_a_dimension_fails(monkeypatch) -> None:
    import run_semantic_all

    executed = _semantic_with(monkeypatch, {"transform.transform_dim_vehicle"})

    with pytest.raises(RuntimeError):
        run_semantic_all.main()

    assert "load.load_semantic" not in executed


def test_fault_feed_loop_runs_immediately_then_waits_interruptibly(monkeypatch) -> None:
    calls: list[str] = []

    class StopAfterFirstWait:
        def __init__(self) -> None:
            self.waits: list[float] = []

        def is_set(self) -> bool:
            return False

        def wait(self, timeout: float) -> bool:
            self.waits.append(timeout)
            return True

    stop = StopAfterFirstWait()
    monkeypatch.setattr(
        worker,
        "_run_faults_realtime_cycle",
        lambda: calls.append("cycle")
        or {"sources_ok": 1, "sources_failed": 0, "pages": 1, "fact_rows": 2},
    )
    monotonic = iter([100.0, 120.0])
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(monotonic))

    worker._faults_realtime_loop(stop, interval_seconds=150)

    assert calls == ["cycle"]
    # El ciclo tomó 20 s: faltan 130 s hasta el siguiente inicio.
    assert stop.waits == [130]


def test_fault_feed_loop_skips_expired_ticks_without_catchup_storm(monkeypatch) -> None:
    class StopAfterFirstWait:
        def __init__(self) -> None:
            self.waits: list[float] = []

        def is_set(self) -> bool:
            return False

        def wait(self, timeout: float) -> bool:
            self.waits.append(timeout)
            return True

    stop = StopAfterFirstWait()
    monkeypatch.setattr(
        worker,
        "_run_faults_realtime_cycle",
        lambda: {"sources_ok": 1, "sources_failed": 0, "pages": 1, "fact_rows": 2},
    )
    # Empezó en 100 y terminó en 260: el tick 250 ya venció. Se omite y se
    # espera hasta 400, en vez de ejecutar inmediatamente una ráfaga atrasada.
    monotonic = iter([100.0, 260.0])
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(monotonic))

    worker._faults_realtime_loop(stop, interval_seconds=150)

    assert stop.waits == [140]


def test_fault_feed_loop_does_not_start_after_shutdown(monkeypatch) -> None:
    calls: list[str] = []
    stopped = threading.Event()
    stopped.set()
    monkeypatch.setattr(
        worker, "_run_faults_realtime_cycle", lambda: calls.append("cycle")
    )

    worker._faults_realtime_loop(stopped, interval_seconds=150)

    assert calls == []


def test_signal_interrupts_both_worker_waits(monkeypatch) -> None:
    stop = threading.Event()
    monkeypatch.setattr(worker, "_shutdown", False)
    monkeypatch.setattr(worker, "_shutdown_event", stop)

    worker._handle_signal(15, None)

    assert worker._shutdown is True
    assert stop.is_set()


def test_fault_feed_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.setattr(worker, "FAULTS_REALTIME_ENABLED", False)
    assert worker._start_faults_realtime_worker() is None


def _raise_timeout(*_args, **kwargs):
    raise worker.subprocess.TimeoutExpired(
        cmd="python",
        timeout=kwargs.get("timeout", 0.5),
        output=b"salida parcial",
        stderr=b"",
    )
