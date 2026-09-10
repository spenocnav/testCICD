import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings

_FLEET_HEADER = "X-Fleet-Id"
_MAX_FLEETS_RECORDED = 50


def _route_template(request: Request) -> str:
    """Plantilla de la ruta a partir del path real y sus path params.

    FastAPI ≥0.116 no aplana los routers incluidos, así que
    `scope["route"].path` trae solo el tramo local (`/me`, no `/api/v1/me`).
    Se reconstruye reemplazando cada segmento cuyo valor sea un path param por
    `{nombre}`; la comparación es por segmento completo para que un valor no
    pise texto estático.
    """
    params = request.scope.get("path_params") or {}
    path = request.url.path
    if not params:
        return path
    value_to_name = {str(v): name for name, v in params.items()}
    return "/".join(
        f"{{{value_to_name[seg]}}}" if seg in value_to_name else seg
        for seg in path.split("/")
    )


def _parse_fleet_header(raw: str | None) -> list[uuid.UUID] | None:
    """UUIDs del filtro de flota, tolerante: un valor malformado se ignora.

    La validación estricta (400/403) es de `get_selected_fleets`; aquí solo se
    registra lo que el cliente declaró consultar.
    """
    if not raw:
        return None
    ids: list[uuid.UUID] = []
    for part in raw.split(",")[:_MAX_FLEETS_RECORDED]:
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(uuid.UUID(part))
        except ValueError:
            continue
    return ids or None

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Añade defensas del navegador sin sobrescribir decisiones del endpoint."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)

        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)

        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")

        if settings.environment == "production":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )

        return response


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Asocia un request_id a cada petición y lo expone en logs + header de respuesta."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            path=request.url.path,
            method=request.method,
        )

        logger = structlog.get_logger("request")
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("request_failed")
            raise

        elapsed_ms = (time.perf_counter() - start) * 1000
        # La dependencia de autenticación deja la identidad en request.state
        # (el scope es compartido); None = petición no autenticada.
        user_id = getattr(request.state, "usage_user_id", None)
        logger.info(
            "request_completed",
            status_code=response.status_code,
            duration_ms=round(elapsed_ms, 2),
            user_id=str(user_id) if user_id else None,
        )
        response.headers["X-Request-ID"] = request_id

        route = request.scope.get("route")
        if user_id is not None and route is not None:
            # Import diferido: el tracker importa modelos y config; mantenerlo
            # fuera del import del middleware evita ciclos al arrancar.
            from app.services.usage_tracker import tracker

            tracker.record(
                user_id=user_id,
                method=request.method,
                # Plantilla de la ruta, nunca el path crudo: sin IDs ni placas.
                route=_route_template(request),
                status_code=response.status_code,
                duration_ms=elapsed_ms,
                fleet_ids=_parse_fleet_header(request.headers.get(_FLEET_HEADER)),
            )
        return response
