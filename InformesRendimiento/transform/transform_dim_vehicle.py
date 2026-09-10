import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

import pandas as pd

from config import DIMS_PATH, SEMANTIC_DIMS_PATH

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

INPUT_FILE = os.path.join(DIMS_PATH, 'dim_vehiculos.parquet')
OUTPUT_FILE = os.path.join(SEMANTIC_DIMS_PATH, 'dim_vehicle.parquet')


def main():
    if not os.path.exists(INPUT_FILE):
        logging.info("No existe dim_vehiculos en silver.")
        return

    df = pd.read_parquet(INPUT_FILE)
    if df.empty:
        logging.info("dim_vehiculos esta vacia.")
        return

    df = df.rename(columns={'placa': 'vehicle_label'})
    columns = [
        'vehicle_id',
        'database_name',
        'device_id',
        'vehicle_label',
        'motor_type',
        'group_key',
        'rpm_class',
        'fuel_type_raw',
        'fuel_kind',
        'fuel_unit',
        'fuel_classification_source',
        'fuel_classification_conflict',
        'is_active',
    ]
    for column in columns:
        if column not in df.columns:
            df[column] = None

    os.makedirs(SEMANTIC_DIMS_PATH, exist_ok=True)
    df = df[columns].drop_duplicates(subset=['vehicle_id']).sort_values(['database_name', 'device_id'])
    df.to_parquet(OUTPUT_FILE, index=False, compression='snappy')
    logging.info(f"Guardado: {OUTPUT_FILE} ({len(df):,} registros)")


if __name__ == '__main__':
    main()
