"""Aislamiento de fallos y códigos seguros del runner de extracción."""

from __future__ import annotations

import pandas as pd
import pytest
import mygeotab

import extract._runner as runner
import extract.extract_combustible as combustible
import master_state
import utils


def test_batch_failure_is_retried_per_device(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    staged: list[pd.DataFrame] = []

    monkeypatch.setattr(runner, "authenticate", lambda *_args: object())
    monkeypatch.setattr(runner, "chunk_windows", lambda *_args: [("from", "to", object())])
    monkeypatch.setattr(runner, "write_staging", lambda frame, _path: staged.append(frame))

    def retry(_func, _api, devices, *_args, **_kwargs):
        calls.append(devices)
        if devices == ["device-a", "device-b"]:
            raise RuntimeError("la respuesta conjunta falló")
        if devices == ["device-b"]:
            raise TimeoutError()
        return pd.DataFrame({"device_id": devices})

    monkeypatch.setattr(runner, "retry_call", retry)

    ok, failed, errors, had_error = runner._status_worker(
        "posicion_pedal",
        lambda *_args: pd.DataFrame(),
        "fleet_db",
        {"username": "etl-user"},
        ["device-a", "device-b"],
        ["vehicle-a", "vehicle-b"],
        object(),
        object(),
        3,
        str(tmp_path / "pedal.parquet"),
    )

    assert calls == [["device-a", "device-b"], ["device-a"], ["device-b"]]
    assert ok == ["vehicle-a"]
    assert failed == ["vehicle-b"]
    assert errors == {"vehicle-b": "network_error"}
    assert had_error is True
    assert len(staged) == 1


def test_error_classifier_never_returns_exception_text():
    error = RuntimeError("password=super-secret https://private.example")

    code = master_state.classify_extraction_error(error)

    assert code == "api_request_error"
    assert "super-secret" not in code
    assert code in master_state.EXTRACTION_ERROR_CODES


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("SessionExpiredException", "authentication_error"),
        ("OverLimitException: 429", "rate_limit_error"),
        ("403 Forbidden", "permission_error"),
        ("request timed out", "network_error"),
        ("unexpected response", "api_request_error"),
    ],
)
def test_error_classifier_separates_operational_causes(message, expected):
    assert master_state.classify_extraction_error(message) == expected


def test_combustible_authentication_failure_is_scoped_and_categorized(monkeypatch):
    def fail_auth(*_args):
        raise RuntimeError("SessionExpiredException")

    monkeypatch.setattr(combustible, "authenticate", fail_auth)

    ok, failed, errors, had_error = combustible._worker(
        "fleet_db",
        {"username": "etl-user"},
        [("device-a", {}, object(), "vehicle-a")],
        object(),
        3,
        {"status": "status.parquet", "trips": "trips.parquet", "events": "events.parquet"},
    )

    assert ok == []
    assert failed == ["vehicle-a"]
    assert errors == {"vehicle-a": "authentication_error"}
    assert had_error is True


def test_retry_call_reauthenticates_after_authentication_error(monkeypatch):
    calls = []
    reauth_calls = []

    monkeypatch.setattr(utils.time, "sleep", lambda _seconds: None)

    def operation(api):
        calls.append(api)
        if len(calls) == 1:
            raise mygeotab.AuthenticationException("user", "db", "server")
        return "ok"

    result = utils.retry_call(
        operation,
        "old-session",
        _script_id="test",
        _label="auth",
        _reauth=lambda: reauth_calls.append(True) or "new-session",
    )

    assert result == "ok"
    assert calls == ["old-session", "new-session"]
    assert reauth_calls == [True]


def test_authenticate_does_not_reuse_legacy_cache_entry(monkeypatch, tmp_path):
    cache_path = tmp_path / "sessions.json"
    cache_path.write_text(
        '{"db_user": {"username": "user", "session_id": "old", "server": "server"}}'
    )
    monkeypatch.setattr(utils, "SESSION_CACHE_FILE", str(cache_path))
    monkeypatch.setattr(utils, "_save_session_to_cache", lambda *_args: None)

    created = {}

    class FakeAPI:
        def __init__(self, **kwargs):
            created.update(kwargs)

        def authenticate(self):
            created["authenticated"] = True

    monkeypatch.setattr(utils.mygeotab, "API", FakeAPI)

    utils.authenticate("db", {"username": "user", "password": "pw"})

    assert "session_id" not in created
    assert created["authenticated"] is True


