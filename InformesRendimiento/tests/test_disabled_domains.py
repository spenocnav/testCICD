"""ETL_DISABLED_DOMAINS apaga un dominio de punta a punta.

Antes, apagar un dominio solo lo sacaba de la extracción: su transform seguía en
run_semantic_all y su fact seguía pasando por el loader. Como varios dominios
comparten silver (extract_habitos escribe fact_log_records, que es la entrada de
transform_ubicaciones), el transform regeneraba el semántico igual y el `Indice`
global de ubicaciones se recorría, cambiando el hash de filas intactas y
disparando un re-upsert de la tabla completa con delta cero.

Estos tests recargan los módulos bajo distintos valores del env para verificar
que extract, transform y load quedan alineados por una sola fuente (config).
"""

from __future__ import annotations

import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _reload(monkeypatch, value):
    """Recarga config + worker + run_semantic_all con ETL_DISABLED_DOMAINS=value."""
    monkeypatch.setenv("ETL_DISABLED_DOMAINS", value)
    monkeypatch.setenv("ETL_STREAMING_DATASETS", "factor_carga")
    import config

    config = importlib.reload(config)
    import run_semantic_all

    run_semantic_all = importlib.reload(run_semantic_all)
    return config, run_semantic_all


@pytest.fixture(autouse=True)
def _restore():
    """Deja los módulos como estaban para no contaminar el resto de la suite."""
    yield
    for name in ("config", "run_semantic_all"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


def test_sin_variable_no_apaga_nada(monkeypatch):
    config, run_semantic_all = _reload(monkeypatch, "")

    assert config.DISABLED_DOMAINS == set()
    assert config.DISABLED_EXTRACT_STEPS == ()
    assert config.DISABLED_TABLES == frozenset()
    assert "transform.transform_ubicaciones" in run_semantic_all.MODULES


def test_dominio_apagado_sale_de_transform_y_load(monkeypatch):
    config, run_semantic_all = _reload(monkeypatch, "ubicaciones")

    assert config.DISABLED_TABLES == {"fact_location_point"}
    assert "extract/extract_ubicaciones.py" in config.DISABLED_EXTRACT_STEPS
    assert "transform.transform_ubicaciones" not in run_semantic_all.MODULES
    # Los demás dominios no se ven afectados.
    assert "transform.transform_combustible" in run_semantic_all.MODULES
    assert "transform.transform_altimetria" in run_semantic_all.MODULES


def test_los_tres_pasos_usan_la_misma_fuente(monkeypatch):
    """extract, transform y load no pueden divergir: worker deriva su lista de
    config en vez de tener una tupla hardcodeada."""
    config, run_semantic_all = _reload(monkeypatch, "ubicaciones,altimetria,consumo_def")
    import worker

    worker = importlib.reload(worker)

    for domain in config.DISABLED_DOMAINS:
        pipeline = config.DOMAIN_PIPELINE[domain]
        assert pipeline["extract"] not in worker.EXTRACT_STEPS
        assert pipeline["transform"] not in run_semantic_all.MODULES
        assert pipeline["table"] in config.DISABLED_TABLES

    # Lo que sigue activo no se tocó.
    assert "extract/extract_combustible.py" in worker.EXTRACT_STEPS
    assert "extract/extract_dimensions.py" in worker.EXTRACT_STEPS


def test_dominio_desconocido_falla_cerrado(monkeypatch):
    """Un typo en la variable debe romper el arranque, no apagar silenciosamente
    de más (o de menos)."""
    monkeypatch.setenv("ETL_DISABLED_DOMAINS", "ubicacione")
    import config

    with pytest.raises(ValueError, match="no soportados"):
        importlib.reload(config)


def test_el_loader_omite_las_tablas_apagadas(monkeypatch):
    config, _ = _reload(monkeypatch, "ubicaciones")
    from load import schema

    cargadas = [
        spec.pg_table
        for spec in schema.TABLES
        if spec.pg_table not in config.DISABLED_TABLES
    ]
    assert "fact_location_point" not in cargadas
    assert "fact_combustible_daily" in cargadas
