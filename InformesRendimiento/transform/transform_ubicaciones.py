import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

import pandas as pd

from config import DIMS_PATH, FACTS_PATH, SEMANTIC_FACTS_PATH
from utils import date_to_key, read_facts

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

FACT_FILE = os.path.join(FACTS_PATH, 'fact_log_records.parquet')
DIM_FILE = os.path.join(DIMS_PATH, 'dim_vehiculos.parquet')
OUTPUT_FILE = os.path.join(SEMANTIC_FACTS_PATH, 'fact_location_point.parquet')


def main():
    df = read_facts(FACT_FILE)
    df = df[df['event_id'].isna()].copy()
    if df.empty:
        logging.info("No hay datos de ubicaciones.")
        return

    dim = pd.read_parquet(DIM_FILE)[['vehicle_id', 'placa']]
    df = df.merge(dim, on='vehicle_id', how='left')

    df['Fecha y Hora'] = pd.to_datetime(df['dateTime'])
    df['Fecha'] = df['Fecha y Hora'].dt.date
    df['date_key'] = df['Fecha'].apply(date_to_key)
    df = df.rename(columns={'placa': 'Placa'})

    result = df[[
        'log_row_id',
        'geotab_id',
        'vehicle_id',
        'database_name',
        'date_key',
        'Fecha',
        'Fecha y Hora',
        'Placa',
        'speed',
        'longitude',
        'latitude',
    ]].drop_duplicates(subset=['log_row_id']).sort_values(['vehicle_id', 'Fecha y Hora'])

    # Índice numérico para el "Control Deslizante" en Power BI: solo necesita ser
    # monótono en el tiempo, no denso.
    #
    # Antes era `range(1, len(result) + 1)` sobre el histórico ordenado. Un
    # contador denso se corre ENTERO cuando entra una fila en el medio: agregar un
    # día viejo cambiaba el `Indice` de todas las filas posteriores, y como la
    # columna entra en el hash de fila, el loader re-upserteaba los 3,3M de
    # registros para un delta de cero.
    #
    # Derivarlo del propio timestamp lo hace estable: una fila conserva su valor
    # aunque se inserten otras antes o después.
    result = result.sort_values('Fecha y Hora')
    result['Indice'] = (
        pd.to_datetime(result['Fecha y Hora']).astype('int64') // 10**9
    )

    os.makedirs(SEMANTIC_FACTS_PATH, exist_ok=True)
    result.to_parquet(OUTPUT_FILE, index=False, compression='snappy')
    logging.info(f"Guardado: {OUTPUT_FILE} ({len(result):,} registros)")


if __name__ == '__main__':
    main()
