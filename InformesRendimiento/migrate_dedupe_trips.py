# ==============================================================================
# migrate_dedupe_trips.py — Migración puntual del silver de viajes
# ==============================================================================
# Motivo
# ------
# `fact_trips.trip_row_id` incluía el `id` de Geotab. Un Trip es un dato
# CALCULADO: el servidor lo reemplaza por un registro NUEVO con OTRO `id`
# cuando el viaje termina o cuando reprocesa el histórico. Con el `id` dentro
# de la clave, la reextracción de una ventana ya cargada insertaba el mismo
# viaje una segunda vez y duplicaba `Kms GPS`, `Hrs GPS` y `Tiempo en ralentí`
# del día.
#
# Esta migración:
#   1. recalcula `trip_row_id` con la clave estable (base, device, start);
#   2. colapsa los duplicados dejando la versión más avanzada del viaje
#      (mayor `stop`, luego mayor `distance`);
#   3. reescribe cada partición mensual de forma atómica.
#
# Es idempotente: correrla dos veces no cambia nada la segunda vez.
# Ejecutar UNA vez, antes de la siguiente corrida del ETL. Después,
# `transform_combustible` + carga semántica para corregir los hechos diarios.
# ==============================================================================
import argparse
import logging
import os

import pandas as pd

from config import FACTS_PATH
from utils import _write_parquet_atomic, make_trip_row_id

FACT_TRIPS_FILE = os.path.join(FACTS_PATH, 'fact_trips.parquet')

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')


def _rekey(df: pd.DataFrame) -> pd.DataFrame:
    return df.assign(
        trip_row_id=[
            make_trip_row_id(database_name, device_id, start)
            for database_name, device_id, start in zip(
                df['database_name'], df['device_id'], df['start']
            )
        ]
    )


def _collapse(df: pd.DataFrame) -> pd.DataFrame:
    """Deja una fila por trip_row_id: la versión más avanzada del viaje."""
    ordered = df.assign(
        _stop=pd.to_datetime(df['stop'], errors='coerce', utc=True),
        _dist=pd.to_numeric(df['distance'], errors='coerce'),
    ).sort_values(['_stop', '_dist'], na_position='first')
    collapsed = ordered.drop_duplicates(subset=['trip_row_id'], keep='last')
    return collapsed[df.columns]


def _partition_files(path: str) -> list:
    if os.path.isdir(path):
        return [
            os.path.join(path, name)
            for name in sorted(os.listdir(path))
            if name.endswith('.parquet') and not name.startswith(('.', '_'))
        ]
    return [path] if os.path.exists(path) else []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--apply',
        action='store_true',
        help='Escribe los cambios. Sin este flag solo reporta (dry-run).',
    )
    args = parser.parse_args()

    files = _partition_files(FACT_TRIPS_FILE)
    if not files:
        logging.warning('No hay particiones de fact_trips en %s', FACT_TRIPS_FILE)
        return

    total_before = total_after = 0
    for part_file in files:
        df = pd.read_parquet(part_file)
        if df.empty:
            continue
        rekeyed = _rekey(df)
        collapsed = _collapse(rekeyed)
        removed = len(rekeyed) - len(collapsed)
        total_before += len(df)
        total_after += len(collapsed)
        logging.info(
            '%s: %s filas → %s (%s duplicados colapsados)',
            os.path.basename(part_file),
            f'{len(df):,}',
            f'{len(collapsed):,}',
            f'{removed:,}',
        )
        if args.apply:
            _write_parquet_atomic(collapsed.reset_index(drop=True), part_file)

    logging.info(
        '%s: %s → %s filas (%s duplicados)%s',
        'APLICADO' if args.apply else 'DRY-RUN',
        f'{total_before:,}',
        f'{total_after:,}',
        f'{total_before - total_after:,}',
        '' if args.apply else '  — usar --apply para escribir',
    )


if __name__ == '__main__':
    main()
