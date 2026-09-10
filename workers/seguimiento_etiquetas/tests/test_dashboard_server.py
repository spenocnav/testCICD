from __future__ import annotations

import importlib.util
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SERVER_SCRIPT = PACKAGE_ROOT / "scripts" / "serve_dashboard.py"


def load_server_module():
    spec = importlib.util.spec_from_file_location("serve_dashboard_for_test", SERVER_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dashboard_server_exposes_only_expected_routes_and_security_headers() -> None:
    module = load_server_module()
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(f"{origin}/healthz", timeout=5) as response:
            assert response.status == 200
            policy = response.headers["Content-Security-Policy"]
            assert "default-src 'self'" in policy
            assert "script-src 'self'" in policy
            assert "style-src 'self' 'unsafe-inline'" in policy
            assert "object-src 'none'" in policy
            assert "base-uri 'none'" in policy
            assert "frame-ancestors 'self'" in policy

        with urllib.request.urlopen(f"{origin}/dashboard/?demo=1", timeout=5) as response:
            assert response.status == 200
            assert b"app.js" in response.read()

        for forbidden_path in ("/.env", "/dashboard/../.env"):
            try:
                urllib.request.urlopen(f"{origin}{forbidden_path}", timeout=5)
            except urllib.error.HTTPError as exc:
                assert exc.code == 404
            else:
                raise AssertionError(f"Ruta sensible expuesta: {forbidden_path}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_closed_stderr_never_breaks_http_responses() -> None:
    module = load_server_module()
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"

    class BrokenOnWrite:
        def write(self, _: str) -> int:
            raise BrokenPipeError("stderr pipe closed")

        def flush(self) -> None:
            raise AssertionError("flush must not run after a failed write")

    class BrokenOnFlush:
        def write(self, message: str) -> int:
            return len(message)

        def flush(self) -> None:
            raise ValueError("stderr stream closed before flush")

    try:
        for broken_stream in (BrokenOnWrite(), BrokenOnFlush()):
            with mock.patch.object(module.sys, "stderr", broken_stream):
                with urllib.request.urlopen(f"{origin}/healthz", timeout=5) as response:
                    assert response.status == 200
                    assert response.read() == b'{"status": "ok"}'
                with urllib.request.urlopen(f"{origin}/dashboard/?demo=1", timeout=5) as response:
                    assert response.status == 200
                    assert b"app.js" in response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
