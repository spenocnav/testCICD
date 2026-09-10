import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

import pandas as pd

from config import DIAGNOSTIC_IDS, DIMS_PATH, FACTS_PATH, SEMANTIC_FACTS_PATH
from utils import COLOMBIA_OFFSET, date_to_key, make_row_id, read_facts

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DIAGNOSTIC_ID = DIAGNOSTIC_IDS['factor_carga']
FACT_FILE = os.path.join(FACTS_PATH, 'fact_status_readings.parquet')
DIM_FILE = os.path.join(DIMS_PATH, 'dim_vehiculos.parquet')
OUTPUT_FILE = os.path.join(SEMANTIC_FACTS_PATH, 'fact_factor_carga_daily.parquet')

OUTPUT_COLUMNS = [
    'fact_row_id',
    'vehicle_id',
    'database_name',
    'date_key',
    'Fecha',
    'Placa',
    'Factor de Carga',
]


def transform_factor_carga(raw: pd.DataFrame, vehicles: pd.DataFrame) -> pd.DataFrame:
    """Transforma lecturas crudas en hechos diarios sin tocar disco ni DB.

    Es el contrato compartido por el pipeline Parquet y el pipeline directo.
    La deduplicación reproduce ``merge_staging_to_main(..., ['row_id'])`` para
    que un micro-lote reintentado produzca exactamente la misma fila diaria.
    """
    if raw.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    required = {'diagnostic_id', 'event_id', 'data', 'dateTime', 'vehicle_id', 'database_name'}
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError(f"factor_carga: faltan columnas crudas: {sorted(missing)}")

    df = raw.copy()
    if 'row_id' in df.columns:
        df = df.drop_duplicates(subset=['row_id'], keep='first')
    df = df[
        (df['diagnostic_id'] == DIAGNOSTIC_ID) &
        (df['event_id'].isna()) &
        (df['data'] > 0)
    ].copy()
    if df.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    df['dateTime'] = pd.to_datetime(df['dateTime'])
    df['Fecha'] = (df['dateTime'] - COLOMBIA_OFFSET).dt.date

    grouped = df.groupby(
        ['vehicle_id', 'database_name', 'Fecha'],
        sort=True,
    )['data'].mean().reset_index()
    grouped.columns = ['vehicle_id', 'database_name', 'Fecha', 'Factor de Carga']

    dim = vehicles.copy()
    if 'placa' not in dim.columns and 'vehicle_label' in dim.columns:
        dim = dim.rename(columns={'vehicle_label': 'placa'})
    if not {'vehicle_id', 'placa'} <= set(dim.columns):
        raise ValueError("factor_carga: dimensión de vehículos incompleta")
    dim = dim[['vehicle_id', 'placa']].drop_duplicates(subset=['vehicle_id'])

    grouped = grouped.merge(dim, on='vehicle_id', how='left').rename(columns={'placa': 'Placa'})
    grouped['date_key'] = grouped['Fecha'].apply(date_to_key)
    grouped['fact_row_id'] = grouped.apply(
        lambda row: make_row_id(row['vehicle_id'], row['Fecha'], 'factor_carga_daily'),
        axis=1,
    )

    return (
        grouped[OUTPUT_COLUMNS]
        .drop_duplicates(subset=['fact_row_id'])
        .sort_values(['vehicle_id', 'Fecha'])
        .reset_index(drop=True)
    )


def main():
    df = read_facts(FACT_FILE)
    dim = pd.read_parquet(DIM_FILE)
    result = transform_factor_carga(df, dim)
    if result.empty:
        logging.info("No hay datos de factor de carga.")
        return

    os.makedirs(SEMANTIC_FACTS_PATH, exist_ok=True)
    result.to_parquet(OUTPUT_FILE, index=False, compression='snappy')
    logging.info(f"Guardado: {OUTPUT_FILE} ({len(result):,} registros)")


if __name__ == '__main__':
    main()
