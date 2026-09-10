FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRACKING_RUNTIME_DIR=/runtime

WORKDIR /app

COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock \
    && groupadd --system --gid 10001 tracker \
    && useradd --system --uid 10001 --gid tracker --home-dir /app tracker \
    && mkdir -p /runtime \
    && chown tracker:tracker /runtime

COPY __init__.py build_label_history.py cloudfleet_api.py historical_seed.py ./
COPY label_config.py operational_logging.py production_io.py ./
COPY sync_order_catalog.py tracking_label_worker.py ./

USER tracker
VOLUME ["/runtime"]

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD ["python", "tracking_label_worker.py", "--healthcheck"]

ENTRYPOINT ["python", "-u", "tracking_label_worker.py"]
