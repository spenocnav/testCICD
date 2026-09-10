"""Inicialización opcional de Sentry para el backend."""

from __future__ import annotations

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("observability")


def init_observability() -> None:
    dsn = (settings.sentry_dsn or "").strip()
    if not dsn or settings.environment == "test":
        return

    # El import diferido permite desplegar primero el código y después reconstruir
    # la imagen. Con DSN configurado, una imagen obsoleta informa el problema sin
    # impedir que la API arranque.
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        logger.error("sentry_sdk_missing")
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.environment,
        release=settings.project_version,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        integrations=[StarletteIntegration(), FastApiIntegration()],
    )
    logger.info(
        "sentry_initialized",
        environment=settings.environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
    )
