"""Programación de la cadena diaria (`scripts/run_daily_sync.py`).

Lo que se protege acá es la aritmética de la hora: los contenedores corren en
UTC y la ventana operativa está definida en hora local de Colombia, así que un
error de offset movería la corrida al día anterior.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_daily_sync.py"
_spec = importlib.util.spec_from_file_location("run_daily_sync", _SCRIPT)
assert _spec and _spec.loader
run_daily_sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_daily_sync)

BOGOTA = ZoneInfo("America/Bogota")


def test_next_fire_is_0900_utc_for_0400_bogota() -> None:
    now = datetime(2026, 7, 31, 21, 40, tzinfo=UTC)
    target = run_daily_sync.next_fire_at(now, at="04:00", tz=BOGOTA)

    assert target == datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    assert target.astimezone(BOGOTA).hour == 4


def test_next_fire_skips_to_tomorrow_when_hour_already_passed() -> None:
    # 04:30 hora Bogotá: la ventana de hoy ya pasó.
    now = datetime(2026, 7, 31, 9, 30, tzinfo=UTC)
    target = run_daily_sync.next_fire_at(now, at="04:00", tz=BOGOTA)

    assert target == datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


def test_next_fire_is_strictly_future_at_the_exact_hour() -> None:
    """Justo a las 04:00 se programa la del día siguiente: evita re-disparar
    la misma ventana cuando la cadena termina en menos de un minuto."""
    now = datetime(2026, 7, 31, 9, 0, tzinfo=UTC)
    target = run_daily_sync.next_fire_at(now, at="04:00", tz=BOGOTA)

    assert target == datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


def test_next_fire_honours_other_timezones() -> None:
    now = datetime(2026, 7, 31, 21, 40, tzinfo=UTC)
    target = run_daily_sync.next_fire_at(now, at="04:00", tz=ZoneInfo("UTC"))

    assert target == datetime(2026, 8, 1, 4, 0, tzinfo=UTC)


def test_steps_keep_canonical_order() -> None:
    assert run_daily_sync.STEPS == ("master", "reportes", "cloudfleet", "novedades")
    # El orden lo fija el pipeline (maestra alimenta al ETL), no el usuario.
    assert run_daily_sync._parse_steps("cloudfleet,master") == ("master", "cloudfleet")


def test_unknown_step_is_rejected() -> None:
    with pytest.raises(Exception, match="desconocidos"):
        run_daily_sync._parse_steps("master,geotab")


async def _clear_requests() -> None:
    from sqlalchemy import delete

    from app.db.session import AsyncSessionLocal
    from app.models.etl_trigger import EtlTriggerRequest

    async with AsyncSessionLocal() as session:
        await session.execute(delete(EtlTriggerRequest))
        await session.commit()


@pytest.fixture(autouse=True)
async def _empty_etl_queue() -> None:
    """Cada caso arranca sin solicitudes: la cola es global por diseño."""
    await _clear_requests()
    yield
    await _clear_requests()


async def _close_pending_as(status: str, *, timeout: float = 10.0) -> None:
    """Simula al worker del ETL cerrando la solicitud que encoló la cadena.

    Espera a que la solicitud exista en vez de dormir un fijo: el primer
    roundtrip a la DB del proceso puede tardar más que cualquier sleep corto.
    """
    import asyncio as _asyncio

    from sqlalchemy import update

    from app.db.session import AsyncSessionLocal
    from app.models.etl_trigger import EtlTriggerRequest

    deadline = _asyncio.get_running_loop().time() + timeout
    while _asyncio.get_running_loop().time() < deadline:
        async with AsyncSessionLocal() as session:
            updated = await session.execute(
                update(EtlTriggerRequest)
                .where(EtlTriggerRequest.status.in_(["pending", "running"]))
                .values(status=status)
            )
            await session.commit()
        if updated.rowcount:
            return
        await _asyncio.sleep(0.05)
    raise AssertionError("la cadena nunca encoló una solicitud de ETL")


@pytest.mark.asyncio
async def test_reportes_step_waits_until_the_etl_closes_the_request() -> None:
    """El paso 2 no corre el pipeline: encola y espera a `reportes-etl-worker`."""
    import asyncio as _asyncio

    closer = _asyncio.create_task(_close_pending_as("done"))
    result = await run_daily_sync._step_reportes(
        timeout=10, poll=0.05, shutdown=run_daily_sync._Shutdown()
    )
    await closer

    assert result == "ok"


@pytest.mark.asyncio
async def test_master_step_uses_full_sync_for_classifications(monkeypatch) -> None:
    calls: list[dict] = []

    async def _sync(*, full: bool, trigger: str):
        calls.append({"full": full, "trigger": trigger})
        return type(
            "Result",
            (),
            {
                "fleets": 1,
                "databases": 1,
                "credentials": 0,
                "rules": 0,
                "vehicles": 1,
            },
        )()

    monkeypatch.setattr(run_daily_sync, "run_sync", _sync)
    await run_daily_sync._step_master()

    assert calls == [{"full": True, "trigger": "worker"}]


@pytest.mark.asyncio
async def test_reportes_step_reuses_a_request_already_queued() -> None:
    """Un disparo manual del portal a la misma hora no apila una segunda
    corrida: la cadena se engancha a la que ya está en cola."""
    first_id, first_created = await run_daily_sync._enqueue_etl()
    second_id, second_created = await run_daily_sync._enqueue_etl()

    assert first_created is True
    assert second_created is False
    assert second_id == first_id


@pytest.mark.asyncio
async def test_reportes_step_raises_when_the_etl_reports_error() -> None:
    import asyncio as _asyncio

    closer = _asyncio.create_task(_close_pending_as("error"))
    with pytest.raises(RuntimeError, match="error"):
        await run_daily_sync._step_reportes(
            timeout=10, poll=0.05, shutdown=run_daily_sync._Shutdown()
        )
    await closer


@pytest.mark.asyncio
async def test_reportes_step_times_out_without_blocking_the_chain() -> None:
    """Un ETL largo no debe costar el paso de CloudFleet: se corta la espera y
    la solicitud queda viva para el worker."""
    await run_daily_sync._enqueue_etl()

    with pytest.raises(TimeoutError):
        await run_daily_sync._step_reportes(
            timeout=0.2, poll=0.05, shutdown=run_daily_sync._Shutdown()
        )


@pytest.mark.asyncio
async def test_chain_keeps_going_after_a_failing_step(monkeypatch) -> None:
    calls: list[str] = []

    async def _boom() -> None:
        calls.append("master")
        raise RuntimeError("Navi Vehículos caído")

    async def _cloudfleet(*, refresh_days: int) -> None:
        calls.append("cloudfleet")

    monkeypatch.setattr(run_daily_sync, "_step_master", _boom)
    monkeypatch.setattr(run_daily_sync, "_step_cloudfleet", _cloudfleet)

    outcome = await run_daily_sync.run_chain(
        steps=("master", "cloudfleet"),
        etl_timeout=1,
        etl_poll=0.05,
        refresh_days=45,
        shutdown=run_daily_sync._Shutdown(),
    )

    assert calls == ["master", "cloudfleet"]
    assert outcome == {
        "master": "error",
        "reportes": "skipped",
        "cloudfleet": "ok",
        "novedades": "skipped",
    }


@pytest.mark.asyncio
async def test_novedades_step_respects_disable_flag_and_reports_partial(monkeypatch) -> None:
    calls: list[str] = []

    async def _sync(*, trigger: str) -> dict:
        calls.append(trigger)
        return {"candidates": 3, "resolved_now": 1, "still_open": 1, "missing": 1}

    monkeypatch.setattr(run_daily_sync, "run_novedad_status_sync", _sync)
    monkeypatch.setattr(run_daily_sync.settings, "daily_sync_novedades_enabled", False)
    assert await run_daily_sync._step_novedades() == "skipped"
    assert calls == []

    monkeypatch.setattr(run_daily_sync.settings, "daily_sync_novedades_enabled", True)
    assert await run_daily_sync._step_novedades() == "partial"
    assert calls == ["worker"]
