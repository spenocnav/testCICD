"""Locks distribuidos livianos para trabajos exclusivos en PostgreSQL."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


class OperationAlreadyRunningError(RuntimeError):
    """La operación exclusiva ya está activa en otro proceso."""


def advisory_lock_key(name: str) -> int:
    """Convierte un nombre estable en un bigint firmado de PostgreSQL."""
    digest = hashlib.blake2b(name.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


@asynccontextmanager
async def session_advisory_lock(
    engine: AsyncEngine,
    name: str,
) -> AsyncIterator[AsyncConnection]:
    """Mantiene exclusión entre procesos sin dejar una transacción abierta.

    El lock pertenece a la conexión física y PostgreSQL lo libera también si
    el proceso muere. La conexión permanece reservada durante la operación,
    pero se hace commit inmediatamente tras adquirir: las esperas de red no
    aparecen como ``idle in transaction`` ni retienen snapshots/row locks.
    """
    key = advisory_lock_key(name)
    async with engine.connect() as connection:
        acquired = bool(
            await connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": key},
            )
        )
        await connection.commit()
        if not acquired:
            raise OperationAlreadyRunningError(
                f"La operación {name!r} ya está en ejecución."
            )

        try:
            yield connection
        finally:
            if connection.in_transaction():
                await connection.rollback()
            await connection.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": key},
            )
            await connection.commit()
