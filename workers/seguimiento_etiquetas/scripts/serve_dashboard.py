"""Serve only the reference dashboard, runtime payload and probe endpoints."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from dotenv import load_dotenv


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_ROOT = PACKAGE_ROOT / "dashboard"
load_dotenv(PACKAGE_ROOT / ".env", override=False)


def runtime_root() -> Path:
    configured = Path(os.getenv("TRACKING_RUNTIME_DIR", "runtime"))
    return configured.resolve() if configured.is_absolute() else (PACKAGE_ROOT / configured).resolve()


def readiness() -> tuple[bool, str]:
    env = os.environ.copy()
    env["TRACKING_RUNTIME_DIR"] = str(runtime_root())
    try:
        result = subprocess.run(
            [sys.executable, str(PACKAGE_ROOT / "tracking_label_worker.py"), "--healthcheck"],
            cwd=PACKAGE_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    detail = (result.stdout or result.stderr or "healthcheck sin detalle").strip()
    return result.returncode == 0, detail[-1000:]


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "CloudfleetDashboard/1.0"

    def _security_headers(self, cache_control: str) -> None:
        self.send_header("Cache-Control", cache_control)
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'self'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")

    def _json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._security_headers("no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _file(self, path: Path, cache_control: str = "no-cache") -> None:
        try:
            resolved = path.resolve(strict=True)
            body = resolved.read_bytes()
        except (OSError, RuntimeError):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        media_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        if resolved.suffix == ".json":
            media_type = "application/json"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{media_type}; charset=utf-8" if media_type.startswith(("text/", "application/json", "application/javascript")) else media_type)
        self.send_header("Content-Length", str(len(body)))
        self._security_headers(cache_control)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _redirect(self, target: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", target)
        self._security_headers("no-store")
        self.end_headers()

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        request_path = unquote(urlsplit(self.path).path)
        if request_path == "/healthz":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if request_path == "/readyz":
            ready, detail = readiness()
            self._json(
                HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
                {"status": "ready" if ready else "not_ready", "detail": detail},
            )
            return
        if request_path in {"/", "/dashboard"}:
            self._redirect("/dashboard/")
            return
        if request_path in {"/runtime/label_dashboard.json", "/data/label_dashboard.json"}:
            self._file(runtime_root() / "label_dashboard.json", "no-store")
            return
        if request_path == "/dashboard/":
            self._file(DASHBOARD_ROOT / "index.html")
            return
        if request_path.startswith("/dashboard/"):
            relative = Path(request_path.removeprefix("/dashboard/"))
            if relative.is_absolute() or ".." in relative.parts:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            candidate = (DASHBOARD_ROOT / relative).resolve()
            try:
                candidate.relative_to(DASHBOARD_ROOT.resolve())
            except ValueError:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._file(candidate)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        message = f"{self.log_date_time_string()} {self.address_string()} {format % args}\n"
        try:
            sys.stderr.write(message)
            sys.stderr.flush()
        except (BrokenPipeError, OSError, ValueError):
            # Logging must never invalidate an otherwise complete HTTP response.
            # Background launchers may close stderr while keeping the server alive.
            return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("DASHBOARD_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("DASHBOARD_PORT", "8765")))
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Dashboard: http://{args.host}:{args.port}/dashboard/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
