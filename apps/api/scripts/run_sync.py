"""Dispara el sync de data maestra desde Navi Vehículos.

Réplica local (flotas, bases geotab, credenciales, reglas, vehículos) por upsert
idempotente. Ver Docs/contrato-intrgracion-portal-clientes.md y
app/services/sync_service.py.

Requiere en el entorno:
  - NAVI_BASE_URL, NAVI_API_KEY (token de integración de Navi Vehículos).
  - MASTER_FERNET_KEY (misma key que InformesRendimiento; cifra credenciales).

Uso (desde apps/api):
  uv run python scripts/run_sync.py            # sync incremental (desde watermark)
  uv run python scripts/run_sync.py --full     # full sync + detección de borrados
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, ".")

from app.services.sync_service import run_sync


async def main() -> None:
    full = "--full" in sys.argv
    result = await run_sync(full=full, trigger="cli")
    modo = "full" if result.full else "incremental"
    print(
        f"Sync {modo} OK (generated_at={result.generated_at}): "
        f"{result.fleets} flotas, {result.databases} bases, "
        f"{result.credentials} credenciales, {result.rules} reglas, "
        f"{result.vehicles} vehículos."
    )
    if result.deactivated:
        desactivadas = ", ".join(f"{k}={n}" for k, n in result.deactivated.items())
        print(f"Desactivadas (no vinieron en full): {desactivadas}.")


if __name__ == "__main__":
    asyncio.run(main())
