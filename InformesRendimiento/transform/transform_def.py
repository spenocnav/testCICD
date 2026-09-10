import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
import warnings

import pandas as pd

from config import (
    DIAGNOSTIC_IDS,
    DIMS_PATH,
    FACTS_PATH,
    SEMANTIC_FACTS_PATH,
    VOLUMEN_POR_DEFECTO,
    VOLUMEN_TANQUES,
)
from utils import date_to_key, make_row_id, read_facts

warnings.filterwarnings('ignore')
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DIAGNOSTIC_ID = DIAGNOSTIC_IDS['def']
FACT_FILE = os.path.join(FACTS_PATH, 'fact_status_readings.parquet')
DIM_FILE = os.path.join(DIMS_PATH, 'dim_vehiculos.parquet')
OUTPUT_FILE = os.path.join(SEMANTIC_FACTS_PATH, 'fact_def_daily.parquet')


def calcular_serie1_mejorada(valores):
    if len(valores) == 0:
        return []
    nivel_ajustado = []
    nivel_actual = valores[0]
    acumulado_incrementos = 0

    for value in valores:
        if pd.isna(value):
            nivel_ajustado.append(nivel_actual)
            continue
        if value >= 99.4:
            nivel_actual = 100.0
        elif value < nivel_actual:
            nivel_actual = value
            acumulado_incrementos = 0
        elif (value - nivel_actual) >= 15:
            nivel_actual = value
        elif (value - nivel_actual) >= 5:
            acumulado_incrementos += (value - nivel_actual)
            if acumulado_incrementos >= 15:
                nivel_actual = value
                acumulado_incrementos = 0
        nivel_ajustado.append(nivel_actual)
    return nivel_ajustado


def calcular_ponderada(row):
    return (
        0.1 * row['Valor'] +
        0.4 * row['Serie1'] +
        0.3 * row['SerieEMA'] +
        0.2 * row['SerieUmbralDinamico']
    )


def _to_bogota_datetime(series: pd.Series) -> pd.Series:
    timestamps = pd.to_datetime(series, errors='coerce')
    if timestamps.dt.tz is None:
        return timestamps.dt.tz_localize('UTC').dt.tz_convert('America/Bogota')
    return timestamps.dt.tz_convert('America/Bogota')


def process_device(df_device: pd.DataFrame, vehicle_id: str, database_name: str, placa: str) -> pd.DataFrame:
    df = df_device.copy()
    df = df.rename(columns={'data': 'Valor', 'dateTime': 'Fecha'})
    df['Valor'] = df['Valor'].astype(float)
    df['Fecha'] = _to_bogota_datetime(df['Fecha'])
    df = df.sort_values('Fecha')

    df['Serie1'] = calcular_serie1_mejorada(df['Valor'].values)
    df['SerieEMA'] = df['Serie1'].rolling(window=12, min_periods=1).mean()
    desviacion = df['Serie1'].rolling(window=5, min_periods=1).std().fillna(0)
    df['SerieUmbralDinamico'] = df['Serie1'] - desviacion
    df['SeriePonderada'] = df.apply(calcular_ponderada, axis=1)

    df['Dia'] = df['Fecha'].dt.date
    df['Consumo_Diff'] = (df['SeriePonderada'].shift(1) - df['SeriePonderada']).clip(lower=0).fillna(0)

    resumen = df.groupby('Dia')['Consumo_Diff'].sum().reset_index()
    resumen.rename(columns={'Consumo_Diff': 'consumoPct', 'Dia': 'fecha'}, inplace=True)

    volumen = VOLUMEN_TANQUES.get(placa, VOLUMEN_POR_DEFECTO)
    resumen['consumoDEF'] = (resumen['consumoPct'] / 100) * volumen
    resumen['vehicle_id'] = vehicle_id
    resumen['database_name'] = database_name
    resumen['placa'] = placa
    resumen['date_key'] = resumen['fecha'].apply(date_to_key)
    resumen['fact_row_id'] = resumen['fecha'].apply(
        lambda value: make_row_id(vehicle_id, value, 'def_daily')
    )
    return resumen


def main():
    df = read_facts(FACT_FILE)
    df = df[(df['diagnostic_id'] == DIAGNOSTIC_ID) & (df['event_id'].isna())].copy()
    if df.empty:
        logging.info("No hay datos DEF.")
        return

    dim = pd.read_parquet(DIM_FILE)[['vehicle_id', 'placa']]
    df = df.merge(dim, on='vehicle_id', how='left')

    all_results = []
    for (vehicle_id, database_name, placa), df_vehicle in df.groupby(['vehicle_id', 'database_name', 'placa']):
        try:
            resumen = process_device(df_vehicle[['data', 'dateTime']].copy(), vehicle_id, database_name, placa)
            all_results.append(resumen)
            logging.info(f"  Procesado: {placa}")
        except Exception as exc:
            logging.error(f"  Error procesando {placa}: {exc}")

    if not all_results:
        logging.info("No se generaron datos DEF.")
        return

    result = pd.concat(all_results, ignore_index=True)
    result = result[[
        'fact_row_id',
        'vehicle_id',
        'database_name',
        'date_key',
        'placa',
        'fecha',
        'consumoPct',
        'consumoDEF',
    ]].drop_duplicates(subset=['fact_row_id']).sort_values(['vehicle_id', 'fecha'])

    os.makedirs(SEMANTIC_FACTS_PATH, exist_ok=True)
    result.to_parquet(OUTPUT_FILE, index=False, compression='snappy')
    logging.info(f"Guardado: {OUTPUT_FILE} ({len(result):,} registros)")


if __name__ == '__main__':
    main()
