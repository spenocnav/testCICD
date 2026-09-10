import json
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_JS = PACKAGE_ROOT / "dashboard" / "app.js"
DASHBOARD_HTML = PACKAGE_ROOT / "dashboard" / "index.html"
SAMPLE_DATA = PACKAGE_ROOT / "dashboard" / "sample-data.json"


def test_dashboard_translates_backend_time_sources_and_health_reasons() -> None:
    source = DASHBOARD_JS.read_text(encoding="utf-8")

    for time_source in (
        "comment_explicit",
        "observation_window_end",
        "tracking_date_baseline",
    ):
        assert time_source in source

    for reason in (
        "api_errors",
        "active_tracking_backlog",
        "orders_outside_poll_sla",
        "insufficient_cycle_capacity",
        "observation_windows_over_sla",
        "invalid_ledger_lines",
        "cycle_failed",
        "health_file_missing",
        "health_timestamp_invalid",
        "health_stale",
        "worker_not_running",
        "historical_bootstrap_required",
        "historical_bootstrap_incomplete",
        "historical_bootstrap_in_progress",
        "live_worker_not_started",
    ):
        assert reason in source

    assert "healthReasonLabel" in source
    assert "Condición operativa:" in source


def test_degraded_status_precedes_generic_not_ready_error() -> None:
    source = DASHBOARD_JS.read_text(encoding="utf-8")

    explicit_error = source.index("errorStatuses.includes(rawStatus) || failedCycles > 1")
    explicit_warning = source.index("warningStatuses.includes(rawStatus)")
    generic_not_ready = source.index("ready === false", explicit_warning)

    assert explicit_error < explicit_warning < generic_not_ready


def test_dashboard_exposes_separate_historical_and_live_coverage() -> None:
    source = DASHBOARD_JS.read_text(encoding="utf-8")
    html = DASHBOARD_HTML.read_text(encoding="utf-8")

    for field in (
        "historicalBootstrapTotalOrders",
        "historicalBootstrapCompletedOrders",
        "historicalBootstrapPendingOrders",
        "historicalBootstrapFailedOrders",
        "historicalBootstrapAttemptedThisRun",
        "historicalBootstrapOrdersPerMinute",
        "historicalBootstrapEtaSeconds",
        "historicalBootstrapProgressUpdatedAt",
        "activeUniverse",
        "activeOrdersPolled",
        "activeOrdersRemainingInRound",
        "activeCoverageRound",
        "fullSweepCycles",
        "fullSweepSeconds",
        "coverageSlaSeconds",
        "activePollSlaFeasible",
    ):
        assert field in source

    assert "Histórico completo por OT" in source
    assert "Sin vencimiento" in source
    assert "pendientes en próximos ciclos · sin descarte" in source
    assert "Barrido activo completo" in source
    assert "Hora máxima de la ventana de observación" in source
    assert "return getBootstrapPhase(data) === 'running'" in source
    assert "if (isBootstrapData(data)) return 15 * 1000;" in source
    assert "Histórico incompleto · live detenido" in source
    assert "kpis.otsWithTracking, kpis.otsWithLabels" in source
    assert "seguimientos · ${formatNumber(labelEvents, 0)} eventos de etiqueta" in source
    assert "Ciclo live de 5 minutos" in html
    assert "SLA ${coverageSlaText} ${sweepFeasibility}" in source
    assert "SLA 5 min ${sweepFeasibility}" not in source


def test_dashboard_sample_uses_final_coverage_contract() -> None:
    payload = json.loads(SAMPLE_DATA.read_text(encoding="utf-8"))
    worker = payload["worker"]

    assert worker["historicalBootstrapStatus"] == "complete"
    assert worker["historicalBootstrapCompletedOrders"] == worker["historicalBootstrapTotalOrders"]
    assert worker["historicalBootstrapPendingOrders"] == 0
    assert worker["historicalBootstrapFailedOrders"] == 0
    assert worker["historicalBootstrapEtaSeconds"] == 0
    assert worker["coverageMode"] == "rotating_complete"
    assert worker["activeUniverse"] >= worker["activeOrdersPolled"]
    assert worker["activeOrdersRemainingInRound"] == 0
    assert worker["fullSweepCycles"] >= 1
    assert worker["fullSweepSeconds"] > 0
    assert worker["coverageSlaSeconds"] == 1800
    assert worker["allActiveScheduledThisCycle"] is True
    assert worker["activePollSlaFeasible"] is True
    assert payload["kpis"]["otsWithTracking"] >= payload["kpis"]["otsWithLabels"]
