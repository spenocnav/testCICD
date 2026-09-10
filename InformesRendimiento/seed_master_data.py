"""Siembra la data maestra hardcoded de config.py hacia la DB del portal.

Stub de la futura API externa: lee los valores que hoy viven en config.py
(vehículos, motores, reglas) y en .env (credenciales geotab) y los carga en las
tablas del portal (schema public) vía upsert idempotente: actualiza lo que
cambió, inserta lo nuevo, respeta lo existente.

Deltas intencionales frente al hardcoded actual (acordados):
  - Se OMITE el vehículo TLK282 (database_name=None, inactivo, sin credenciales).
  - La base geotab 'demo_fleet' se renombra a su nombre real 'fleet_colombia'
    (cliente Bavaria).

Requiere en el entorno (.env):
  MASTER_DB_URL      URL SQLAlchemy a la DB del portal (schema public).
  MASTER_FERNET_KEY  key Fernet (misma que el portal) para cifrar credenciales.
  Credenciales geotab DEMO_FLEET_*/NAVITRANS_* (ya usadas por config.py).

Uso:
  python seed_master_data.py          # siembra
  python seed_master_data.py --verify # siembra y verifica paridad
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

import config

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("seed_master_data")


# --- Mapeo base geotab -> cliente/flota -------------------------------------
# Renombre de bases respecto al hardcoded actual.
DB_RENAME = {
    "demo_fleet": "fleet_colombia",
    "navitrans": "navitrans",
}

# Base geotab (ya renombrada) -> (fleet_code, fleet_name).
DB_TO_FLEET = {
    "navitrans": ("navitrans", "Navitrans"),
    "fleet_colombia": ("bavaria", "Bavaria"),
}


def _db_name(raw: str) -> str:
    return DB_RENAME.get(raw, raw)


def _fernet() -> Fernet:
    key = os.environ.get("MASTER_FERNET_KEY")
    if not key:
        raise RuntimeError("MASTER_FERNET_KEY no configurada (requerida para cifrar credenciales)")
    return Fernet(key.encode("utf-8"))


def _engine() -> Engine:
    url = os.environ.get("MASTER_DB_URL")
    if not url:
        raise RuntimeError("MASTER_DB_URL no configurada (URL a la DB del portal)")
    # hide_parameters: el seed escribe credenciales cifradas; un fallo no debe
    # volcar los valores del INSERT al log.
    return create_engine(url, pool_pre_ping=True, hide_parameters=True)


def _categoria(rule_name: str) -> str:
    return "RPM Descenso" if "Descenso" in rule_name else "Rango RPM"


def seed(engine: Engine) -> None:
    fernet = _fernet()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        # 1. Flotas (clientes). Upsert por code.
        fleet_ids: dict[str, uuid.UUID] = {}
        for fleet_code, fleet_name in sorted(set(DB_TO_FLEET.values())):
            row = conn.execute(
                text(
                    "INSERT INTO fleets (id, code, name, is_active, created_at, updated_at) "
                    "VALUES (:id, :code, :name, true, :now, :now) "
                    "ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, updated_at = :now "
                    "RETURNING id"
                ),
                {"id": uuid.uuid4(), "code": fleet_code, "name": fleet_name, "now": now},
            ).one()
            fleet_ids[fleet_code] = row[0]
        log.info("flotas: %d", len(fleet_ids))

        # 2. Bases geotab. Upsert por (fleet, database_name); database_key =
        #    nombre en minúsculas (identifica la db física, ver contrato §3.1).
        db_ids: dict[str, uuid.UUID] = {}
        for raw_db in config.CREDENTIALS:
            name = _db_name(raw_db)
            fleet_code, _ = DB_TO_FLEET[name]
            row = conn.execute(
                text(
                    "INSERT INTO geotab_databases "
                    "(id, fleet_id, database_name, database_key, connection_type, is_active, created_at, updated_at) "
                    "VALUES (:id, :fleet_id, :name, :key, 'geotab', true, :now, :now) "
                    "ON CONFLICT (fleet_id, database_name) DO UPDATE SET "
                    "database_key = EXCLUDED.database_key, is_active = true, updated_at = :now "
                    "RETURNING id"
                ),
                {
                    "id": uuid.uuid4(),
                    "fleet_id": fleet_ids[fleet_code],
                    "name": name,
                    "key": name.lower(),
                    "now": now,
                },
            ).one()
            db_ids[name] = row[0]
        log.info("bases geotab: %d", len(db_ids))

        # 3. Credenciales geotab (clave cifrada). Upsert por (db, username).
        n_creds = 0
        for raw_db, creds in config.CREDENTIALS.items():
            name = _db_name(raw_db)
            for cred in creds:
                conn.execute(
                    text(
                        "INSERT INTO geotab_credentials "
                        "(id, geotab_database_id, username, password_enc, is_active, created_at, updated_at) "
                        "VALUES (:id, :db, :user, :pwd, true, :now, :now) "
                        "ON CONFLICT (geotab_database_id, username) DO UPDATE SET "
                        "password_enc = EXCLUDED.password_enc, "
                        "is_active = true, updated_at = :now"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "db": db_ids[name],
                        "user": cred["username"],
                        "pwd": fernet.encrypt(cred["password"].encode("utf-8")),
                        "now": now,
                    },
                )
                n_creds += 1
        log.info("credenciales: %d", n_creds)

        # 4. Catálogo de motores + reglas de combustible (= RULES_BY_MOTOR).
        n_motor_rules = 0
        for motor_type, rules in config.RULES_BY_MOTOR.items():
            conn.execute(
                text(
                    "INSERT INTO motor_catalog (motor_type, description, created_at, updated_at) "
                    "VALUES (:mt, NULL, :now, :now) "
                    "ON CONFLICT (motor_type) DO UPDATE SET updated_at = :now"
                ),
                {"mt": motor_type, "now": now},
            )
            for rule_name, rule_id in rules.items():
                conn.execute(
                    text(
                        "INSERT INTO motor_rules (id, motor_type, rule_name, rule_id, categoria, created_at, updated_at) "
                        "VALUES (:id, :mt, :rn, :rid, :cat, :now, :now) "
                        "ON CONFLICT (motor_type, rule_name) DO UPDATE SET "
                        "rule_id = EXCLUDED.rule_id, categoria = EXCLUDED.categoria, updated_at = :now"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "mt": motor_type,
                        "rn": rule_name,
                        "rid": rule_id,
                        "cat": _categoria(rule_name),
                        "now": now,
                    },
                )
                n_motor_rules += 1
        log.info("motores: %d, reglas de motor: %d", len(config.RULES_BY_MOTOR), n_motor_rules)

        # 5. Reglas de evento (hábitos seguros) por base geotab (= EVENT_RULES).
        #    Van a geotab_rules con category='habito_seguro' (contrato §2.1).
        n_event = 0
        for raw_db, rules in config.EVENT_RULES.items():
            name = _db_name(raw_db)
            if name not in db_ids:
                continue
            for rule_name, rule_id in rules.items():
                conn.execute(
                    text(
                        "INSERT INTO geotab_rules "
                        "(id, geotab_database_id, rule_id, name, category, is_active, created_at, updated_at) "
                        "VALUES (:id, :db, :rid, :rn, 'habito_seguro', true, :now, :now) "
                        "ON CONFLICT (geotab_database_id, rule_id, category) DO UPDATE SET "
                        "name = EXCLUDED.name, is_active = true, updated_at = :now"
                    ),
                    {"id": uuid.uuid4(), "db": db_ids[name], "rn": rule_name, "rid": rule_id, "now": now},
                )
                n_event += 1
        log.info("reglas de evento: %d", n_event)

        # 6. Reglas de exceso RPM por clase (= RPM_RULES).
        n_rpm = 0
        for rpm_class, rule_ids in config.RPM_RULES.items():
            for ordinal, rule_id in enumerate(rule_ids, start=1):
                conn.execute(
                    text(
                        "INSERT INTO rpm_rules (id, rpm_class, rule_id, ordinal, created_at, updated_at) "
                        "VALUES (:id, :rc, :rid, :ord, :now, :now) "
                        "ON CONFLICT (rpm_class, ordinal) DO UPDATE SET "
                        "rule_id = EXCLUDED.rule_id, updated_at = :now"
                    ),
                    {"id": uuid.uuid4(), "rc": rpm_class, "rid": rule_id, "ord": ordinal, "now": now},
                )
                n_rpm += 1
        log.info("reglas RPM: %d", n_rpm)

        # 7. Vehículos (solo activos; se omite TLK282). Upsert por plate (clave
        #    natural del contrato). geotab_customer_status='found': el device_id
        #    hardcoded ya está verificado en la db del cliente.
        n_veh = 0
        for veh in config.ACTIVE_VEHICLE_CATALOG:
            name = _db_name(veh["database_name"])
            if name not in db_ids:
                continue
            fleet_code, _ = DB_TO_FLEET[name]
            conn.execute(
                text(
                    "INSERT INTO vehicles (id, fleet_id, geotab_database_id, geotab_device_id, "
                    "plate, geotab_customer_status, motor_type, "
                    "group_key, rpm_class, tank_volume, is_active, created_at, updated_at) "
                    "VALUES (:id, :fleet, :db, :dev, :plate, 'found', :mt, :gk, :rc, :tank, true, :now, :now) "
                    "ON CONFLICT (plate) DO UPDATE SET "
                    "fleet_id = EXCLUDED.fleet_id, geotab_database_id = EXCLUDED.geotab_database_id, "
                    "geotab_device_id = EXCLUDED.geotab_device_id, "
                    "geotab_customer_status = EXCLUDED.geotab_customer_status, "
                    "motor_type = EXCLUDED.motor_type, "
                    "group_key = EXCLUDED.group_key, rpm_class = EXCLUDED.rpm_class, "
                    "tank_volume = EXCLUDED.tank_volume, updated_at = :now"
                ),
                {
                    "id": uuid.uuid4(),
                    "fleet": fleet_ids[fleet_code],
                    "db": db_ids[name],
                    "dev": veh["device_id"],
                    "plate": veh["placa"],
                    "mt": veh["motor_type"],
                    "gk": veh["group_key"],
                    "rc": veh["rpm_class"],
                    "tank": config.VOLUMEN_TANQUES.get(veh["placa"]),
                    "now": now,
                },
            )
            n_veh += 1
        log.info("vehículos: %d", n_veh)


def main() -> int:
    engine = _engine()
    seed(engine)
    log.info("Seed completado.")
    if "--verify" in sys.argv:
        from verify_master_data import verify

        ok = verify(engine)
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
