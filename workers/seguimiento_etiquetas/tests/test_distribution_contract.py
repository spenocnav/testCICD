from __future__ import annotations

import os
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from historical_seed import load_certified_seed


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ENV = {
    "CLOUDFLEET_API_KEY",
    "CLOUDFLEET_BASE_URL",
    "CLOUDFLEET_TIMEOUT_SECONDS",
    "CLOUDFLEET_MIN_REQUEST_INTERVAL_SECONDS",
    "CLOUDFLEET_MAX_RETRIES",
    "CLOUDFLEET_BACKOFF_BASE_SECONDS",
    "CLOUDFLEET_RATE_LIMIT_FALLBACK_SECONDS",
    "TRACKING_RUNTIME_DIR",
    "TRACKING_POLL_INTERVAL_SECONDS",
    "TRACKING_MAX_ORDERS_PER_CYCLE",
    "TRACKING_COVERAGE_SLA_SECONDS",
    "TRACKING_MAX_ENRICHMENTS_PER_CYCLE",
    "TRACKING_DISCOVERY_OVERLAP_SECONDS",
    "TRACKING_RECONCILIATION_INTERVAL_MINUTES",
    "TRACKING_CATALOG_FROM_DATE",
    "TRACKING_ACTIVE_STATUSES",
    "TRACKING_HEALTH_MAX_AGE_SECONDS",
    "TRACKING_LOG_LEVEL",
}
FORBIDDEN_SUFFIXES = {".pyc", ".zip", ".jsonl", ".parquet", ".csv", ".log", ".db", ".sqlite", ".sqlite3"}
SIGNED_URL = re.compile(
    rb"https?://[^\s\"']+\?[^\s\"']*(?:x-amz-signature|x-amz-security-token|x-amz-credential|x-amz-algorithm|x-amz-expires|x-goog-signature|signature|token|sig|se)=",
    re.IGNORECASE,
)
SECRET_ASSIGNMENT = re.compile(rb"(?mi)^\s*CLOUDFLEET_API_KEY\s*=\s*([^\s#]+)")
BEARER_CREDENTIAL = re.compile(
    rb"(?i)Authorization\s*:\s*Bearer\s+(?![<{][$A-Z_][^>}]*[>}])([A-Za-z0-9._~+/=-]{8,})"
)


