"""Reglas de puntuación de la calificación.

Tres defectos que motivaron estas pruebas:

- Los excesos de RPM contaban como evento de seguridad además de pesar 20 % en
  el QHO. Eran el 99.4 % de los eventos registrados, así que el QHS medía RPM
  otra vez en lugar de conducción segura.
- La cifra mensual de flota sumaba eventos y km de todos los vehículos antes de
  aplicar el tope, así que un vehículo con 44 ev/1000 km mandaba el mes entero a
  cero aunque otro estuviera en 59.6.
- El gauge promediaba los QGen sin ponderar, de modo que la misma pantalla
  mostraba tres cifras distintas para el mismo mes y la misma flota.
"""

from __future__ import annotations

import pytest

from app.services.analytics_service import (
    _promedio_ponderado,
    _qhs_event_weight,
    _score_qho,
    _score_qhs,
    _score_rpm,
    _weighted_rpm_events,
)
from app.services.calificacion_config import DEFAULT_CALIFICACION_CONFIG

# Estas pruebas fijan la CALIBRACIÓN POR DEFECTO. Desde 2026-08-27 los umbrales
# no son constantes de módulo sino campos de `CalificacionConfig`, así que se
# pasan explícitamente: leerlos de `DEFAULT_CALIFICACION_CONFIG` es lo que hace
# que un cambio accidental del default rompa acá y no en producción.
_CFG = DEFAULT_CALIFICACION_CONFIG
_EVENTOS_CAP = _CFG.eventos_cap
_RPM_COMMERCIAL_CAP_PER_1000KM = _CFG.rpm_cap_comercial_1000km
_RPM_VOCATIONAL_CAP_PER_100H = _CFG.rpm_cap_vocacional_100h


@pytest.mark.parametrize(
    ("event_type", "expected"),
    [
        ("Excesos de RPM", 0.0),
        ("Exceso RPM", 0.0),
        ("excesos de rpm", 0.0),
        ("  EXCESOS DE RPM  ", 0.0),
        ("Excesos de velocidad", 1.0),
        ("Frenadas bruscas", 1.0),
        ("Giros bruscos", 1.0),
        ("Aceleraciones bruscas", 0.5),
        ("Baches o Resaltos fuertes", 0.25),
    ],
)
def test_peso_por_tipo_de_evento(event_type: str, expected: float) -> None:
    assert _qhs_event_weight(event_type, config=_CFG) == expected


def test_tipo_desconocido_cuenta_completo() -> None:
    """Una regla nueva no puede colarse sin puntuar por no estar en la tabla."""
    assert _qhs_event_weight("Evento que nadie calibró", config=_CFG) == 1.0
    assert _qhs_event_weight(None, config=_CFG) == 1.0


def test_rpm_no_puntua_en_seguridad() -> None:
    """Caso real: WPK740 en 2026-06 tenía 155 de sus 171 eventos en RPM."""
    peso = (
        _qhs_event_weight("Excesos de RPM", config=_CFG) * 155
        + _qhs_event_weight("Excesos de velocidad", config=_CFG) * 11
        + _qhs_event_weight("Baches o Resaltos fuertes", config=_CFG) * 4
        + _qhs_event_weight("Frenadas bruscas", config=_CFG) * 1
    )
    assert peso == 13.0

    qhs = _score_qhs(peso, km=3856.2, config=_CFG)
    assert qhs is not None
    assert 77.0 < qhs < 78.0  # antes daba 0 por contar los RPM


def test_qho_cumple_con_metas_y_sin_eventos_rpm() -> None:
    assert _score_qho(0.70, 0.10, 0.0, 1000.0, None, False, config=_CFG) == 100.0
    assert _score_qho(0.80, 0.05, 0.0, 1000.0, None, False, config=_CFG) == 100.0


