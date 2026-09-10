import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

import pandas as pd

from config import DIMS_PATH, FACTS_PATH, SEMANTIC_DIMS_PATH, SEMANTIC_FACTS_PATH
from utils import date_to_key

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

FACT_FILE = os.path.join(FACTS_PATH, 'fact_fault_data.parquet')
OUTPUT_FILE = os.path.join(SEMANTIC_FACTS_PATH, 'fact_fault_event.parquet')

COLOMBIA_OFFSET = timedelta(hours=5)

OUTPUT_COLUMNS = [
    'row_id',
    'vehicle_id',
    'database_name',
    'date_key',
    'diagnostic_sk',
    'controller_sk',
    'failure_mode_sk',
    'Fecha',
    'Movil',
    'Fecha de Falla',
    'Codigo Diagnostico',
    'Codigo Modo de Falla',
    'Nombre Fuente Diagnostico',
    'Codigo Controlador',
    'Estado de Falla',
    'Recuento de Fallos',
    'Tipo de Atencion',
    'Luz de Parada Amber',
    'Luz de Parada Roja',
    'Lampara de Averia',
    'Lampara de advertencia',
    'Nombre de Controlador',
    'Diagnostico',
    'Modo de Falla',
]


def _ensure_dim_integrity(fact: pd.DataFrame, sk_col: str, dim_filename: str, id_col: str) -> None:
    """Garantiza que la dim semántica contenga TODOS los sk referenciados por el
    fact (FK). Las fallas sin modo/diagnóstico/controlador generan un sk estable
    (make_row_id(db, None)) que el catálogo no trae; sin esta siembra, el load
    viola la FK. Agrega filas sintéticas '(Sin información)' para los faltantes."""
    path = os.path.join(SEMANTIC_DIMS_PATH, dim_filename)
    if not os.path.exists(path):
        return
    dim = pd.read_parquet(path)
    have = set(dim[sk_col].dropna().unique())
    ref = fact[[sk_col, 'database_name']].dropna(subset=[sk_col]).drop_duplicates()
    missing = ref[~ref[sk_col].isin(have)]
    if missing.empty:
        return
    rows = []
    for _, r in missing.iterrows():
        row = {c: None for c in dim.columns}
        row[sk_col] = r[sk_col]
        if 'database_name' in dim.columns:
            row['database_name'] = r['database_name']
        if 'name' in dim.columns:
            row['name'] = '(Sin información)'
        rows.append(row)
    out = pd.concat([dim, pd.DataFrame(rows)[dim.columns]], ignore_index=True)
    out = out.drop_duplicates(subset=[sk_col])
    out.to_parquet(path, index=False, compression='snappy')
    logging.info(f"dim integrity: +{len(rows)} fila(s) sintética(s) en {dim_filename}")


