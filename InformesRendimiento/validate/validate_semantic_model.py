import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

import pandas as pd

from config import QA_REPORTS_PATH, SEMANTIC_DIMS_PATH, SEMANTIC_FACTS_PATH

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DIM_CHECKS = [
    ('dim_vehicle.parquet', 'vehicle_id'),
    ('dim_date.parquet', 'date_key'),
    ('dim_rule.parquet', 'rule_sk'),
    ('dim_diagnostic.parquet', 'diagnostic_sk'),
    ('dim_controller.parquet', 'controller_sk'),
    ('dim_failure_mode.parquet', 'failure_mode_sk'),
]

FACT_CHECKS = [
    ('fact_combustible_daily.parquet', 'fact_row_id', 'vehicle_id', 'date_key', []),
    ('fact_combustible_monthly.parquet', 'fact_row_id', 'vehicle_id', 'month_start_date_key', []),
    ('fact_habito_event.parquet', 'event_sk', 'vehicle_id', 'date_key', [('rule_sk', 'dim_rule.parquet', 'rule_sk')]),
    (
        'fact_fault_event.parquet',
        'row_id',
        'vehicle_id',
        'date_key',
        [
            ('diagnostic_sk', 'dim_diagnostic.parquet', 'diagnostic_sk'),
            ('controller_sk', 'dim_controller.parquet', 'controller_sk'),
            ('failure_mode_sk', 'dim_failure_mode.parquet', 'failure_mode_sk'),
        ],
    ),
    # Carga directa desde extract_ralenti.py: sin parquet semántico, así que aquí
    # siempre reporta 'missing'. Está en la lista por el contrato de PK con
    # load/schema.py (tests/test_schema_contract.py).
    ('fact_ralenti_event.parquet', 'event_sk', 'vehicle_id', 'date_key', []),
    ('fact_factor_carga_daily.parquet', 'fact_row_id', 'vehicle_id', 'date_key', []),
    ('fact_def_daily.parquet', 'fact_row_id', 'vehicle_id', 'date_key', []),
    ('fact_altimetria_reading.parquet', 'row_id', 'vehicle_id', 'date_key', []),
    ('fact_pedal_reading.parquet', 'row_id', 'vehicle_id', 'date_key', []),
    ('fact_location_point.parquet', 'log_row_id', 'vehicle_id', 'date_key', []),
]


def _load_dim_values(file_name: str, key_column: str) -> set:
    path = os.path.join(SEMANTIC_DIMS_PATH, file_name)
    if not os.path.exists(path):
        return set()
    df = pd.read_parquet(path)
    if key_column not in df.columns:
        return set()
    return set(df[key_column].dropna().tolist())


def _validate_pk(path: str, pk_column: str) -> tuple[bool, int, int]:
    df = pd.read_parquet(path)
    if pk_column not in df.columns:
        return False, 0, 0
    return True, int(df[pk_column].isna().sum()), int(df.duplicated(subset=[pk_column]).sum())


def _missing_fk_count(df: pd.DataFrame, fk_column: str, valid_values: set) -> int:
    if fk_column not in df.columns:
        return len(df)
    return int((~df[fk_column].isin(valid_values)).sum())


def main():
    results = []

    for file_name, pk_column in DIM_CHECKS:
        path = os.path.join(SEMANTIC_DIMS_PATH, file_name)
        if not os.path.exists(path):
            results.append({'table': file_name, 'status': 'missing'})
            continue

        has_pk, null_rows, duplicate_rows = _validate_pk(path, pk_column)
        if not has_pk:
            results.append({
                'table': file_name,
                'pk_column': pk_column,
                'status': 'missing_pk',
            })
            continue

        results.append({
            'table': file_name,
            'pk_column': pk_column,
            'status': 'ok' if null_rows == 0 and duplicate_rows == 0 else 'invalid',
            'null_rows': null_rows,
            'duplicate_rows': duplicate_rows,
        })

    dim_cache = {
        ('dim_vehicle.parquet', 'vehicle_id'): _load_dim_values('dim_vehicle.parquet', 'vehicle_id'),
        ('dim_date.parquet', 'date_key'): _load_dim_values('dim_date.parquet', 'date_key'),
        ('dim_rule.parquet', 'rule_sk'): _load_dim_values('dim_rule.parquet', 'rule_sk'),
        ('dim_diagnostic.parquet', 'diagnostic_sk'): _load_dim_values('dim_diagnostic.parquet', 'diagnostic_sk'),
        ('dim_controller.parquet', 'controller_sk'): _load_dim_values('dim_controller.parquet', 'controller_sk'),
        ('dim_failure_mode.parquet', 'failure_mode_sk'): _load_dim_values('dim_failure_mode.parquet', 'failure_mode_sk'),
    }

    for file_name, pk_column, fk_vehicle, fk_date, extra_fks in FACT_CHECKS:
        path = os.path.join(SEMANTIC_FACTS_PATH, file_name)
        if not os.path.exists(path):
            results.append({'table': file_name, 'status': 'missing'})
            continue

        df = pd.read_parquet(path)
        if pk_column not in df.columns:
            results.append({
                'table': file_name,
                'pk_column': pk_column,
                'status': 'missing_pk',
            })
            continue

        null_rows = int(df[pk_column].isna().sum())
        duplicate_rows = int(df.duplicated(subset=[pk_column]).sum())
        missing_vehicle_fk = _missing_fk_count(df, fk_vehicle, dim_cache[('dim_vehicle.parquet', 'vehicle_id')])
        missing_date_fk = _missing_fk_count(df, fk_date, dim_cache[('dim_date.parquet', 'date_key')]) if fk_date else 0
        missing_extra_fk = 0
        for fk_column, dim_file, dim_pk in extra_fks:
            missing_extra_fk += _missing_fk_count(df, fk_column, dim_cache[(dim_file, dim_pk)])

        status = (
            'ok'
            if null_rows == 0
            and duplicate_rows == 0
            and missing_vehicle_fk == 0
            and missing_date_fk == 0
            and missing_extra_fk == 0
            else 'invalid'
        )
        results.append({
            'table': file_name,
            'pk_column': pk_column,
            'status': status,
            'null_rows': null_rows,
            'duplicate_rows': duplicate_rows,
            'missing_vehicle_fk': missing_vehicle_fk,
            'missing_date_fk': missing_date_fk,
            'missing_extra_fk': missing_extra_fk,
        })

    os.makedirs(QA_REPORTS_PATH, exist_ok=True)
    output_file = os.path.join(QA_REPORTS_PATH, 'validate_semantic_model.parquet')
    pd.DataFrame(results).to_parquet(output_file, index=False, compression='snappy')
    logging.info(f"Guardado: {output_file} ({len(results):,} verificaciones)")


if __name__ == '__main__':
    main()
