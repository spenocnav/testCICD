# ==============================================================================
# extract_fallas.py — Extrae FaultData a fact_fault_data
#
# Chunk: 3 días INMUTABLE (plan §5.3 — rangos más grandes pueden perder registros)
# Multi-call: batch de todos los devices de una partición en 1 llamada por chunk
# Guarda IDs crudos: la resolución de nombres (dim_diagnosticos, dim_controllers,
#   dim_failure_modes) queda para transform_fallas.py
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
from config import CHUNK_SIZE_DAYS, FACTS_PATH
from _runner import run_status_extraction

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

SCRIPT_ID    = 'alertas'
FACT_FILE    = os.path.join(FACTS_PATH, 'fact_fault_data.parquet')
DEDUP_SUBSET = ['row_id']


def _extract_id(value) -> str:
    """Extrae el string ID de un campo que puede ser dict{'id': ...} o string."""
    if isinstance(value, dict):
        return value.get('id')
    return value


def fault_record_to_raw(db_name: str, device_id: str, record: dict) -> dict:
    """Mapea un ``FaultData`` al contrato silver canónico.

    Tanto el barrido diario por ``Get`` como la vía de baja latencia por
    ``GetFeed`` deben producir exactamente la misma fila y, en particular, el
    mismo ``row_id``. Mantener el mapeo en un único lugar evita que una
    corrección recibida por el feed termine insertada como otro hecho.
    """
    dt = record.get('dateTime')
    diag = _extract_id(record.get('diagnostic'))
    ctrl = _extract_id(record.get('controller'))
    fmode = _extract_id(record.get('failureMode'))
    return {
        'database_name':       db_name,
        'vehicle_id':           make_vehicle_id(db_name, device_id),
        'device_id':            device_id,
        'dateTime':             dt,
        'diagnostic_id':        diag,
        'diagnostic_sk':        make_row_id(db_name, diag),
        'controller_id':        ctrl,
        'controller_sk':        make_row_id(db_name, ctrl),
        'failure_mode_id':      fmode,
        'failure_mode_sk':      make_row_id(db_name, fmode),
        'fault_state':          record.get('faultState'),
        'count':                record.get('count'),
        'amber_warning_lamp':   record.get('amberWarningLamp'),
        'red_stop_lamp':        record.get('redStopLamp'),
        'malfunction_lamp':     record.get('malfunctionLamp'),
        'protect_warning_lamp': record.get('protectWarningLamp'),
        'fecha_colombia':       (dt - COLOMBIA_OFFSET).date() if dt else None,
        'row_id':               make_row_id(db_name, device_id, dt, diag, fmode, ctrl),
    }


def extract_chunk(api, devices: list, from_str: str, to_str: str, db_name: str) -> pd.DataFrame:
    """
    1 multi-call con 1 request de FaultData por device.
    Devuelve DataFrame con columnas de fact_fault_data (IDs crudos, sin nombres).
    """
    requests_list = [
        ['Get', {
            'typeName': 'FaultData',
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
        for r in result:
            rows.append(fault_record_to_raw(db_name, dev, r))
    return pd.DataFrame(rows)


def main():
    run_status_extraction(
        script_id=SCRIPT_ID,
        extract_chunk=extract_chunk,
        fact_file=FACT_FILE,
        dedup_subset=DEDUP_SUBSET,
        chunk_days=CHUNK_SIZE_DAYS['alertas'],
        default_start='2024-09-01',
        # FaultData puede reenviar una entidad corregida. La clave permanece
        # estable, pero debe ganar su representación más reciente.
        prefer_new=True,
    )


if __name__ == '__main__':
    main()
