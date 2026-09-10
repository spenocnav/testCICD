"""Orquestador de carga: parquet semántico → PostgreSQL (schema `analytics`).

Carga dims primero y luego facts (respeta FKs). Idempotente (upsert por PK).
Si `ANALYTICS_DB_URL` no está configurada, omite la carga sin romper el pipeline.

Carga incremental (Fase A): el transform sigue produciendo el semántico COMPLETO
y correcto; el loader calcula un hash de contenido por fila y lo compara contra un
manifiesto del último load (`PK → hash`). Solo upsertea filas nuevas o cambiadas.
Equivalencia: saltar una fila cuyo hash no cambió la deja idéntica en PG, así que
el estado final es el mismo que re-upsertear todo. La 1ra corrida (sin manifiesto)
sube todo, igual que antes. `LOAD_FULL_UPSERT=1` fuerza el re-upsert total
(reconciliación / comportamiento previo exacto).
"""

from __future__ import annotations

import logging
import os

import pandas as pd

import config
from load import db, schema
from load.fuel_contract import ensure_fuel_contract
from load.distance_quality import record_automatic_distance_decisions
from load.naming import normalize_column

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

_HASH_COL = "__rowhash"
_STREAMING_TABLES = {
    "fact_factor_carga_daily"
    for dataset in os.environ.get("ETL_STREAMING_DATASETS", "").split(",")
    if dataset.strip() == "factor_carga"
}
if os.environ.get("ETL_FAULTS_REALTIME_ENABLED", "false").lower() in {"1", "true", "yes"}:
    _STREAMING_TABLES.add("fact_fault_event")
# Los episodios de ralentí los escribe extract_ralenti.py directo en PostgreSQL;
# nunca hay parquet semántico que cargar.
_STREAMING_TABLES.add("fact_ralenti_event")


def _path(spec: schema.TableSpec) -> str:
    base = config.SEMANTIC_DIMS_PATH if spec.kind == "dim" else config.SEMANTIC_FACTS_PATH
    return os.path.join(base, spec.parquet_name)


def _manifest_path(spec: schema.TableSpec) -> str:
    return os.path.join(config.LOAD_MANIFEST_PATH, f"{spec.pg_table}.parquet")


def _read_manifest(spec: schema.TableSpec) -> pd.DataFrame | None:
    path = _manifest_path(spec)
    if not os.path.exists(path):
        return None
    try:
        return pd.read_parquet(path)
    except Exception:
        logging.warning("Manifiesto ilegible para %s; se hará upsert completo", spec.pg_table)
        return None


def _write_manifest(spec: schema.TableSpec, frame_norm: pd.DataFrame, hashes: pd.Series) -> None:
    pk = [normalize_column(c) for c in spec.pk]
    manifest = frame_norm[pk].copy()
    manifest[_HASH_COL] = hashes.values
    os.makedirs(config.LOAD_MANIFEST_PATH, exist_ok=True)
    tmp = _manifest_path(spec) + ".tmp"
    manifest.to_parquet(tmp, index=False, compression="snappy")
    os.replace(tmp, _manifest_path(spec))


def _changed_mask(
    frame_norm: pd.DataFrame, spec: schema.TableSpec, hashes: pd.Series,
    manifest: pd.DataFrame | None,
) -> pd.Series:
    """True para filas nuevas o cuyo hash difiere del manifiesto. Diff por PK
    mediante dict (robusto al orden), no por merge."""
    if manifest is None or manifest.empty or _HASH_COL not in manifest.columns:
        return pd.Series(True, index=frame_norm.index)
    pk = [normalize_column(c) for c in spec.pk]
    old = dict(zip(map(tuple, manifest[pk].to_numpy()), manifest[_HASH_COL].to_numpy()))
    new_keys = map(tuple, frame_norm[pk].to_numpy())
    changed = [old.get(k) != h for k, h in zip(new_keys, hashes.to_numpy())]
    return pd.Series(changed, index=frame_norm.index)


