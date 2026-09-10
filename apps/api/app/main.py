import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import redis.asyncio as redis_async
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from app.api.v1 import api_router
from app.api.v1.auth import limiter
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.core.observability import init_observability
from app.db.session import engine

configure_logging()
init_observability()
logger = get_logger("app")

_READINESS_TIMEOUT_SECONDS: float = 2.0
_REDIS_SOCKET_TIMEOUT_SECONDS: float = 2.0


async def rate_limit_exception_handler(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, RateLimitExceeded):
        raise exc
    return _rate_limit_exceeded_handler(request, exc)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    from app.services.usage_tracker import tracker

    tracker.start()
    logger.info(
        "api_started",
        environment=settings.environment,
        version=settings.project_version,
        cors_origins=settings.cors_origins_list,
    )
    yield
    await tracker.stop()
    logger.info("api_shutdown")


app = FastAPI(
    title=settings.project_name,
    version=settings.project_version,
    description="Backend API del Portal de Clientes",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exception_handler)

app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=settings.effective_cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
# Starlette deja el último middleware añadido como el más externo. Así las
# cabeceras también cubren preflights respondidos directamente por CORS.
app.add_middleware(SecurityHeadersMiddleware)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception", path=request.url.path, method=request.method)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(api_router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Healthcheck simple — pensado para load balancers."""
    return {"status": "ok"}


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    """Liveness: proceso vivo. Mismo contrato simple que /health."""
    return {"status": "ok"}


CheckStatus = Literal["ok", "fail"]


async def _check_db() -> CheckStatus:
    try:
        async with asyncio.timeout(_READINESS_TIMEOUT_SECONDS):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error(
            "readiness_dependency_failed",
            dependency="database",
            error_type=type(exc).__name__,
        )
        return "fail"
    return "ok"


async def _check_redis() -> CheckStatus:
    try:
        async with asyncio.timeout(_READINESS_TIMEOUT_SECONDS):
            client = redis_async.from_url(
                settings.effective_redis_url,
                socket_timeout=_REDIS_SOCKET_TIMEOUT_SECONDS,
            )
            try:
                await client.ping()
            finally:
                await client.aclose()
    except Exception as exc:
        logger.error(
            "readiness_dependency_failed",
            dependency="redis",
            error_type=type(exc).__name__,
        )
        return "fail"
    return "ok"


@app.get(
    "/health/ready",
    tags=["health"],
    status_code=status.HTTP_200_OK,
    responses={503: {"description": "Una o más dependencias no están listas."}},
)
async def readiness() -> JSONResponse:
    """Readiness: DB y Redis en paralelo. 200 si ambas OK, 503 si alguna falla."""
    db_status, redis_status = await asyncio.gather(_check_db(), _check_redis())
    overall: CheckStatus = "ok" if db_status == "ok" and redis_status == "ok" else "fail"
    http_status = status.HTTP_200_OK if overall == "ok" else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(
        status_code=http_status,
        content={
            "status": overall,
            "checks": {
                "database": {"status": db_status},
                "redis": {"status": redis_status},
            },
        },
    )


@app.get("/version", tags=["health"])
async def version() -> dict[str, str]:
    return {
        "name": settings.project_name,
        "version": settings.project_version,
        "environment": settings.environment,
    }
