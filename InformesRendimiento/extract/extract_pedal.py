# ==============================================================================
# extract_pedal.py — Extrae lecturas de posición de pedal del acelerador
#
# Diagnostic ID: a8E-MlXuWg0-3oH2dO424Qw  (INMUTABLE)
# Chunk: 3 días
# Multi-call: batch de todos los devices de una partición en 1 llamada
# event_id: NULL (lecturas independientes)
#
# Estado por vehículo (Fase 3): el rango por vehículo lo resuelve _runner desde
# vehicle_extraction_state (Postgres), con fallback al watermark global JSON.
# ==============================================================================
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # para importar _runner

import logging
import pandas as pd
from utils import safe_multi_call, COLOMBIA_OFFSET, make_row_id, make_vehicle_id
from config import DIAGNOSTIC_IDS, CHUNK_SIZE_DAYS, FACTS_PATH
from _runner import run_status_extraction

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

SCRIPT_ID     = 'posicion_pedal'
DIAGNOSTIC_ID = DIAGNOSTIC_IDS['pedal']   # INMUTABLE
FACT_FILE     = os.path.join(FACTS_PATH, 'fact_status_readings.parquet')
DEDUP_SUBSET  = ['row_id']


def extract_chunk(api, devices: list, from_str: str, to_str: str, db_name: str) -> pd.DataFrame:
    requests_list = [
        ['Get', {
            'typeName': 'StatusData',
            'search': {
                'deviceSearch':    {'id': dev},
                'diagnosticSearch': {'id': DIAGNOSTIC_ID},
                'fromDate': from_str,
                'toDate':   to_str,
            }
        }]
        for dev in devices
    ]
    results = safe_multi_call(api, requests_list)

    rows = []
    for dev, result in zip(devices, results):
        if not result:
            continue
        vid = make_vehicle_id(db_name, dev)
        for r in result:
            dt = r.get('dateTime')
            rows.append({
                'database_name':  db_name,
                'vehicle_id':     vid,
                'device_id':      dev,
                'diagnostic_id':  DIAGNOSTIC_ID,
                'dateTime':       dt,
                'data':           r.get('data'),
                'event_id':       None,
                'fecha_colombia': (dt - COLOMBIA_OFFSET).date() if dt else None,
                'row_id':         make_row_id(db_name, dev, DIAGNOSTIC_ID, dt, None),
            })
    return pd.DataFrame(rows)


def main():
    run_status_extraction(
        script_id=SCRIPT_ID,
        extract_chunk=extract_chunk,
        fact_file=FACT_FILE,
        dedup_subset=DEDUP_SUBSET,
        chunk_days=CHUNK_SIZE_DAYS['posicion_pedal'],
        default_start='2024-09-01',
    )


if __name__ == '__main__':
    main()