def transform_fault_rows(
    raw: pd.DataFrame,
    *,
    dim_vehicle: pd.DataFrame,
    dim_diagnostic: pd.DataFrame,
    dim_controller: pd.DataFrame,
    dim_failure_mode: pd.DataFrame,
) -> pd.DataFrame:
    """Transforma ``FaultData`` silver al contrato de ``fact_fault_event``.

    Es la única implementación del mapeo semántico: el pipeline diario y el
    feed de baja latencia la comparten para que severidad, zona horaria, nombres
    y tipos de columna no diverjan.
    """
    if raw.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    df = raw.copy()
    dim_vehicle = dim_vehicle[
        ['vehicle_id', 'database_name', 'placa']
    ].copy()
    dim_diagnostic = dim_diagnostic[
        ['diagnostic_sk', 'name', 'code', 'source_id']
    ].rename(columns={'name': 'diag_name', 'code': 'diag_code', 'source_id': 'diag_source'})
    dim_controller = dim_controller[
        ['controller_sk', 'name', 'code']
    ].rename(columns={'name': 'ctrl_name', 'code': 'ctrl_code'})
    dim_failure_mode = dim_failure_mode[
        ['failure_mode_sk', 'name', 'code']
    ].rename(columns={'name': 'fm_name', 'code': 'fm_code'})

    df = df.merge(dim_vehicle, on=['vehicle_id', 'database_name'], how='left')
    df = df.merge(dim_diagnostic, on='diagnostic_sk', how='left')
    df = df.merge(dim_controller, on='controller_sk', how='left')
    df = df.merge(dim_failure_mode, on='failure_mode_sk', how='left')

    df['Fecha de Falla'] = pd.to_datetime(df['dateTime'])
    df['Fecha'] = (df['Fecha de Falla'] - COLOMBIA_OFFSET).dt.date
    df['date_key'] = df['Fecha'].apply(date_to_key)
    df = df.rename(columns={'placa': 'Movil'})

    df['Tipo de Atencion'] = 'Nivel 3 - Pronta'
    df.loc[df['amber_warning_lamp'] == True, 'Tipo de Atencion'] = 'Nivel 2 - Prioritaria'
    df.loc[df['red_stop_lamp'] == True, 'Tipo de Atencion'] = 'Nivel 1 - Urgente'

    result = pd.DataFrame({
        'row_id': df['row_id'],
        'vehicle_id': df['vehicle_id'],
        'database_name': df['database_name'],
        'date_key': df['date_key'],
        'diagnostic_sk': df['diagnostic_sk'],
        'controller_sk': df['controller_sk'],
        'failure_mode_sk': df['failure_mode_sk'],
        'Fecha': df['Fecha'],
        'Movil': df['Movil'],
        'Fecha de Falla': df['Fecha de Falla'],
        'Codigo Diagnostico': df['diag_code'],
        'Codigo Modo de Falla': df['fm_code'],
        'Nombre Fuente Diagnostico': df['diag_source'].fillna(''),
        'Codigo Controlador': df['ctrl_code'],
        'Estado de Falla': df['fault_state'],
        'Recuento de Fallos': df['count'],
        'Tipo de Atencion': df['Tipo de Atencion'],
        'Luz de Parada Amber': df['amber_warning_lamp'],
        'Luz de Parada Roja': df['red_stop_lamp'],
        'Lampara de Averia': df['malfunction_lamp'],
        'Lampara de advertencia': df['protect_warning_lamp'],
        'Nombre de Controlador': df['ctrl_name'].fillna(''),
        'Diagnostico': df['diag_name'],
        'Modo de Falla': df['fm_name'],
    })
    return result.drop_duplicates(subset=['row_id']).sort_values(
        ['vehicle_id', 'Fecha de Falla']
    )


def main():
    if not os.path.exists(FACT_FILE):
        logging.info("No hay datos de fallas (archivo ausente).")
        return
    df = pd.read_parquet(FACT_FILE)
    if df.empty:
        logging.info("No hay datos de fallas.")
        return

    dim_vehicle = pd.read_parquet(os.path.join(DIMS_PATH, 'dim_vehiculos.parquet'))
    dim_diag = pd.read_parquet(os.path.join(DIMS_PATH, 'dim_diagnosticos.parquet'))
    dim_ctrl = pd.read_parquet(os.path.join(DIMS_PATH, 'dim_controllers.parquet'))
    dim_fm = pd.read_parquet(os.path.join(DIMS_PATH, 'dim_failure_modes.parquet'))

    result = transform_fault_rows(
        df,
        dim_vehicle=dim_vehicle,
        dim_diagnostic=dim_diag,
        dim_controller=dim_ctrl,
        dim_failure_mode=dim_fm,
    )

    # Integridad referencial: sembrar en las dims semánticas los sk que el fact
    # referencia pero la dimensión no trae (p.ej. fallas sin modo de falla).
    _ensure_dim_integrity(result, 'failure_mode_sk', 'dim_failure_mode.parquet', 'failure_mode_id')
    _ensure_dim_integrity(result, 'diagnostic_sk', 'dim_diagnostic.parquet', 'diagnostic_id')
    _ensure_dim_integrity(result, 'controller_sk', 'dim_controller.parquet', 'controller_id')

    os.makedirs(SEMANTIC_FACTS_PATH, exist_ok=True)
    result.to_parquet(OUTPUT_FILE, index=False, compression='snappy')
    logging.info(f"Guardado: {OUTPUT_FILE} ({len(result):,} registros)")


if __name__ == '__main__':
    main()
