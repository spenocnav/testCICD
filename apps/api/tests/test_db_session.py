"""Test smoke: la sesión async debe poder ejecutar SELECT 1 contra la DB.

Requiere Postgres corriendo (docker compose up -d postgres).
Saltar si DB no disponible.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.db.session import AsyncSessionLocal, engine


@pytest.mark.asyncio
async def test_db_select_one() -> None:
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
    except (OSError, ConnectionError) as exc:
        pytest.skip(f"Postgres no disponible para test de DB: {exc}")
    finally:
        await engine.dispose()
