"""Configuración de tests.

`config.py` exige credenciales en el entorno al importarse (`_load_credentials`
lanza ValueError si faltan). Inyectamos credenciales dummy ANTES de cualquier
import de `config`/`validate`/`load` para que los tests no dependan de un `.env`.
"""

import os

# Los tests unitarios deben fallar cerrados y nunca descubrir las URLs reales
# desde InformesRendimiento/.env al importar config.py.
os.environ["MASTER_DB_URL"] = ""
os.environ["MASTER_FERNET_KEY"] = ""
os.environ["ANALYTICS_DB_URL"] = ""

os.environ.setdefault("DEMO_FLEET_USER_1", "test")
os.environ.setdefault("DEMO_FLEET_PASS_1", "test")
os.environ.setdefault("NAVITRANS_USER_1", "test")
os.environ.setdefault("NAVITRANS_PASS_1", "test")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: requiere PostgreSQL (env TEST_ANALYTICS_DB_URL)"
    )
