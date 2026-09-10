# Worker del ETL de reportes (InformesRendimiento) para el compose del Portal.
#
# Dos targets:
# - development: root y el código montado como volumen (./InformesRendimiento:/etl),
#   igual que la API en desarrollo. La imagen sólo trae Python + dependencias.
# - production: el código va dentro de la imagen, corre sin root y escribe el
#   lago Parquet y su estado local en /var/lib/etl (un volumen), nunca sobre el
#   propio código.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /etl

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt


FROM base AS development

COPY . .

CMD ["python", "worker.py"]


FROM base AS production

RUN groupadd --system etl \
    && useradd --system --gid etl --home-dir /var/lib/etl --create-home etl \
    && mkdir -p /var/lib/etl/data_lake /var/lib/etl/state \
    && chown -R etl:etl /var/lib/etl

COPY --chown=etl:etl . .

ENV ETL_DATA_LAKE_DIR=/var/lib/etl/data_lake \
    ETL_STATE_DIR=/var/lib/etl/state

USER etl

CMD ["python", "worker.py"]
