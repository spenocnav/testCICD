"""Entorno Alembic con soporte async para SQLAlchemy 2.x."""

from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig

# psycopg async requiere SelectorEventLoop en Windows.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.db.base import Base

# Importar modelos para que estén registrados en Base.metadata
# (Fase 2 agregará: from app.models import user, role, permission)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url con settings (asyncpg).
config.set_main_option("sqlalchemy.url", settings.effective_database_url)

target_metadata = Base.metadata


def _include_object(object, name, type_, reflected, compare_to):  # noqa: A002
    """Excluir schema analytics de autogenerate — lo gestiona el loader ETL."""
    if getattr(object, "schema", None) == "analytics":
        return False
    if type_ == "table" and getattr(object, "schema", None) == "analytics":
        return False
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
