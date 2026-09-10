"""Paridad del seed de data maestra contra los valores hardcoded de config.py.

Integración: requiere la DB del portal (env MASTER_DB_URL). Se omite si no está.
Siembra (idempotente) y verifica que lo cargado reconstruye exactamente las
estructuras de config, salvo los dos deltas intencionales (omitir TLK282 y
renombrar demo_fleet -> fleet_colombia).
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine


@pytest.mark.integration
def test_seed_parity_against_config() -> None:
    url = os.environ.get("MASTER_DB_URL")
    if not url:
        pytest.skip("MASTER_DB_URL no configurada")
    if not os.environ.get("MASTER_FERNET_KEY"):
        pytest.skip("MASTER_FERNET_KEY no configurada")

    from seed_master_data import seed
    from verify_master_data import verify

    engine = create_engine(url, pool_pre_ping=True)
    seed(engine)  # idempotente
    assert verify(engine) is True