def test_qho_aplica_escalas_de_eficiencia_y_ralenti() -> None:
    # 35% eficiente = 50 puntos del componente; 20% ralentí = 50 puntos.
    # Sin excesos RPM, el tercer componente da 100.
    assert _score_qho(
        0.35, 0.20, 0.0, 1000.0, None, False, config=_CFG
    ) == pytest.approx(60.0)


def test_rpm_se_mide_por_conteo_y_los_altos_cuentan_doble() -> None:
    assert _weighted_rpm_events(total=10, sobre_umbral=0, config=_CFG) == 10.0
    assert _weighted_rpm_events(total=10, sobre_umbral=4, config=_CFG) == 14.0
    assert _score_rpm(0.0, km=1000.0, horas=None, vocacional=False, config=_CFG) == 100.0
    assert (
        _score_rpm(
            _RPM_COMMERCIAL_CAP_PER_1000KM,
            km=1000.0,
            horas=None,
            vocacional=False,
            config=_CFG,
        )
        == 0.0
    )
    assert (
        _score_rpm(
            _RPM_VOCATIONAL_CAP_PER_100H,
            km=0.0,
            horas=100.0,
            vocacional=True,
            config=_CFG,
        )
        == 0.0
    )


def test_rpm_sin_kilometros_no_altera_los_otros_componentes() -> None:
    # Sin exposición comparable se redistribuyen los pesos entre eficiencia y
    # ralentí; no se premia artificialmente la ausencia de kilómetros.
    assert _score_rpm(10.0, km=0.0, horas=0.0, vocacional=False, config=_CFG) is None
    assert _score_rpm(10.0, km=0.0, horas=0.0, vocacional=True, config=_CFG) is None
    assert _score_qho(0.70, 0.10, 10.0, 0.0, 0.0, False, config=_CFG) == 100.0


def test_score_es_lineal_hasta_el_tope_y_luego_cero() -> None:
    assert _score_qhs(0.0, km=1000.0, config=_CFG) == 100.0
    assert _score_qhs(_EVENTOS_CAP / 2, km=1000.0, config=_CFG) == 50.0
    assert _score_qhs(_EVENTOS_CAP, km=1000.0, config=_CFG) == 0.0
    assert _score_qhs(_EVENTOS_CAP * 10, km=1000.0, config=_CFG) == 0.0


def test_sin_kilometros_no_es_evaluable() -> None:
    assert _score_qhs(5.0, km=None, config=_CFG) is None
    assert _score_qhs(5.0, km=0.0, config=_CFG) is None


def test_promedio_pondera_por_kilometros() -> None:
    """Un vehículo de 200 km no puede pesar lo mismo que uno de 4000."""
    resultado = _promedio_ponderado([(100.0, 4000.0), (0.0, 200.0)])
    assert resultado == pytest.approx(95.238, abs=0.001)

    simple = (100.0 + 0.0) / 2
    assert resultado != simple


def test_promedio_ignora_valores_nulos() -> None:
    assert _promedio_ponderado([(80.0, 100.0), (None, 900.0)]) == 80.0


def test_sin_pesos_cae_a_promedio_simple() -> None:
    """Vehículos que puntuaron pero no registraron distancia no se descartan."""
    assert _promedio_ponderado([(60.0, 0.0), (80.0, 0.0)]) == 70.0


def test_lista_vacia_no_es_evaluable() -> None:
    assert _promedio_ponderado([]) is None
    assert _promedio_ponderado([(None, 10.0)]) is None


def test_un_vehiculo_malo_no_manda_la_flota_a_cero() -> None:
    """El caso que disparó todo: 0 y 59.6 en la tabla, 0 en la gráfica."""
    peor = _score_qhs(44.3 * 3.8562, km=3856.2, config=_CFG)
    mejor = _score_qhs(8.1 * 2.7225, km=2722.5, config=_CFG)
    assert peor == 0.0
    assert mejor is not None and mejor > 0

    flota = _promedio_ponderado([(peor, 3856.2), (mejor, 2722.5)])
    assert flota is not None and flota > 0
