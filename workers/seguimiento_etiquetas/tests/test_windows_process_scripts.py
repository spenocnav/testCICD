from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_NAMES = (
    "process-control.ps1",
    "spawn_detached.py",
    "start-all.ps1",
    "status-all.ps1",
    "stop-all.ps1",
)


def _powershell() -> str | None:
    return shutil.which("powershell.exe") or shutil.which("powershell")


def _run(powershell: str, script: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *arguments,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def _process_exists(powershell: str, pid: int) -> bool:
    result = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 1 }}",
        ],
        capture_output=True,
        timeout=10,
        check=False,
    )
    return result.returncode == 0


@pytest.mark.skipif(_powershell() is None, reason="PowerShell no disponible")
def test_start_status_stop_manage_exact_processes_without_orphans(tmp_path: Path) -> None:
    powershell = _powershell()
    assert powershell
    package = tmp_path / "package with spaces"
    scripts = package / "scripts"
    scripts.mkdir(parents=True)
    for name in SCRIPT_NAMES:
        shutil.copy2(PACKAGE_ROOT / "scripts" / name, scripts / name)

    stub = """\
import os
import time

print('{"event":"stub_started","pid":%d}' % os.getpid(), flush=True)
while True:
    time.sleep(0.1)
"""
    (package / "tracking_label_worker.py").write_text(stub, encoding="utf-8")
    (scripts / "serve_dashboard.py").write_text(stub, encoding="utf-8")
    env_file = package / "test.env"
    env_file.write_text(
        "CLOUDFLEET_API_KEY=test-only-placeholder\n"
        "DASHBOARD_HOST=127.0.0.1\n"
        "DASHBOARD_PORT=18765\n",
        encoding="utf-8",
    )

    start = scripts / "start-all.ps1"
    status = scripts / "status-all.ps1"
    stop = scripts / "stop-all.ps1"
    pids: list[int] = []
    try:
        started = _run(
            powershell,
            start,
            "-EnvFile",
            str(env_file),
            "-Python",
            sys.executable,
            "-NoOpenBrowser",
        )
        assert started.returncode == 0, started.stdout + started.stderr

        state_root = package / "runtime" / "processes"
        for role in ("worker", "dashboard"):
            metadata = json.loads((state_root / f"{role}.pid.json").read_text(encoding="utf-8"))
            pids.append(int(metadata["pid"]))
            assert _process_exists(powershell, pids[-1])
            assert "CLOUDFLEET_API_KEY" not in json.dumps(metadata)

        reported = _run(powershell, status, "-Json")
        assert reported.returncode == 0, reported.stdout + reported.stderr
        states = json.loads(reported.stdout.lstrip("\ufeff"))
        assert {item["role"]: item["status"] for item in states} == {
            "worker": "running",
            "dashboard": "running",
        }

        duplicate = _run(
            powershell,
            start,
            "-EnvFile",
            str(env_file),
            "-Python",
            sys.executable,
            "-NoOpenBrowser",
        )
        assert duplicate.returncode == 0, duplicate.stdout + duplicate.stderr
        # Keep the assertion independent of Windows PowerShell's console code page.
        assert "Worker y dashboard ya est" in duplicate.stdout

        stopped = _run(powershell, stop)
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        for pid in pids:
            for _ in range(30):
                if not _process_exists(powershell, pid):
                    break
                time.sleep(0.1)
            assert not _process_exists(powershell, pid)
        assert not list(state_root.glob("*.pid.json"))
    finally:
        _run(powershell, stop)
        for pid in pids:
            if _process_exists(powershell, pid):
                subprocess.run(
                    [powershell, "-NoProfile", "-Command", f"Stop-Process -Id {pid} -Force"],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
