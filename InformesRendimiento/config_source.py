"""Lectura de la data maestra desde la DB del portal (fuente de verdad).

`load()` arma las mismas estructuras que config.py exponía hardcoded
(CREDENTIALS, VEHICLE_CATALOG, RULES_BY_DB_MOTOR, RULES_BY_MOTOR, EVENT_RULES,
EVENT_RULES_BY_MOTOR, RPM_RULES, VOLUMEN_TANQUES) leyendo las tablas del portal
vía SQLAlchemy y descifrando las credenciales con Fernet.

Las reglas de banda se indexan por (base, motor). Cada cliente tiene su propia
base Geotab y crea sus propias reglas para el mismo motor: son reglas distintas
con rule_id distinto, así que indexar solo por motor cruza bases y pide a una
base ids que no existen en ella.

Si `MASTER_DB_URL` no está, la DB no responde, falta la key, o no hay vehículos
sembrados, devuelve None para que config.py use su fallback hardcoded. Nunca
lanza: cualquier fallo se loguea y degrada a None.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("config_source")

# Banda declarada por la fuente maestra (`geotab_rule_applications.band`) ->
# banda canónica que esperan los transforms. Esta es la fuente PRIMARIA: Navi
# Vehículos declara explícitamente qué mide cada regla, sin depender del nombre.
_BAND_BY_ENUM: dict[str, str] = {
    "rango_bajo": "Rango Bajo",
    "rango_economico": "Rango Economico",
    "rango_balanceado": "Rango Balanceado",
    "rango_potencia": "Rango Potencia",
    "rango_potencia_ineficiente": "Rango Potencia Ineficiente",
    "exceso_rpm": "Exceso RPM",
    "ralenti": "Ralentí",
}

# FALLBACK (solo si `band` viene NULL): regla de banda de RPM -> banda canónica
# que esperan los transforms (transform_combustible: lista `rangos` y variantes
# `… Descenso`). Los nombres varían por base/motor ("Rango Bajo X11", "Rango
# Consumo X11", "Potencia Eficiente", "Rango Económico"…), así que se matchea por
# PALABRA CLAVE en orden de prioridad (gana el primero que coincida). El orden
# resuelve solapamientos de substring: 'ineficiente' contiene 'eficiente';
# 'potencia ineficiente' debe ganar sobre 'potencia', y 'potencia eficiente'
# mapea a Potencia, no a Económico.
_BAND_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("ineficiente", "consumo"), "Rango Potencia Ineficiente"),
    (("potencia",), "Rango Potencia"),
    (("balanceado",), "Rango Balanceado"),
    (("economico", "eficiente"), "Rango Economico"),
    (("bajo",), "Rango Bajo"),
    (("exceso",), "Exceso RPM"),
    (("ralenti",), "Ralentí"),
)


# Bandas del eje de RPM (`motor_rpm_bands.band`) -> banda canónica de los
# transforms. Es la MISMA nomenclatura que las bandas por regla: el modo por RPM
# solo cambia de dónde sale el tiempo, no cómo se llama. 'ralenti' no está: en
# ese modo el ralentí se deriva de la telemetría (encendido + velocidad 0).
_RPM_RANGE_BANDS: tuple[str, ...] = (
    "rango_bajo",
    "rango_economico",
    "rango_balanceado",
    "rango_potencia",
    "rango_potencia_ineficiente",
    "exceso_rpm",
)


def _normalize(text: str) -> str:
    """minúsculas, sin acentos, espacios colapsados."""
    import unicodedata

    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return " ".join(ascii_text.lower().split())


def _canonical_band(rule_name: str, motor_type: str | None = None) -> str | None:
    """Mapea el nombre de una regla a su banda canónica por palabra clave.

    Devuelve None si el nombre no corresponde a ninguna banda conocida. Si trae
    'descenso' (y la banda no es Ralentí) devuelve la variante en descenso
    ('Rango Bajo Descenso', …), que se grafica como dimensión aparte. Agnóstico
    al motor: no depende del sufijo del nombre."""
    name = _normalize(rule_name)
    if not name:
        return None
    is_descenso = "descenso" in name
    for keywords, band in _BAND_KEYWORDS:
        if any(kw in name for kw in keywords):
            return _with_descenso(band, is_descenso)
    return None


def _with_descenso(band: str, is_descenso: bool) -> str:
    """Aplica el sufijo ' Descenso'. Ralentí nunca lo lleva: los transforms no
    arman la dimensión 'Ralentí Descenso'."""
    if is_descenso and band != "Ralentí":
        return f"{band} Descenso"
    return band


def _resolve_band(
    rule_name: str,
    db_band: str | None,
    db_is_descenso: bool,
    database_name: str | None = None,
    rule_id: str | None = None,
) -> str | None:
    """Banda canónica de una regla: DB declarada primero, keyword como fallback.

    La fuente maestra (`geotab_rule_applications.band`) manda. Solo si viene NULL
    se infiere del nombre, y se avisa: el keyword-matching falla en silencio ante
    un renombre y desbalancea los % por rango. Si tampoco resuelve, se avisa
    explícitamente que la regla queda FUERA del cálculo de rangos."""
    if db_band:
        canonical = _BAND_BY_ENUM.get(db_band.strip().lower())
        if canonical:
            return _with_descenso(canonical, bool(db_is_descenso))
        log.warning(
            "config_source: banda desconocida %r en DB (base=%s, regla=%s, rule_id=%s); "
            "caigo a inferirla del nombre",
            db_band,
            database_name,
            rule_name,
            rule_id,
        )
    else:
        log.warning(
            "config_source: regla operacion sin banda declarada en la fuente maestra "
            "(base=%s, regla=%s, rule_id=%s); infiero la banda del nombre",
            database_name,
            rule_name,
            rule_id,
        )

    canonical = _canonical_band(rule_name)
    if canonical is None:
        log.warning(
            "config_source: regla operacion sin banda (base=%s, regla=%s, rule_id=%s); "
            "excluida del cálculo de rangos",
            database_name,
            rule_name,
            rule_id,
        )
    return canonical


def _build_rpm_bands(rows) -> dict[str, list[dict[str, Any]]]:
    """`{motor: [{'band','rpm_min','rpm_max'}...]}` con la partición del eje.

    Fail-closed POR MOTOR: si la configuración no cubre el eje completo, tiene
    huecos, solapes o un tramo intermedio abierto, ese motor se descarta entero
    y sus vehículos se quedan sin cálculo por RPM. Repartir tiempo con cortes
    inconsistentes es peor que no calcular: el portal ya reporta el motor sin
    rangos como problema de calidad de datos.
    """
    grouped: dict[str, dict[str, tuple[int, int | None]]] = {}
    poisoned: set[str] = set()
    for motor_type, band, rpm_min, rpm_max in rows:
        if not motor_type:
            continue
        key = str(band).strip().lower()
        if key not in _RPM_RANGE_BANDS:
            log.warning(
                "config_source: banda de RPM desconocida %r en motor %s; "
                "descarto sus rangos",
                band,
                motor_type,
            )
            poisoned.add(motor_type)
            continue
        grouped.setdefault(motor_type, {})[key] = (
            int(rpm_min),
            None if rpm_max is None else int(rpm_max),
        )

    result: dict[str, list[dict[str, Any]]] = {}
    for motor_type, bands in grouped.items():
        if motor_type in poisoned:
            continue
        missing = [band for band in _RPM_RANGE_BANDS if band not in bands]
        if missing:
            log.warning(
                "config_source: motor %s no cubre el eje de RPM (faltan %s); "
                "sus vehículos quedan sin cálculo por rangos de RPM",
                motor_type,
                ", ".join(missing),
            )
            continue

        ordered: list[dict[str, Any]] = []
        previous_max: int | None = None
        broken = False
        for position, band in enumerate(_RPM_RANGE_BANDS):
            rpm_min, rpm_max = bands[band]
            is_last = position == len(_RPM_RANGE_BANDS) - 1
            if rpm_min < 0 or (rpm_max is not None and rpm_max <= rpm_min):
                broken = True
            elif rpm_max is None and not is_last:
                broken = True
            elif previous_max is not None and previous_max != rpm_min:
                broken = True
            if broken:
                log.warning(
                    "config_source: rangos de RPM inconsistentes en motor %s "
                    "(banda %s: %s-%s); sus vehículos quedan sin cálculo",
                    motor_type,
                    band,
                    rpm_min,
                    rpm_max,
                )
                break
            previous_max = rpm_max
            ordered.append(
                {
                    "band": _BAND_BY_ENUM[band],
                    "rpm_min": rpm_min,
                    "rpm_max": rpm_max,
                }
            )
        if not broken:
            result[motor_type] = ordered
    return result


def _build(engine, fernet) -> dict[str, Any] | None:
    """Catálogo y credenciales, restringidos a FLOTAS ACTIVAS.

    Apagar una flota en el portal no desactiva sus vehículos ni sus bases uno por
    uno, así que sin el join a `fleets` el catálogo traía los 250 vehículos de las
    32 flotas y las 12 bases con credenciales. Eso inflaba `dim_vehiculos` (el
    loop de transform_combustible recorre esa dimensión) y hacía que
    extract_dimensions autenticara y bajara Diagnostic/Controller/FailureMode de
    las 12 bases en serie.

    Las dims ya cargadas en `analytics` no se pierden: el loader solo hace
    `INSERT ... ON CONFLICT DO UPDATE`, nunca borra, así que las filas de flotas
    apagadas dejan de refrescarse pero siguen respaldando las FK de hechos viejos.
    """
    from sqlalchemy import text

    with engine.connect() as conn:
        vehicles = conn.execute(
            text(
                "SELECT gd.database_name, v.geotab_device_id, v.plate, v.motor_type, "
                "v.group_key, v.rpm_class, v.tank_volume, v.tipo_combustible, "
                "f.range_mode, f.code, f.ralenti_analysis_enabled "
                "FROM vehicles v JOIN geotab_databases gd ON gd.id = v.geotab_database_id "
                "JOIN fleets f ON f.id = v.fleet_id AND f.is_active "
                "WHERE v.is_active AND gd.is_active AND v.geotab_device_id IS NOT NULL"
            )
        ).all()
        if not vehicles:
            log.warning("config_source: sin vehículos activos en DB; uso fallback")
            return None

        creds_rows = conn.execute(
            text(
                "SELECT gd.database_name, gc.username, gc.password_enc "
                "FROM geotab_credentials gc JOIN geotab_databases gd ON gd.id = gc.geotab_database_id "
                "JOIN fleets f ON f.id = gd.fleet_id AND f.is_active "
                "WHERE gc.is_active AND gd.is_active ORDER BY gd.database_name, gc.created_at, gc.username"
            )
        ).all()
        # Bandas de conducción (rango bajo/económico/…): el sync las guarda en
        # geotab_rule_applications (category 'operacion' + el 'exceso_rpm' por
        # motor), NO en la tabla motor_rules (que quedó vacía). `a.band` es la
        # banda declarada por Navi Vehículos; si viene NULL se infiere del nombre.
        #
        # El scope es (base, motor), NUNCA el motor solo: cada cliente tiene su
        # propia base Geotab y crea SUS reglas para el mismo motor, con rule_id
        # distinto. Dos bases con el mismo motor no se pueden cruzar; el join a
        # `fleets` activas evita además que una flota apagada aporte reglas. El
        # ORDER BY hace determinista el recorrido.
        motor_rows = conn.execute(
            text(
                "SELECT DISTINCT a.motor_type, r.name, r.rule_id, a.band, a.is_descenso, "
                "gd.database_name "
                "FROM geotab_rules r "
                "JOIN geotab_databases gd ON gd.id = r.geotab_database_id "
                "JOIN fleets f ON f.id = gd.fleet_id AND f.is_active "
                "JOIN geotab_rule_applications a ON a.geotab_rule_id = r.id "
                "WHERE a.motor_type IS NOT NULL AND a.is_active "
                "AND a.category = 'operacion' "
                "AND r.is_active AND gd.is_active "
                "ORDER BY gd.database_name, a.motor_type, r.name, r.rule_id"
            )
        ).all()
        event_rows = conn.execute(
            text(
                "SELECT DISTINCT gd.database_name, a.description, a.event_type, "
                "r.name, r.rule_id, a.motor_type "
                "FROM geotab_rules r "
                "JOIN geotab_databases gd ON gd.id = r.geotab_database_id "
                "JOIN geotab_rule_applications a ON a.geotab_rule_id = r.id "
                "WHERE a.category = 'habito_seguro' AND a.is_active "
                "AND r.is_active AND gd.is_active"
            )
        ).all()
        rpm_rows = conn.execute(
            text("SELECT rpm_class, rule_id, ordinal FROM rpm_rules ORDER BY rpm_class, ordinal")
        ).all()
        # Cortes del eje de RPM por motor (flotas con range_mode='rpm').
        rpm_band_rows = conn.execute(
            text(
                "SELECT motor_type, band, rpm_min, rpm_max FROM motor_rpm_bands "
                "ORDER BY motor_type, rpm_min"
            )
        ).all()

    vehicle_catalog: list[dict[str, Any]] = []
    volumen_tanques: dict[str, Any] = {}
    for (
        name,
        device_id,
        placa,
        motor_type,
        group_key,
        rpm_class,
        tank,
        tipo_combustible,
        range_mode,
        fleet_code,
        ralenti_analysis,
    ) in vehicles:
        vehicle_catalog.append(
            {
                "database_name": name,
                "device_id": device_id,
                "placa": placa,
                "motor_type": motor_type,
                "group_key": group_key,
                "rpm_class": rpm_class,
                "tipo_combustible": tipo_combustible,
                # Modo de la FLOTA, replicado en cada vehículo porque el ETL
                # razona por (base, device) y no tiene la dimensión flota.
                "range_mode": (range_mode or "reglas"),
                # Identidad de la flota y su flag de "Análisis de Ralentí"
                # (`fleets.ralenti_analysis_enabled`, lo activa el portal en
                # /gestion/flotas). Igual que range_mode: decisión de la flota,
                # replicada por vehículo. Sin él, extract_ralenti no extrae nada.
                "fleet_code": fleet_code,
                "ralenti_analysis": bool(ralenti_analysis),
            }
        )
        if tank is not None:
            volumen_tanques[placa] = tank

    credentials: dict[str, list[dict[str, str]]] = {}
    # geotab_databases tiene UNA fila por flota, así que la misma base física
    # (y la misma cuenta) llega repetida tantas veces como flotas la compartan.
    # Sin este corte, `navitrans` producía 12 entradas con 2 usuarios reales:
    # 12 hilos sobre 2 sesiones y 2 cuotas, es decir paralelismo falso y más
    # OverLimit. La identidad de una credencial es (base, usuario).
    seen_credentials: set[tuple[str, str]] = set()
    for name, username, pwd_enc in creds_rows:
        if (name, username) in seen_credentials:
            continue
        seen_credentials.add((name, username))
        # Una credencial indescifrable (seed de prueba, key rotada) NO debe
        # tumbar el catálogo entero al fallback hardcoded: se omite y se sigue.
        try:
            password = fernet.decrypt(bytes(pwd_enc)).decode("utf-8")
        except Exception:
            log.warning(
                "config_source: credencial indescifrable en %s (user=%s); la omito",
                name,
                username,
            )
            continue
        credentials.setdefault(name, []).append(
            {"username": username, "password": password}
        )

    # Estructura de verdad: {base: {motor: {banda: rule_id}}}. `rules_by_motor`
    # se conserva SOLO para verify_master_data/seed_master_data, que comparan
    # catálogos; no sirve para extraer ni transformar porque colapsa bases.
    rules_by_db_motor: dict[str, dict[str, dict[str, str]]] = {}
    rules_by_motor: dict[str, dict[str, str]] = {}
    for motor_type, rule_name, rule_id, band, is_descenso, database_name in motor_rows:
        canonical = _resolve_band(rule_name, band, is_descenso, database_name, rule_id)
        if not canonical:
            continue
        scoped = rules_by_db_motor.setdefault(database_name, {}).setdefault(
            motor_type, {}
        )
        previous = scoped.get(canonical)
        if previous is not None and previous != rule_id:
            # Dos reglas activas para la misma banda dentro de la MISMA base y
            # motor: el catálogo está ambiguo y la elección sería arbitraria.
            log.warning(
                "config_source: banda %s duplicada en base=%s motor=%s "
                "(rule_id %s y %s); conservo la primera",
                canonical,
                database_name,
                motor_type,
                previous,
                rule_id,
            )
        else:
            scoped[canonical] = rule_id
        rules_by_motor.setdefault(motor_type, {})[canonical] = rule_id

    event_rules: dict[str, dict[str, str]] = {}
    event_rules_by_motor: dict[str, dict[str, dict[str, str]]] = {}
    for name, description, event_type, rule_name, rule_id, motor_type in event_rows:
        if description is None:
            # Compatibilidad antigua basada en semántica declarada, nunca en el
            # nombre físico para Exceso de RPM.
            description = (
                "Excesos de RPM" if event_type == "exceso_rpm" else rule_name
            )
        if motor_type is None:
            event_rules.setdefault(name, {})[description] = rule_id
        else:
            event_rules_by_motor.setdefault(name, {}).setdefault(motor_type, {})[
                description
            ] = rule_id

    rpm_rules: dict[str, list[str]] = {}
    for rpm_class, rule_id, _ordinal in rpm_rows:
        rpm_rules.setdefault(rpm_class, []).append(rule_id)

    rpm_bands_by_motor = _build_rpm_bands(rpm_band_rows)

    return {
        "credentials": credentials,
        "vehicle_catalog": vehicle_catalog,
        "rules_by_db_motor": rules_by_db_motor,
        "rules_by_motor": rules_by_motor,
        "event_rules": event_rules,
        "event_rules_by_motor": event_rules_by_motor,
        "rpm_rules": rpm_rules,
        "rpm_bands_by_motor": rpm_bands_by_motor,
        "volumen_tanques": volumen_tanques,
    }


def load() -> dict[str, Any] | None:
    """Devuelve la data maestra desde la DB, o None para usar el fallback."""
    url = os.environ.get("MASTER_DB_URL")
    if not url:
        return None
    key = os.environ.get("MASTER_FERNET_KEY")
    if not key:
        log.warning("config_source: MASTER_FERNET_KEY ausente; uso fallback")
        return None
    try:
        from cryptography.fernet import Fernet
        from sqlalchemy import create_engine

        # hide_parameters: esta conexión lee credenciales cifradas; un error
        # de SQLAlchemy no debe volcarlas en el log.
        engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
        result = _build(engine, Fernet(key.encode("utf-8")))
        engine.dispose()
        if result is not None:
            log.info(
                "config_source: data maestra desde DB (%d vehículos, %d bases, "
                "%d motores con rangos de RPM)",
                len(result["vehicle_catalog"]),
                len(result["credentials"]),
                len(result["rpm_bands_by_motor"]),
            )
        return result
    except Exception as exc:  # degradar a fallback ante cualquier fallo
        log.warning("config_source: fallo leyendo DB (%s); uso fallback", exc)
        return None
