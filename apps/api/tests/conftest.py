"""Guardas globales y aislamiento de la suite de API.

La suite usa commits reales, TestClient (sesiones propias) y pruebas de
concurrencia. Por eso el aislamiento fiable es recrear el esquema entre
módulos, no envolver una sola conexión en un rollback.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy.engine import make_url

from alembic import command

_API_ROOT = Path(__file__).resolve().parents[1]
_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

if (
    not _TEST_DATABASE_URL
    or make_url(_TEST_DATABASE_URL).database != "portal_clientes_codex_test"
):
    raise RuntimeError(
        "La suite requiere TEST_DATABASE_URL apuntando exactamente a "
        "portal_clientes_codex_test; se rechazó ejecutar para proteger otras bases."
    )

# Debe ocurrir antes de que cualquier módulo de test importe app.settings o
# app.db.session. DATABASE_URL_LOCAL tiene precedencia en development/test.
os.environ["ENVIRONMENT"] = "test"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["DATABASE_URL"] = _TEST_DATABASE_URL
os.environ["DATABASE_URL_LOCAL"] = ""
os.environ.setdefault("TEST_NOVEDADES_DATABASE_URL", _TEST_DATABASE_URL)
os.environ.setdefault("TEST_AUTH_DATABASE_URL", _TEST_DATABASE_URL)
# La suite cifra y descifra credenciales de MyGeotab (sync de la fuente maestra);
# sin una key Fernet cada una de esas pruebas falla con RuntimeError. Una key
# efímera por corrida basta: nada persiste fuera de la base desechable. Si el
# entorno ya trae una, se respeta.
if not os.environ.get("MASTER_FERNET_KEY"):
    from cryptography.fernet import Fernet

    os.environ["MASTER_FERNET_KEY"] = Fernet.generate_key().decode()

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _alembic_config() -> Config:
    config = Config(str(_API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_API_ROOT / "alembic"))
    return config


@pytest.fixture(scope="session", autouse=True)
def migrated_test_database() -> None:
    """Asegura un head válido al iniciar y al terminar la suite."""
    command.upgrade(_alembic_config(), "head")
    yield
    command.upgrade(_alembic_config(), "head")


@pytest.fixture(scope="module", autouse=True)
def isolated_database_module(migrated_test_database: None) -> None:
    """Recrea el esquema por módulo para cortar dependencias de orden.

    Es deliberadamente más costoso que un savepoint: los endpoints y workers
    abren conexiones independientes y hacen commit, por lo que un rollback de
    fixture no podría revertirlos de forma honesta.
    """
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield
