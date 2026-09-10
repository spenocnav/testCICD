import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

import pandas as pd

from config import DIAGNOSTIC_IDS, DIMS_PATH, FACTS_PATH, SEMANTIC_FACTS_PATH
from utils import date_to_key, read_facts

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DIAGNOSTIC_ID = DIAGNOSTIC_IDS['pedal']
FACT_FILE = os.path.join(FACTS_PATH, 'fact_status_readings.parquet')
DIM_FILE = os.path.join(DIMS_PATH, 'dim_vehiculos.parquet')
OUTPUT_FILE = os.path.join(SEMANTIC_FACTS_PATH, 'fact_pedal_reading.parquet')

COLOMBIA_OFFSET = timedelta(hours=5)


def main():
    df = read_facts(FACT_FILE)
    df = df[
        (df['diagnostic_id'] == DIAGNOSTIC_ID) &
        (df['event_id'].isna()) &
        (df['data'] > 0)
    ].copy()
    if df.empty:
        logging.info("No hay datos de posicion de pedal.")
        return

    dim = pd.read_parquet(DIM_FILE)[['vehicle_id', 'placa']]
    df = df.merge(dim, on='vehicle_id', how='left')

    df['Fecha y Hora'] = pd.to_datetime(df['dateTime'])
    df['Fecha'] = (df['Fecha y Hora'] - COLOMBIA_OFFSET).dt.date
    df['date_key'] = df['Fecha'].apply(date_to_key)
    df['Valor'] = df['data']
    df['Unidad de Medida'] = '%'
    df = df.rename(columns={'placa': 'Placa'})

    result = df[[
        'row_id',
        'vehicle_id',
        'database_name',
        'date_key',
        'Fecha',
        'Placa',
        'Fecha y Hora',
        'Valor',
        'Unidad de Medida',
    ]].drop_duplicates(subset=['row_id']).sort_values(['vehicle_id', 'Fecha y Hora'])

    os.makedirs(SEMANTIC_FACTS_PATH, exist_ok=True)
    result.to_parquet(OUTPUT_FILE, index=False, compression='snappy')
    logging.info(f"Guardado: {OUTPUT_FILE} ({len(result):,} registros)")


if __name__ == '__main__':
    main()