def parse_env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (PACKAGE_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, value = stripped.split("=", 1)
        values[key] = value
    return values


def run_from_temporary_cwd(*arguments: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    with tempfile.TemporaryDirectory() as temporary_cwd:
        return subprocess.run(
            [sys.executable, *arguments],
            cwd=temporary_cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )


def test_env_example_is_complete_and_has_no_secret() -> None:
    values = parse_env_example()
    assert REQUIRED_ENV <= values.keys()
    assert values["CLOUDFLEET_API_KEY"] == ""
    assert values["TRACKING_POLL_INTERVAL_SECONDS"] == "300"
    assert values["TRACKING_DISCOVERY_OVERLAP_SECONDS"] == "120"
    assert values["TRACKING_MAX_ENRICHMENTS_PER_CYCLE"] == "5"
    assert values["CLOUDFLEET_MIN_REQUEST_INTERVAL_SECONDS"] == "2.5"
    assert values["TRACKING_MAX_ORDERS_PER_CYCLE"] == "60"
    assert values["TRACKING_COVERAGE_SLA_SECONDS"] == "1800"
    assert values["TRACKING_HEALTH_MAX_AGE_SECONDS"] == "900"
    assert values["CLOUDFLEET_RATE_LIMIT_FALLBACK_SECONDS"] == "60"
    assert values["TRACKING_LOG_LEVEL"] == "INFO"


def test_sensitive_content_detectors_cover_delivery_risks() -> None:
    key_name = b"CLOUDFLEET_API_" + b"KEY"
    bearer_header = b"Authorization: " + b"Bearer "
    assert SECRET_ASSIGNMENT.search(key_name + b"=real-secret-value")
    assert not SECRET_ASSIGNMENT.search(key_name + b"=")
    assert BEARER_CREDENTIAL.search(bearer_header + b"abcdefghijklmnop")
    assert not BEARER_CREDENTIAL.search(bearer_header + b"<" + key_name + b">")
    assert SIGNED_URL.search(
        b"https://bucket.s3.example/file?X-Amz-" + b"Security-Token=temporary-value"
    )
    assert SIGNED_URL.search(
        b"https://bucket.s3.example/file?X-Amz-"
        + b"Algorithm=AWS4-HMAC-SHA256&X-Amz-"
        + b"Signature=deadbeef"
    )


def test_full_reconciliation_yields_shared_api_quota_to_live_tracking() -> None:
    compose = (PACKAGE_ROOT / "compose.yaml").read_text(encoding="utf-8")
    systemd = (
        PACKAGE_ROOT / "deploy" / "systemd" / "cloudfleet-label-reconcile.service"
    ).read_text(encoding="utf-8")
    assert '"--delay", "60.0"' in compose
    assert "--delay 60.0" in systemd
    assert "--delay 5.0" not in compose
    assert "--delay 5.0" not in systemd
    assert compose.count("      CLOUDFLEET_RATE_LIMIT_FALLBACK_SECONDS:") == 3
    assert "start_period: 600s" in compose


def test_resumable_tracking_bootstrap_is_exposed_for_compose_and_systemd() -> None:
    compose = (PACKAGE_ROOT / "compose.yaml").read_text(encoding="utf-8")
    systemd = (
        PACKAGE_ROOT / "deploy" / "systemd" / "cloudfleet-label-bootstrap.service"
    ).read_text(encoding="utf-8")

    assert '"--bootstrap-all-tracking"' in compose
    assert "--bootstrap-all-tracking" in systemd
    assert "Type=oneshot" in systemd
    assert "TimeoutStartSec=infinity" in systemd


def test_demo_payload_is_explicitly_synthetic() -> None:
    demo = json.loads(
        (PACKAGE_ROOT / "dashboard" / "sample-data.json").read_text(encoding="utf-8")
    )
    assert demo["orders"]
    for order in demo["orders"]:
        assert 90000 <= int(order["ot"]) < 91000
        assert order.get("plate") is None or str(order["plate"]).startswith("DEM")
        assert str(order["client"]).startswith("Cliente ")
    for event in demo["recentEvents"]:
        assert "Demo" in str(event["by"]) or event["by"] == "Registro histórico"
    serialized = json.dumps(demo, ensure_ascii=False).lower()
    assert "http://" not in serialized
    assert "https://" not in serialized


def test_entrypoints_do_not_depend_on_current_working_directory() -> None:
    worker = run_from_temporary_cwd(str(PACKAGE_ROOT / "tracking_label_worker.py"), "--help")
    catalog = run_from_temporary_cwd(str(PACKAGE_ROOT / "sync_order_catalog.py"), "--help")
    assert worker.returncode == 0, worker.stderr
    assert catalog.returncode == 0, catalog.stderr
    assert "--log-level" in worker.stdout
    assert "--bootstrap-all-tracking" in worker.stdout
    assert "--max-enrichments" in worker.stdout
    assert "--log-level" in catalog.stdout
    assert "--max-enrichments" in catalog.stdout


def test_healthcheck_is_local_and_not_ready_on_empty_runtime() -> None:
    with tempfile.TemporaryDirectory() as runtime:
        result = run_from_temporary_cwd(
            str(PACKAGE_ROOT / "tracking_label_worker.py"),
            "--healthcheck",
            extra_env={"TRACKING_RUNTIME_DIR": runtime, "CLOUDFLEET_API_KEY": ""},
        )
    assert result.returncode == 1
    combined = f"{result.stdout}\n{result.stderr}".lower()
    assert "authorization" not in combined
    assert "api key" not in combined


def test_python_sources_have_no_external_workspace_dependency() -> None:
    forbidden = ("cloudfleet_kpi_package", "prueba_actualizacion_api", "C:\\Proyectos\\")
    for path in PACKAGE_ROOT.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not any(value in text for value in forbidden), f"Dependencia externa en {path.name}"


def test_text_assets_are_utf8_without_common_mojibake_markers() -> None:
    extensions = {".py", ".md", ".html", ".css", ".js", ".json", ".yaml", ".yml", ".txt", ".example", ".service"}
    ignored_roots = {"runtime", "output", "logs", "__pycache__"}
    for path in PACKAGE_ROOT.rglob("*"):
        if not path.is_file() or any(part in ignored_roots for part in path.relative_to(PACKAGE_ROOT).parts):
            continue
        if path.suffix.lower() not in extensions and path.name not in {"Dockerfile", ".env.example", ".gitignore", ".dockerignore"}:
            continue
        text = path.read_text(encoding="utf-8")
        markers = (
            chr(0x00C3),
            chr(0x00C2),
            chr(0x00E2) + chr(0x20AC),
        )
        assert not any(marker in text for marker in markers), f"Posible mojibake en {path.relative_to(PACKAGE_ROOT)}"


def test_clean_zip_contains_only_distributable_files() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "package.zip"
        result = subprocess.run(
            [sys.executable, str(PACKAGE_ROOT / "scripts" / "build_package.py"), "--output", str(output)],
            cwd=PACKAGE_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            payloads = {name: archive.read(name) for name in names if not name.endswith("/")}
            modes = {name: archive.getinfo(name).external_attr >> 16 for name in names}
            extracted = Path(temporary) / "extracted"
            archive.extractall(extracted)
        certified = load_certified_seed(
            extracted / "seguimiento_etiquetas_produccion" / "seed"
        )
        certified_manifest = certified.manifest

    assert names
    assert all(name.startswith("seguimiento_etiquetas_produccion/") for name in names)
    assert "seguimiento_etiquetas_produccion/.env.example" in names
    assert "seguimiento_etiquetas_produccion/INSTRUCCIONES_TRANSFERENCIA.md" in names
    assert "seguimiento_etiquetas_produccion/dashboard/sample-data.json" in names
    for seed_name in (
        "historical_seed_manifest.json",
        "historical_tracking_seed.ndjson",
        "order_catalog_seed.json",
    ):
        assert f"seguimiento_etiquetas_produccion/seed/{seed_name}" in names
    assert certified_manifest["snapshotComplete"] is True
    assert certified_manifest["totals"]["workOrders"] == 5262
    assert certified_manifest["totals"]["trackingRows"] == 9394
    assert certified_manifest["totals"]["emptyWorkOrders"] == 2125
    for name in names:
        path = Path(name)
        assert path.name != ".env"
        assert "__pycache__" not in path.parts
        assert path.suffix.lower() not in FORBIDDEN_SUFFIXES
        if "runtime" in path.parts or "output" in path.parts or "logs" in path.parts:
            assert path.name == ".gitkeep"
    for name, payload in payloads.items():
        assert not SIGNED_URL.search(payload), f"URL firmada en {name}"
        assert not SECRET_ASSIGNMENT.search(payload), f"API key asignada en {name}"
        assert not BEARER_CREDENTIAL.search(payload), f"Bearer token en {name}"
    shell_scripts = [name for name in names if name.endswith(".sh")]
    assert shell_scripts
    assert all(modes[name] & 0o111 for name in shell_scripts)
