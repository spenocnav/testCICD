#!/usr/bin/env python3
"""Audita o persiste el mapa dateplate/service_model_name → manual Navifault.

Sin ``--execute`` no escribe. Con ``--execute`` conserva decisiones manuales,
actualiza únicamente evidencia automática y retira asociaciones automáticas que
ya no estén sustentadas. El mismo servicio se ejecuta tras cada sync maestro.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, cast

from app.db.advisory_lock import session_advisory_lock
from app.db.session import AsyncSessionLocal, engine
from app.services.navifault_dateplate_map_service import (
    refresh_automatic_dateplate_mappings,
)

if sys.platform == "win32":
    cast(Any, sys.stdout).reconfigure(encoding="utf-8")
    cast(Any, sys.stderr).reconfigure(encoding="utf-8")


async def _main_async(args: argparse.Namespace) -> int:
    async with (
        session_advisory_lock(engine, "navifault-dateplate-map-sync"),
        AsyncSessionLocal() as session,
    ):
        result = await refresh_automatic_dateplate_mappings(
            session,
            execute=args.execute,
            include_inactive=args.include_inactive,
        )
        if args.execute:
            await session.commit()
    print(
        json.dumps(
            {
                "mode": "execute" if args.execute else "dry-run",
                "audit": result.audit,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.execute:
        print(
            json.dumps(
                {
                    "status": "ready",
                    "rows_upserted": len(result.rows),
                    "automatic_rows_retired": result.automatic_rows_retired,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    await engine.dispose()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute", action="store_true", help="Confirma upserts del mapa automático"
    )
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Incluye vehículos inactivos como evidencia histórica",
    )
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(_main_async(args)))
    except Exception as exc:
        print(
            f"Sincronización dateplate Navifault falló: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
