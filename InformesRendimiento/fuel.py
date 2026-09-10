"""Clasificación canónica de combustible para el ETL.

La fuente primaria es ``vehicles.tipo_combustible``. Los motores N15 son el
fallback acordado para vehículos a gas cuando esa fuente viene vacía o no usa
un valor reconocido. Nunca se infiere gas por el prefijo ``gas`` porque
``gasolina`` es combustible líquido.
"""

from __future__ import annotations

import unicodedata
from typing import Any

FUEL_KIND_GAS = "gas"
FUEL_KIND_LIQUID = "liquid"
FUEL_UNIT_GAS = "m3"
FUEL_UNIT_LIQUID = "gal"

_GAS_MOTOR_TYPES = {"N15"}
_GAS_VALUES = {
    "CNG",
    "GAS",
    "GAS NATURAL",
    "GAS NATURAL COMPRIMIDO",
    "GAS NATURAL VEHICULAR",
    "GNC",
    "GNV",
}
_LIQUID_VALUES = {
    "ACPM",
    "DIESEL",
    "GASOLINA",
    "GASOLINE",
}


def normalize_fuel_label(value: Any) -> str:
    text = "" if value is None else str(value)
    ascii_text = (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    )
    return " ".join(ascii_text.upper().split())


def classify_fuel(
    raw_fuel_type: Any,
    motor_type: Any,
) -> tuple[str, str, str, bool]:
    """Devuelve ``(fuel_kind, fuel_unit, source, conflict)``.

    ``source`` permite auditar si ganó el maestro, el fallback N15 o el default
    compatible con el histórico. Un N15 declarado explícitamente como líquido
    conserva el maestro y se marca como conflicto; no se sobreescribe en
    silencio.
    """

    raw = normalize_fuel_label(raw_fuel_type)
    motor = normalize_fuel_label(motor_type)
    n15 = motor in _GAS_MOTOR_TYPES

    if raw in _GAS_VALUES:
        return FUEL_KIND_GAS, FUEL_UNIT_GAS, "vehicle_master", False
    if raw in _LIQUID_VALUES:
        return FUEL_KIND_LIQUID, FUEL_UNIT_LIQUID, "vehicle_master", n15
    if n15:
        return FUEL_KIND_GAS, FUEL_UNIT_GAS, "motor_n15_fallback", False
    return FUEL_KIND_LIQUID, FUEL_UNIT_LIQUID, "legacy_default", False
