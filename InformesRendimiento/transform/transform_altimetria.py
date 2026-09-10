import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

import pandas as pd

from config import DIAGNOSTIC_IDS, DIMS_PATH, FACTS_PATH, SEMANTIC_FACTS_PATH
from utils import date_to_key, read_facts

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DIAGNOSTIC_ID = DIAGNOSTIC_IDS['altimetria']
FACT_FILE = os.path.join(FACTS_PATH, 'fact_status_readings.parquet')
DIM_FILE = os.path.join(DIMS_PATH, 'dim_vehiculos.parquet')
OUTPUT_FILE = os.path.join(SEMANTIC_FACTS_PATH, 'fact_altimetria_reading.parquet')


def main():
    df = read_facts(FACT_FILE)
    df = df[(df['diagnostic_id'] == DIAGNOSTIC_ID) & (df['event_id'].isna())].copy()
    if df.empty:
        logging.info("No hay datos de altimetria.")
        return

    dim = pd.read_parquet(DIM_FILE)[['vehicle_id', 'placa']]
    df = df.merge(dim, on='vehicle_id', how='left')

    df['Fecha'] = pd.to_datetime(df['dateTime'])
    df['date_key'] = df['Fecha'].dt.date.apply(date_to_key)
    df = df.rename(columns={'placa': 'Placa', 'data': 'Valor'})

    result = df[[
        'row_id',
        'vehicle_id',
        'database_name',
        'date_key',
        'Placa',
        'Fecha',
        'Valor',
    ]].drop_duplicates(subset=['row_id']).sort_values(['vehicle_id', 'Fecha'])

    # Agregar índice numérico para habilitar el "Control Deslizante" en Power BI
    # Se ordena globalmente por Fecha para mantener el orden cronológico
    result = result.sort_values('Fecha')
    result['Indice'] = range(1, len(result) + 1)

    os.makedirs(SEMANTIC_FACTS_PATH, exist_ok=True)
    result.to_parquet(OUTPUT_FILE, index=False, compression='snappy')
    logging.info(f"Guardado: {OUTPUT_FILE} ({len(result):,} registros)")


if __name__ == '__main__':
    main()