def _fk_keep_mask(
    engine, spec: schema.TableSpec, frame_norm: pd.DataFrame,
) -> tuple[pd.Series, dict[str, int]]:
    """Máscara de filas cargables + conteo de descartes por columna FK.

    Los facts del lago son acumulativos y las dims se reconstruyen desde el
    catálogo activo, así que un vehículo que sale del catálogo deja sus hechos
    históricos apuntando a una `dim_vehicle` que ya no lo tiene. Antes esto
    reventaba la carga entera con un ForeignKeyViolation en el primer fact
    afectado, y las tablas siguientes no se cargaban. Se descartan solo esas
    filas. NULL es válido para una FK, así que no cuenta como huérfana.
    """
    keep = pd.Series(True, index=frame_norm.index)
    dropped: dict[str, int] = {}
    for column, ref_table, ref_column in spec.fks:
        column_n = normalize_column(column)
        if column_n not in frame_norm.columns:
            continue
        keys = db.fetch_key_set(engine, ref_table, normalize_column(ref_column))
        if keys is None:
            continue  # dim aún no creada (primera corrida): la FK tampoco existe
        values = frame_norm[column_n]
        valid = values.isna() | values.isin(keys)
        n_dropped = int((~valid & keep).sum())
        if n_dropped:
            dropped[column_n] = n_dropped
        keep &= valid
    return keep, dropped


def main() -> None:
    try:
        engine = db.get_engine()
    except ValueError as exc:
        logging.warning("Loader omitido: %s", exc)
        return

    db.ensure_schema(engine)
    ensure_fuel_contract(engine, config.ANALYTICS_DB_SCHEMA)
    full = config.LOAD_FULL_UPSERT

    loaded: list[schema.TableSpec] = []
    for spec in schema.TABLES:
        if spec.pg_table in _STREAMING_TABLES:
            logging.info(
                "Carga legacy omitida para %s (dataset en modo directo)",
                spec.pg_table,
            )
            continue
        if spec.pg_table in config.DISABLED_TABLES:
            # Sin extracción no hay filas nuevas, pero el hash de fila sí podía
            # moverse (p. ej. el `Indice` global de ubicaciones se recorre cuando
            # otro dominio agrega filas al mismo silver), y eso disparaba un
            # re-upsert de la tabla entera con delta cero.
            logging.info("Carga omitida para %s (dominio desactivado)", spec.pg_table)
            continue
        path = _path(spec)
        if not os.path.exists(path):
            logging.warning("Falta %s, se omite", spec.parquet_name)
            continue
        frame = pd.read_parquet(path)
        db.create_table(engine, spec, frame)
        db.create_indexes(engine, spec, frame)

        frame_norm = db._normalized(frame)
        hashes = db.row_hashes(frame_norm)

        keep, dropped = _fk_keep_mask(engine, spec, frame_norm)
        if dropped:
            keep_np = keep.to_numpy()
            logging.warning(
                "%s: %d de %d filas descartadas por dimensión ausente (%s)",
                spec.pg_table, int((~keep).sum()), len(frame),
                ", ".join(f"{col}={n}" for col, n in sorted(dropped.items())),
            )
            frame = frame[keep_np]
            frame_norm = frame_norm[keep_np]
            hashes = hashes[keep_np]

        if full:
            to_upsert = frame
            n_skip = 0
        else:
            mask = _changed_mask(frame_norm, spec, hashes, _read_manifest(spec))
            to_upsert = frame[mask.to_numpy()]
            n_skip = len(frame) - len(to_upsert)

        db.upsert_dataframe(engine, spec, to_upsert)
        # Manifiesto = todas las filas CARGABLES (no solo las upserteadas), para que
        # las saltadas sigan marcadas como "sin cambios" en la próxima corrida. Las
        # descartadas por FK quedan fuera a propósito: si su dimensión vuelve, la
        # próxima corrida las ve como nuevas y las sube.
        _write_manifest(spec, frame_norm, hashes)
        loaded.append(spec)
        logging.info(
            "Cargada %s.%s (%d upsert, %d sin cambios)",
            config.ANALYTICS_DB_SCHEMA, spec.pg_table, len(to_upsert), n_skip,
        )

    # FKs al final: todas las dims ya existen.
    for spec in loaded:
        db.add_fk_constraints(engine, spec)

    recorded = record_automatic_distance_decisions(
        engine, config.ANALYTICS_DB_SCHEMA
    )
    logging.info("Decisiones automáticas de distancia auditadas: %d", recorded)

    logging.info("Carga completa: %d tablas", len(loaded))


if __name__ == "__main__":
    main()
