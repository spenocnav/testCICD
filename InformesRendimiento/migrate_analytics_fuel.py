"""Ejecuta la migración idempotente del contrato gas/líquido de analytics."""

from __future__ import annotations

import logging

import config
from load import db
from load.fuel_contract import ensure_fuel_contract

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def main() -> None:
    engine = db.get_engine()
    db.ensure_schema(engine)
    migrated = ensure_fuel_contract(engine, config.ANALYTICS_DB_SCHEMA)
    logging.info(
        "Contrato combustible listo: vehículos=%d, diario líquido=%d, mensual líquido=%d",
        migrated["vehicles"],
        migrated["daily_liquid"],
        migrated["monthly_liquid"],
    )


if __name__ == "__main__":
    main()
