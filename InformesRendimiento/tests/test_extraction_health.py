"""`_extraction_health` no puede reportar pares vehículo-dataset como vehículos.

`vehicle_extraction_state` guarda una fila por (vehículo, dataset). Contarla y
llamar al resultado `vehiculos_*` engañaba dos veces: el número eran pares (16
vehículos × 8 datasets = 128) y los dominios apagados a propósito quedaban para
siempre en `pending`, sumando a un contador que se lee como alarma.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import worker


class _FakeResult:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar


class _FakeConn:
    """Responde la consulta de estados o la de conteo según el SQL recibido."""

    def __init__(self, rows, vehiculos):
        self._rows = rows
        self._vehiculos = vehiculos

    def execute(self, statement):
        sql = str(statement)
        if "vehicle_extraction_state" in sql:
            return _FakeResult(rows=self._rows)
        return _FakeResult(scalar=self._vehiculos)


class _FakeEngine:
    def __init__(self, rows, vehiculos):
        self._rows = rows
        self._vehiculos = vehiculos

    @contextmanager
    def connect(self):
        yield _FakeConn(self._rows, self._vehiculos)


def _health(monkeypatch, rows, vehiculos, disabled=frozenset()):
    monkeypatch.setattr(worker.config, "DISABLED_DOMAINS", set(disabled))
    return worker._extraction_health(_FakeEngine(rows, vehiculos))


def test_los_dominios_apagados_no_cuentan_como_pendientes(monkeypatch):
    """El caso real: 16 vehículos, 5 datasets al día y 3 apagados a propósito."""
    rows = [
        ("alertas", "ok", 16),
        ("analisis_combustible", "ok", 16),
        ("factor_carga", "ok", 16),
        ("habito_seguro", "ok", 16),
        ("posicion_pedal", "ok", 16),
        ("altimetria", "pending", 16),
        ("consumo_def", "pending", 16),
        ("ubicaciones", "pending", 16),
    ]
    health = _health(
        monkeypatch, rows, 16, disabled={"altimetria", "consumo_def", "ubicaciones"}
    )

    assert health["vehiculos_activos"] == 16  # vehículos, no pares
    assert health["datasets_ok"] == 80
    assert health["datasets_apagados"] == 48
    # Lo importante: nada pendiente, porque no hay nada pendiente de verdad.
    assert "datasets_pending" not in health


def test_un_pendiente_real_si_se_reporta(monkeypatch):
    """Un dominio ACTIVO sin extraer sí es una señal y tiene que aparecer."""
    rows = [
        ("analisis_combustible", "ok", 14),
        ("analisis_combustible", "pending", 2),
        ("ubicaciones", "pending", 16),
    ]
    health = _health(monkeypatch, rows, 16, disabled={"ubicaciones"})

    assert health["datasets_ok"] == 14
    assert health["datasets_pending"] == 2
    assert health["datasets_apagados"] == 16


def test_los_errores_se_agregan_entre_datasets(monkeypatch):
    rows = [
        ("analisis_combustible", "error", 3),
        ("habito_seguro", "error", 2),
        ("posicion_pedal", "ok", 16),
    ]
    health = _health(monkeypatch, rows, 16)

    assert health["datasets_error"] == 5
    assert health["datasets_ok"] == 16


def test_sin_dominios_apagados_el_contador_queda_en_cero(monkeypatch):
    health = _health(monkeypatch, [("alertas", "ok", 16)], 16)
    assert health["datasets_apagados"] == 0


def test_un_fallo_de_base_no_tumba_la_corrida(monkeypatch):
    """La auditoría es best-effort: si no se puede medir, se devuelve vacío en
    vez de romper el pipeline que ya terminó bien."""

    class _Broken:
        @contextmanager
        def connect(self):
            raise RuntimeError("sin conexión")
            yield  # pragma: no cover

    monkeypatch.setattr(worker.config, "DISABLED_DOMAINS", set())
    assert worker._extraction_health(_Broken()) == {}
