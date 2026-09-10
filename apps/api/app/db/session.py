import asyncio
import sys
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

# asyncpg en Windows requiere SelectorEventLoopPolicy (no Proactor).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _connect_args(database_url: str) -> dict[str, Any]:
    """Opciones de sesión equivalentes para psycopg y asyncpg.

    Se configuran al abrir la conexión para que cualquier transacción —incluido
    código SQL textual— herede límites y una identidad observable.
    """
    session_settings = {
        "application_name": settings.database_application_name,
        "statement_timeout": str(settings.database_statement_timeout_ms),
        "lock_timeout": str(settings.database_lock_timeout_ms),
        "idle_in_transaction_session_timeout": str(
            settings.database_idle_transaction_timeout_ms
        ),
    }
    driver = make_url(database_url).drivername
    if driver.endswith("asyncpg"):
        return {"server_settings": session_settings}

    options = " ".join(
        f"-c {name}={value}" for name, value in session_settings.items()
    )
    return {"options": options}


engine = create_async_engine(
    settings.effective_database_url,
    echo=settings.environment == "development" and settings.log_level == "DEBUG",
    connect_args=_connect_args(settings.effective_database_url),
    pool_pre_ping=True,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_timeout=settings.database_pool_timeout_seconds,
    pool_recycle=settings.database_pool_recycle_seconds,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependencia FastAPI: provee una sesión async y la cierra al final."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
