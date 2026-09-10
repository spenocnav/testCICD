# ==============================================================================
# extract_ubicaciones.py — Extrae registros GPS (LogRecord) a fact_log_records
#
# Chunk: 3 días
# Multi-call: batch de todos los devices de una partición en 1 llamada
# event_id: NULL (lecturas independientes, no vinculadas a ExceptionEvent)
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
from utils import safe_multi_call, COLOMBIA_OFFSET, make_log_row_id, make_vehicle_id
from config import CHUNK_SIZE_DAYS, FACTS_PATH
from _runner import run_status_extraction

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

SCRIPT_ID    = 'ubicaciones'
FACT_FILE    = os.path.join(FACTS_PATH, 'fact_log_records.parquet')
DEDUP_SUBSET = ['log_row_id']


def extract_chunk(api, devices: list, from_str: str, to_str: str, db_name: str) -> pd.DataFrame:
    """
    1 multi-call con 1 request de LogRecord por device.
    Devuelve DataFrame con columnas de fact_log_records (event_id = NULL).
    """
    requests_list = [
        ['Get', {
            'typeName': 'LogRecord',
            'search': {
                'deviceSearch': {'id': dev},
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
                'event_id':       None,
                'geotab_id':      r.get('id'),
                'log_row_id':     make_log_row_id(db_name, dev, r.get('id'), None, dt),
                'database_name':  db_name,
                'vehicle_id':     vid,
                'device_id':      dev,
                'dateTime':       dt,
                'speed':          r.get('speed'),
                'longitude':      r.get('longitude'),
                'latitude':       r.get('latitude'),
                'fecha_colombia': (dt - COLOMBIA_OFFSET).date() if dt else None,
            })
    return pd.DataFrame(rows)


def main():
    run_status_extraction(
        script_id=SCRIPT_ID,
        extract_chunk=extract_chunk,
        fact_file=FACT_FILE,
        dedup_subset=DEDUP_SUBSET,
        chunk_days=CHUNK_SIZE_DAYS['ubicaciones'],
        default_start='2024-09-01',
    )


if __name__ == '__main__':
    main()
