import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

import pandas as pd

from config import DIMS_PATH, SEMANTIC_DIMS_PATH

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

INPUT_FILE = os.path.join(DIMS_PATH, 'dim_reglas.parquet')
OUTPUT_FILE = os.path.join(SEMANTIC_DIMS_PATH, 'dim_rule.parquet')


def main():
    if not os.path.exists(INPUT_FILE):
        logging.info("No existe dim_reglas en silver.")
        return

    df = pd.read_parquet(INPUT_FILE)
    if df.empty:
        logging.info("dim_reglas esta vacia.")
        return

    columns = [
        'rule_sk',
        'rule_id',
        'rule_name',
        'source_script',
        'scope_type',
        'scope_value',
        'categoria',
    ]
    for column in columns:
        if column not in df.columns:
            df[column] = None

    os.makedirs(SEMANTIC_DIMS_PATH, exist_ok=True)
    df = df[columns].drop_duplicates(subset=['rule_sk']).sort_values(['source_script', 'scope_type', 'scope_value', 'rule_name'])
    df.to_parquet(OUTPUT_FILE, index=False, compression='snappy')
    logging.info(f"Guardado: {OUTPUT_FILE} ({len(df):,} registros)")


if __name__ == '__main__':
    main()
