"""Normalización de nombres de columna parquet → PostgreSQL.

Los parquet usan nombres humanos (`"Kms ECM"`, `"% Rango Bajo"`, `"Revisión"`).
Para que el portal tenga modelos limpios, al escribir a PG se normalizan a
snake_case ASCII.
"""

from __future__ import annotations

import re
import unicodedata


def normalize_column(name: str) -> str:
    """Convierte un nombre de columna humano a snake_case ASCII.

    Reglas:
      - minúsculas, sin acentos (NFKD + drop combining)
      - `%` → `pct`, `/` → `_`, `-` → `_`
      - cualquier no-alfanumérico → `_`, colapsar `__` → `_`, strip extremos
    """
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("%", "pct").replace("/", "_").replace("-", "_")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s
