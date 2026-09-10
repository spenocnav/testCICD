import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

import pandas as pd

from config import DIMS_PATH, SEMANTIC_DIMS_PATH

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DIM_MAP = {
    'dim_diagnosticos.parquet': ('dim_diagnostic.parquet', ['diagnostic_sk', 'database_name', 'diagnostic_id', 'name', 'code', 'source_id']),
    'dim_controllers.parquet': ('dim_controller.parquet', ['controller_sk', 'database_name', 'controller_id', 'name', 'code']),
    'dim_failure_modes.parquet': ('dim_failure_mode.parquet', ['failure_mode_sk', 'database_name', 'failure_mode_id', 'name', 'code']),
}


def main():
    os.makedirs(SEMANTIC_DIMS_PATH, exist_ok=True)

    for source_name, (target_name, columns) in DIM_MAP.items():
        source_file = os.path.join(DIMS_PATH, source_name)
        if not os.path.exists(source_file):
            logging.info(f"No existe {source_name} en silver.")
            continue

        df = pd.read_parquet(source_file)
        if df.empty:
            logging.info(f"{source_name} esta vacia.")
            continue

        for column in columns:
            if column not in df.columns:
                df[column] = None

        target_file = os.path.join(SEMANTIC_DIMS_PATH, target_name)
        pk = columns[0]
        df = df[columns].drop_duplicates(subset=[pk]).sort_values(columns[:2])
        df.to_parquet(target_file, index=False, compression='snappy')
        logging.info(f"Guardado: {target_file} ({len(df):,} registros)")


if __name__ == '__main__':
    main()
