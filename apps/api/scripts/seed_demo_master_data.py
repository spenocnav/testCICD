"""Siembra una flota DEMO con data maestra completa para visualizar el portal.

Crea el cliente ficticio "Transportes El Roble" (el del contrato de
integración) con 2 bases geotab, credenciales, reglas (operación + hábitos
seguros) y vehículos con todos los campos del snapshot. Idempotente: corre las
veces que quieras. NO toca las flotas reales (navitrans/bavaria).

Requiere MASTER_FERNET_KEY en el entorno — usar la MISMA key que
InformesRendimiento, porque su config_source descifra TODO el pool activo y
una credencial con otra key lo rompería.

Uso (desde apps/api):
  MASTER_FERNET_KEY=... uv run python scripts/seed_demo_master_data.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta

sys.path.insert(0, ".")

from sqlalchemy import text

from app.core.crypto import encrypt_secret
from app.db.session import AsyncSessionLocal

NOW = datetime.now(UTC)

FLEET = {"code": "el_roble", "name": "Transportes El Roble", "source_id": 12}

# OJO: las bases demo van INACTIVAS para que el ETL de InformesRendimiento no
# intente autenticar contra ellas (config_source filtra por gd.is_active). El
# portal las muestra igual; solo el pipeline las ignora.
DATABASES = [
    {
        "source_id": 31,
        "database_name": "el_roble_sa",
        "database_key": "el_roble_sa",
        "plate_prefix": None,
        "is_active": False,
    },
    {
        "source_id": 32,
        "database_name": "roble_refrigerados",
        "database_key": "roble_refrigerados",
        "plate_prefix": "TR",
        "is_active": False,
    },
]

# (database_key, username, label, is_active, last_used hace N horas o None)
CREDENTIALS = [
    ("el_roble_sa", "reportes@navitrans.com.co", "cuenta reportes", True, 2),
    ("el_roble_sa", "reportes2@navitrans.com.co", "cuenta secundaria", True, 26),
    ("el_roble_sa", "legacy@navitrans.com.co", "cuenta antigua", False, None),
    ("roble_refrigerados", "frio@navitrans.com.co", "cuenta refrigerados", True, 5),
]

# Catálogo de tipos de motor (controlado; vehículos y reglas operacion lo usan).
# (motor_type, descripción)
MOTORS = [
    ("ISD", "Cummins ISD 6.7"),
    ("X15", "Cummins X15"),
    ("B6.7", "Cummins B6.7"),
    ("OM906", "Mercedes-Benz OM 906"),  # demo: sin reglas operacion (caso huérfano)
]

# (database_key, rule_id geotab, nombre, categoria, motor_type)
# operacion => motor_type obligatorio; habito_seguro => motor_type None (toda la db).
# el_roble_sa mezcla motores ISD y X15: cada uno necesita sus reglas de operacion.
# (db_key, rule_id, name, category, motor_type, band, is_descenso)
# `band` es la banda de RPM declarada por la fuente maestra; None en habito_seguro.
RULES = [
    ("el_roble_sa", "aB1cD2eF3gH", "RPM > 2200 (ISD)", "operacion", "ISD", "exceso_rpm", False),
    (
        "el_roble_sa", "aQ4rS5tU6vW", "Ralentí prolongado > 10 min (ISD)",
        "operacion", "ISD", "ralenti", False,
    ),
    ("el_roble_sa", "aR7sT8uV9wX", "RPM > 2000 (X15)", "operacion", "X15", "exceso_rpm", False),
    (
        "el_roble_sa", "aY1zA2bC3dE", "Ralentí prolongado > 10 min (X15)",
        "operacion", "X15", "ralenti", False,
    ),
    ("el_roble_sa", "aX9yZ8wV7uT", "Frenada brusca", "habito_seguro", None, None, False),
    ("el_roble_sa", "aK3lM2nO1pQ", "Aceleración brusca", "habito_seguro", None, None, False),
    (
        "el_roble_sa", "aF8gH7iJ6kL", "Exceso de velocidad > 85 km/h",
        "habito_seguro", None, None, False,
    ),
    (
        "roble_refrigerados", "bC2dE3fG4hI", "RPM > 2400 (B6.7)",
        "operacion", "B6.7", "exceso_rpm", False,
    ),
    ("roble_refrigerados", "bN5oP6qR7sT", "Frenada brusca", "habito_seguro", None, None, False),
]

# Vehículos con shape completo del snapshot. (db_key None => sin base: not_found)
VEHICLES = [
    {
        "plate": "GQT318", "db": "el_roble_sa", "device": "b1F2", "status": "found",
        "vin": "3HSDJAPR1KN123456", "marca": "INTERNATIONAL", "linea": "PROSTAR",
        "ano": "2023", "comb": "DIESEL", "engine": "79123456", "tech": "D103005BX03",
        "cpl": "4955", "motor": "ISD", "synced_h": 9,
    },
    {
        "plate": "GQT421", "db": "el_roble_sa", "device": "b1F7", "status": "found",
        "vin": "3HSDJAPR3KN654321", "marca": "INTERNATIONAL", "linea": "PROSTAR",
        "ano": "2023", "comb": "DIESEL", "engine": "79123988", "tech": "D103005BX04",
        "cpl": "4955", "motor": "ISD", "synced_h": 9,
    },
    {
        "plate": "SQR905", "db": "el_roble_sa", "device": "b2A1", "status": "found",
        "vin": "1XKYDP9X4NJ481215", "marca": "KENWORTH", "linea": "T680",
        "ano": "2022", "comb": "DIESEL", "engine": "80455211", "tech": "K200881AX01",
        "cpl": "5120", "motor": "X15", "synced_h": 9,
    },
    {
        "plate": "TRC112", "db": "roble_refrigerados", "device": "c4D9", "status": "found",
        "vin": "3ALACWDT5EDFM2851", "marca": "FREIGHTLINER", "linea": "M2 106",
        "ano": "2024", "comb": "DIESEL", "engine": "91220034", "tech": "F300114CX02",
        "cpl": "3310", "motor": "B6.7", "synced_h": 32, "vocacional": True,
    },
    {
        "plate": "TRC587", "db": "roble_refrigerados", "device": "c5E2", "status": "found",
        "vin": "3ALACWDT7EDFM9914", "marca": "FREIGHTLINER", "linea": "M2 106",
        "ano": "2024", "comb": "DIESEL", "engine": "91220177", "tech": "F300114CX03",
        "cpl": "3310", "motor": "B6.7", "synced_h": 32,
    },
    {
        # motor OM906 existe en catálogo pero NO tiene reglas operacion en la db:
        # demo del caso huérfano (solo recibe habito_seguro).
        "plate": "WLP664", "db": "el_roble_sa", "device": None, "status": "not_found",
        "vin": "9BM958074JB112233", "marca": "MERCEDES-BENZ", "linea": "ATEGO 1726",
        "ano": "2018", "comb": "DIESEL", "engine": "45788120", "tech": None,
        "cpl": None, "motor": "OM906", "synced_h": None,
    },
    {
        # sin base y sin motor: solo demo de status unknown.
        "plate": "JDK301", "db": None, "device": None, "status": "unknown",
        "vin": None, "marca": "CHEVROLET", "linea": "NPR 729",
        "ano": "2016", "comb": "DIESEL", "engine": None, "tech": None,
        "cpl": None, "motor": None, "synced_h": None,
    },
]


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        # Flota demo (réplica de customer source_id=12).
        fleet_id = (
            await session.execute(
                text(
                    "INSERT INTO fleets (id, source_id, code, name, is_active, synced_at, created_at, updated_at) "
                    "VALUES (:id, :sid, :code, :name, true, :now, :now, :now) "
                    "ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, "
                    "source_id = EXCLUDED.source_id, synced_at = :now, updated_at = :now "
                    "RETURNING id"
                ),
                {"id": uuid.uuid4(), "sid": FLEET["source_id"], "code": FLEET["code"],
                 "name": FLEET["name"], "now": NOW},
            )
        ).scalar_one()

        db_ids: dict[str, uuid.UUID] = {}
        for d in DATABASES:
            db_ids[d["database_key"]] = (
                await session.execute(
                    text(
                        "INSERT INTO geotab_databases "
                        "(id, source_id, fleet_id, database_name, database_key, connection_type, "
                        "plate_prefix, is_active, synced_at, created_at, updated_at) "
                        "VALUES (:id, :sid, :fleet, :name, :key, 'geotab', :prefix, :active, :now, :now, :now) "
                        "ON CONFLICT (fleet_id, database_name) DO UPDATE SET "
                        "source_id = EXCLUDED.source_id, database_key = EXCLUDED.database_key, "
                        "plate_prefix = EXCLUDED.plate_prefix, is_active = EXCLUDED.is_active, "
                        "synced_at = :now, updated_at = :now "
                        "RETURNING id"
                    ),
                    {"id": uuid.uuid4(), "sid": d["source_id"], "fleet": fleet_id,
                     "name": d["database_name"], "key": d["database_key"],
                     "prefix": d["plate_prefix"], "active": d["is_active"], "now": NOW},
                )
            ).scalar_one()

        for db_key, username, label, active, used_h in CREDENTIALS:
            await session.execute(
                text(
                    "INSERT INTO geotab_credentials "
                    "(id, geotab_database_id, username, password_enc, label, is_active, "
                    "last_used_at, synced_at, created_at, updated_at) "
                    "VALUES (:id, :db, :user, :pwd, :label, :active, :used, :now, :now, :now) "
                    "ON CONFLICT (geotab_database_id, username) DO UPDATE SET "
                    "label = EXCLUDED.label, is_active = EXCLUDED.is_active, "
                    "last_used_at = EXCLUDED.last_used_at, synced_at = :now, updated_at = :now"
                ),
                {"id": uuid.uuid4(), "db": db_ids[db_key], "user": username,
                 "pwd": encrypt_secret("demo-password-no-real"), "label": label,
                 "active": active,
                 "used": NOW - timedelta(hours=used_h) if used_h is not None else None,
                 "now": NOW},
            )

        # Catálogo de motores (global). Idempotente por PK.
        for motor_type, description in MOTORS:
            await session.execute(
                text(
                    "INSERT INTO motor_catalog (motor_type, description, created_at, updated_at) "
                    "VALUES (:mt, :desc, :now, :now) "
                    "ON CONFLICT (motor_type) DO UPDATE SET "
                    "description = EXCLUDED.description, updated_at = :now"
                ),
                {"mt": motor_type, "desc": description, "now": NOW},
            )

        # Reglas: regla fisica + aplicacion/clasificacion.
        await session.execute(
            text("DELETE FROM geotab_rules WHERE geotab_database_id = ANY(:dbs)"),
            {"dbs": list(db_ids.values())},
        )
        for db_key, rule_id, name, category, motor_type, band, is_descenso in RULES:
            physical_id = (
                await session.execute(
                    text(
                        "INSERT INTO geotab_rules "
                        "(id, geotab_database_id, rule_id, name, is_active, "
                        "synced_at, created_at, updated_at) "
                        "VALUES (:id, :db, :rid, :name, true, :now, :now, :now) "
                        "ON CONFLICT (geotab_database_id, rule_id) DO UPDATE SET "
                        "name = EXCLUDED.name, is_active = true, updated_at = :now "
                        "RETURNING id"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "db": db_ids[db_key],
                        "rid": rule_id,
                        "name": name,
                        "now": NOW,
                    },
                )
            ).scalar_one()
            await session.execute(
                text(
                    "INSERT INTO geotab_rule_applications "
                    "(id, geotab_rule_id, category, motor_type, band, is_descenso, is_active, "
                    "synced_at, created_at, updated_at) "
                    "VALUES (:id, :rule, :cat, :motor, :band, :descenso, true, :now, :now, :now)"
                ),
                {
                    "id": uuid.uuid4(),
                    "rule": physical_id,
                    "cat": category,
                    "motor": motor_type,
                    "band": band,
                    "descenso": is_descenso,
                    "now": NOW,
                },
            )

        for v in VEHICLES:
            await session.execute(
                text(
                    "INSERT INTO vehicles "
                    "(id, fleet_id, geotab_database_id, plate, vin, geotab_device_id, "
                    "geotab_device_synced_at, geotab_customer_status, engine_number, "
                    "technical_number, cpl, marca, linea, ano_modelo, tipo_combustible, "
                    "nombre_vehiculo, vocacional, category, motor_type, is_active, synced_at, created_at, updated_at) "
                    "VALUES (:id, :fleet, :db, :plate, :vin, :dev, :dev_sync, :status, "
                    ":engine, :tech, :cpl, :marca, :linea, :ano, :comb, :nombre, :vocacional, :category, :motor, true, "
                    ":now, :now, :now) "
                    "ON CONFLICT (plate) DO UPDATE SET "
                    "fleet_id = EXCLUDED.fleet_id, geotab_database_id = EXCLUDED.geotab_database_id, "
                    "vin = EXCLUDED.vin, geotab_device_id = EXCLUDED.geotab_device_id, "
                    "geotab_device_synced_at = EXCLUDED.geotab_device_synced_at, "
                    "geotab_customer_status = EXCLUDED.geotab_customer_status, "
                    "engine_number = EXCLUDED.engine_number, technical_number = EXCLUDED.technical_number, "
                    "cpl = EXCLUDED.cpl, marca = EXCLUDED.marca, linea = EXCLUDED.linea, "
                    "ano_modelo = EXCLUDED.ano_modelo, tipo_combustible = EXCLUDED.tipo_combustible, "
                    "nombre_vehiculo = EXCLUDED.nombre_vehiculo, vocacional = EXCLUDED.vocacional, "
                    "category = EXCLUDED.category, motor_type = EXCLUDED.motor_type, "
                    "synced_at = :now, updated_at = :now"
                ),
                {
                    "id": uuid.uuid4(), "fleet": fleet_id,
                    "db": db_ids.get(v["db"]) if v["db"] else None,
                    "plate": v["plate"], "vin": v["vin"], "dev": v["device"],
                    "dev_sync": NOW - timedelta(hours=v["synced_h"]) if v["synced_h"] else None,
                    "status": v["status"], "engine": v["engine"], "tech": v["tech"],
                    "cpl": v["cpl"], "marca": v["marca"], "linea": v["linea"],
                    "ano": v["ano"], "comb": v["comb"], "motor": v["motor"],
                    "vocacional": v.get("vocacional", False),
                    "category": v.get("category", "Flota Administrada"),
                    "nombre": f"{v['plate']} - {v['linea']}",
                    "now": NOW,
                },
            )

        await session.commit()
        print(
            f"Demo OK: flota '{FLEET['name']}' con {len(DATABASES)} bases, "
            f"{len(CREDENTIALS)} credenciales, {len(MOTORS)} motores, "
            f"{len(RULES)} reglas, {len(VEHICLES)} vehículos."
        )


if __name__ == "__main__":
    asyncio.run(seed())