def _checkpoint_harness(monkeypatch, tmp_path, retry):
    """Worker de tres trozos con checkpoint por trozo; devuelve los checkpoints."""
    checkpoints: list[tuple[list[str], list[str], object]] = []
    staged: list[str] = []
    ends = [f"end-{i}" for i in range(3)]

    monkeypatch.setattr(runner, "authenticate", lambda *_args: object())
    monkeypatch.setattr(
        runner, "chunk_windows",
        lambda *_args: [(f"from-{i}", f"to-{i}", ends[i]) for i in range(3)],
    )
    monkeypatch.setattr(runner, "write_staging", lambda _frame, path: staged.append(path))
    monkeypatch.setattr(runner, "retry_call", retry)

    result = runner._status_worker(
        "rangos_rpm",
        lambda *_args: pd.DataFrame(),
        "fleet_db",
        {"username": "etl-user"},
        ["device-a", "device-b"],
        ["vehicle-a", "vehicle-b"],
        object(),
        object(),
        1,
        str(tmp_path / "rpm.parquet"),
        checkpoint=lambda paths, vids, through: checkpoints.append((paths, vids, through)),
        checkpoint_every=1,
    )
    return result, checkpoints, staged, ends


def test_checkpoint_advances_watermark_after_every_chunk(monkeypatch, tmp_path):
    """Cada trozo persistido avanza a los vehículos vivos hasta el fin del trozo.

    Es lo que hace reanudable un backfill largo: si el worker lo cancela a las
    3 h, los trozos ya consolidados no se repiten mañana."""
    def retry(_func, _api, devices, *_args, **_kwargs):
        return pd.DataFrame({"device_id": devices})

    (ok, failed, _errors, had_error), checkpoints, staged, ends = _checkpoint_harness(
        monkeypatch, tmp_path, retry
    )

    assert [c[2] for c in checkpoints] == ends
    assert all(c[1] == ["vehicle-a", "vehicle-b"] for c in checkpoints)
    # Un staging propio por checkpoint, y nada queda acumulado para el final.
    assert len(staged) == 3 and all(".ck" in p for p in staged)
    assert all(c[0] == [p] for c, p in zip(checkpoints, staged))
    assert (ok, failed, had_error) == (["vehicle-a", "vehicle-b"], [], False)


def test_checkpoint_excludes_devices_that_failed_that_chunk(monkeypatch, tmp_path):
    """Un dispositivo que falla sale de los siguientes trozos y su watermark no
    avanza; el resto sigue persistiéndose."""
    def retry(_func, _api, devices, from_str, *_args, **_kwargs):
        if from_str == "from-1" and devices == ["device-a", "device-b"]:
            raise RuntimeError("lote fallido")
        if from_str == "from-1" and devices == ["device-b"]:
            raise TimeoutError()
        return pd.DataFrame({"device_id": devices})

    (ok, failed, errors, had_error), checkpoints, _staged, ends = _checkpoint_harness(
        monkeypatch, tmp_path, retry
    )

    assert [c[1] for c in checkpoints] == [
        ["vehicle-a", "vehicle-b"], ["vehicle-a"], ["vehicle-a"],
    ]
    assert [c[2] for c in checkpoints] == ends
    assert ok == ["vehicle-a"] and failed == ["vehicle-b"]
    assert errors == {"vehicle-b": "network_error"} and had_error is True


def test_failed_checkpoint_stops_the_partition_without_losing_earlier_ones(monkeypatch, tmp_path):
    calls: list[str] = []

    def retry(_func, _api, devices, from_str, *_args, **_kwargs):
        calls.append(from_str)
        return pd.DataFrame({"device_id": devices})

    checkpoints: list[object] = []

    def checkpoint(_paths, _vids, through):
        checkpoints.append(through)
        if through == "end-1":
            raise OSError("disco lleno")

    monkeypatch.setattr(runner, "authenticate", lambda *_args: object())
    monkeypatch.setattr(
        runner, "chunk_windows",
        lambda *_args: [(f"from-{i}", f"to-{i}", f"end-{i}") for i in range(3)],
    )
    monkeypatch.setattr(runner, "write_staging", lambda *_args: None)
    monkeypatch.setattr(runner, "retry_call", retry)

    ok, failed, errors, had_error = runner._status_worker(
        "rangos_rpm", lambda *_args: pd.DataFrame(), "fleet_db", {"username": "u"},
        ["device-a"], ["vehicle-a"], object(), object(), 1, str(tmp_path / "rpm.parquet"),
        checkpoint=checkpoint, checkpoint_every=1,
    )

    # El primer checkpoint quedó; tras el fallo no se pide el tercer trozo.
    assert checkpoints == ["end-0", "end-1"]
    assert calls == ["from-0", "from-1"]
    assert ok == [] and failed == ["vehicle-a"]
    assert errors == {"vehicle-a": "worker_error"} and had_error is True


def test_chunk_windows_matches_chunk_date_range_and_exposes_end():
    from datetime import datetime, timedelta, timezone

    start = datetime(2026, 1, 1, 5, tzinfo=timezone.utc)
    end = start + timedelta(days=2, hours=12)
    windows = list(runner.chunk_windows(start, end, 1))
    legacy = list(utils.chunk_date_range(start, end, 1))

    assert [(f, t) for f, t, _ in windows] == legacy
    assert [w[2] for w in windows] == [start + timedelta(days=1), start + timedelta(days=2), end]
