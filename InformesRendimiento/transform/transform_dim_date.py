import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

import pandas as pd

from config import FACTS_PATH, SEMANTIC_DIMS_PATH
from utils import date_to_key, month_to_key

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

OUTPUT_FILE = os.path.join(SEMANTIC_DIMS_PATH, 'dim_date.parquet')
FACT_FILES = [
    os.path.join(FACTS_PATH, 'fact_status_readings.parquet'),
    os.path.join(FACTS_PATH, 'fact_log_records.parquet'),
    os.path.join(FACTS_PATH, 'fact_exception_events.parquet'),
    os.path.join(FACTS_PATH, 'fact_fault_data.parquet'),
    os.path.join(FACTS_PATH, 'fact_trips.parquet'),
]


def _infer_range() -> tuple[date, date]:
    dates = []
    for path in FACT_FILES:
        if not os.path.exists(path):
            continue
        try:
            df = pd.read_parquet(path, columns=['fecha_colombia'])
        except Exception:
            continue
        if df.empty or 'fecha_colombia' not in df.columns:
            continue
        serie = pd.to_datetime(df['fecha_colombia'], errors='coerce').dt.date.dropna()
        if not serie.empty:
            dates.append(serie.min())
            dates.append(serie.max())

    if not dates:
        today = date.today()
        return date(today.year - 2, 1, 1), today + timedelta(days=365)

    # Los hechos mensuales referencian el primer día del mes
    # (`month_start_date_key`), que puede ser anterior a la primera lectura:
    # sin ese piso la FK contra dim_date rechaza la carga del mes inicial.
    return min(dates).replace(day=1), max(dates) + timedelta(days=365)


def main():
    start_date, end_date = _infer_range()
    rows = []
    current = start_date
    while current <= end_date:
        month_start = current.replace(day=1)
        rows.append({
            'date_key': date_to_key(current),
            'date': current,
            'year': current.year,
            'month': current.month,
            'day': current.day,
            'quarter': ((current.month - 1) // 3) + 1,
            'month_key': month_to_key(current),
            'month_start_date': month_start,
            'week_of_year': current.isocalendar().week,
            'day_of_week': current.isoweekday(),
            'is_weekend': current.isoweekday() >= 6,
        })
        current += timedelta(days=1)

    os.makedirs(SEMANTIC_DIMS_PATH, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_parquet(OUTPUT_FILE, index=False, compression='snappy')
    logging.info(f"Guardado: {OUTPUT_FILE} ({len(df):,} registros)")


if __name__ == '__main__':
    main()
