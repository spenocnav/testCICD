"""Verifica paridad entre la data maestra sembrada en la DB del portal y los
valores hardcoded de config.py (con los dos deltas intencionales: se omite
TLK282 y 'demo_fleet' -> 'fleet_colombia').

`verify(engine)` reconstruye las estructuras desde la DB y las compara contra lo
esperado. Devuelve True si todo cuadra; loguea las diferencias si no.
"""

from __future__ import annotations

import logging

from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.engine import Engine

import config
from seed_master_data import DB_RENAME, _fernet

log = logging.getLogger("verify_master_data")


def _db_name(raw: str) -> str:
    return DB_RENAME.get(raw, raw)


def _config_db_names() -> tuple[str, ...]:
    """Bases que vienen de config.py. La paridad se acota a estas: la DB puede
    tener además flotas demo u otras sembradas por el portal."""
    return tuple(sorted({_db_name(d) for d in config.CREDENTIALS}))


def _expected_vehicles() -> set[tuple]:
    return {
        (
            _db_name(v["database_name"]),
            v["device_id"],
            v["placa"],
            v["motor_type"],
            v["group_key"],
            v["rpm_class"],
            config.VOLUMEN_TANQUES.get(v["placa"]),
        )
        for v in config.ACTIVE_VEHICLE_CATALOG
    }


def _db_vehicles(engine: Engine) -> set[tuple]:
    rows = engine.connect().execute(
        text(
            "SELECT gd.database_name, v.geotab_device_id, v.plate, v.motor_type, "
            "v.group_key, v.rpm_class, v.tank_volume "
            "FROM vehicles v JOIN geotab_databases gd ON gd.id = v.geotab_database_id "
            "WHERE gd.database_name = ANY(:names)"
        ),
        {"names": list(_config_db_names())},
    ).all()
    return {tuple(r) for r in rows}


def _expected_motor_rules() -> set[tuple]:
    return {
        (mt, rn, rid)
        for mt, rules in config.RULES_BY_MOTOR.items()
        for rn, rid in rules.items()
    }


def _db_motor_rules(engine: Engine) -> set[tuple]:
    rows = engine.connect().execute(
        text("SELECT motor_type, rule_name, rule_id FROM motor_rules")
    ).all()
    return {tuple(r) for r in rows}


def _expected_event_rules() -> set[tuple]:
    return {
        (_db_name(db), rn, rid)
        for db, rules in config.EVENT_RULES.items()
        if _db_name(db) in {_db_name(d) for d in config.CREDENTIALS}
        for rn, rid in rules.items()
    }


def _db_event_rules(engine: Engine) -> set[tuple]:
    rows = engine.connect().execute(
        text(
            "SELECT gd.database_name, r.name, r.rule_id "
            "FROM geotab_rules r JOIN geotab_databases gd ON gd.id = r.geotab_database_id "
            "WHERE r.category = 'habito_seguro' AND gd.database_name = ANY(:names)"
        ),
        {"names": list(_config_db_names())},
    ).all()
    return {tuple(r) for r in rows}


def _expected_rpm_rules() -> set[tuple]:
    return {
        (rc, rid, ordinal)
        for rc, ids in config.RPM_RULES.items()
        for ordinal, rid in enumerate(ids, start=1)
    }


def _db_rpm_rules(engine: Engine) -> set[tuple]:
    rows = engine.connect().execute(
        text("SELECT rpm_class, rule_id, ordinal FROM rpm_rules")
    ).all()
    return {tuple(r) for r in rows}


def _expected_credentials() -> dict[str, set[tuple]]:
    out: dict[str, set[tuple]] = {}
    for db, creds in config.CREDENTIALS.items():
        out[_db_name(db)] = {(c["username"], c["password"]) for c in creds}
    return out


def _db_credentials(engine: Engine, fernet: Fernet) -> dict[str, set[tuple]]:
    rows = engine.connect().execute(
        text(
            "SELECT gd.database_name, gc.username, gc.password_enc "
            "FROM geotab_credentials gc JOIN geotab_databases gd ON gd.id = gc.geotab_database_id "
            "JOIN fleets f ON f.id = gd.fleet_id AND f.is_active "
            "WHERE gc.is_active AND gd.is_active AND gd.database_name = ANY(:names)"
        ),
        {"names": list(_config_db_names())},
    ).all()
    out: dict[str, set[tuple]] = {}
    for name, username, pwd_enc in rows:
        out.setdefault(name, set()).add(
            (username, fernet.decrypt(bytes(pwd_enc)).decode("utf-8"))
        )
    return out


def _check(label: str, expected: set, got: set) -> bool:
    if expected == got:
        log.info("OK  %s: %d filas", label, len(expected))
        return True
    log.error("FAIL %s: faltan=%s sobran=%s", label, expected - got, got - expected)
    return False


def verify(engine: Engine) -> bool:
    fernet = _fernet()
    ok = True
    ok &= _check("vehicles", _expected_vehicles(), _db_vehicles(engine))
    ok &= _check("motor_rules", _expected_motor_rules(), _db_motor_rules(engine))
    ok &= _check("event_rules", _expected_event_rules(), _db_event_rules(engine))
    ok &= _check("rpm_rules", _expected_rpm_rules(), _db_rpm_rules(engine))

    exp_cred = _expected_credentials()
    got_cred = _db_credentials(engine, fernet)
    if exp_cred == got_cred:
        log.info("OK  credentials: %d bases", len(exp_cred))
    else:
        # Nunca volcar las tuplas (usuario, contraseña): sólo qué usuarios
        # faltan o sobran por base (SEC-013).
        for name in sorted(set(exp_cred) | set(got_cred)):
            exp_users = {u for u, _ in exp_cred.get(name, set())}
            got_users = {u for u, _ in got_cred.get(name, set())}
            same_users = exp_users & got_users
            changed_pw = {
                u
                for u in same_users
                if {p for uu, p in exp_cred[name] if uu == u}
                != {p for uu, p in got_cred[name] if uu == u}
            }
            if exp_users != got_users or changed_pw:
                log.error(
                    "FAIL credentials %s: faltan_usuarios=%s sobran_usuarios=%s "
                    "contraseña_distinta=%s",
                    name,
                    sorted(exp_users - got_users),
                    sorted(got_users - exp_users),
                    sorted(changed_pw),
                )
        ok = False

    log.info("Paridad %s", "OK" if ok else "FALLÓ")
    return bool(ok)
