import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

import pandas as pd

from config import FACTS_PATH, QA_REPORTS_PATH
from utils import read_facts

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

CHECKS = [
    ('fact_status_readings.parquet', 'row_id'),
    ('fact_fault_data.parquet', 'row_id'),
    ('fact_exception_events.parquet', 'event_sk'),
    ('fact_log_records.parquet', 'log_row_id'),
    ('fact_trips.parquet', 'trip_row_id'),
    # Agregado (vehículo/día/banda) del modo por rangos de RPM.
    ('fact_rpm_band_daily.parquet', 'row_id'),
]


def _check_file(file_name: str, pk_column: str) -> dict:
    path = os.path.join(FACTS_PATH, file_name)
    if not os.path.exists(path):
        return {'table': file_name, 'pk_column': pk_column, 'status': 'missing'}

    df = read_facts(path)
    if pk_column not in df.columns:
        return {'table': file_name, 'pk_column': pk_column, 'status': 'missing_pk'}

    total_rows = len(df)
    null_rows = int(df[pk_column].isna().sum())
    duplicate_rows = int(df.duplicated(subset=[pk_column]).sum())
    status = 'ok' if null_rows == 0 and duplicate_rows == 0 else 'invalid'
    return {
        'table': file_name,
        'pk_column': pk_column,
        'status': status,
        'rows': total_rows,
        'null_rows': null_rows,
        'duplicate_rows': duplicate_rows,
    }


def main():
    results = [_check_file(file_name, pk_column) for file_name, pk_column in CHECKS]
    os.makedirs(QA_REPORTS_PATH, exist_ok=True)
    output_file = os.path.join(QA_REPORTS_PATH, 'validate_silver_keys.parquet')
    pd.DataFrame(results).to_parquet(output_file, index=False, compression='snappy')
    logging.info(f"Guardado: {output_file} ({len(results):,} verificaciones)")


if __name__ == '__main__':
    main()
