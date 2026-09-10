"""`fact_habito_event` debe publicar los números, no solo el string.

`transform_habitos` ya calculaba velocidad, RPM, factor de carga y fuerza G para
cada evento y los descartaba formateándolos en `Observacion Corta`. El portal los
recuperaba con un regex en SQL en cada consulta (7-17 s sobre 30 días). Estas
pruebas fijan las columnas numéricas nuevas y, sobre todo, dos invariantes que
son fáciles de romper:

1. el pico del acelerómetro se elige por MAGNITUD conservando el signo; `max()`
   sobre una frenada devuelve la lectura más suave, no el pico;
2. `Observacion Corta` NO cambia. El string ya está publicado en 381.786 filas y
   en la UI, incluido su `or 'N/A'`, que convierte un 0 legítimo en `'N/A'`.
   Es un comportamiento raro, pero es el vigente y aquí se fija como invariante,
   no se corrige.
"""

from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from transform.transform_habitos import (  # noqa: E402
    ACCEL_FWD_ID,
    ACCEL_SIDE_ID,
    ACCEL_UP_ID,
    PEAK_COLUMN_BY_AXIS,
    _accel_peaks_by_axis,
    _build_observacion,
    axis_for_event_type,
    build_event_metrics,
    peak_signed,
    to_g,
)

NO_G = {'longitudinal': None, 'lateral': None, 'vertical': None}


def _g(**axes):
    readings = dict(NO_G)
    readings.update(axes)
    return readings


# --------------------------------------------------------------------------
# 1. Cada tipo de evento define su propia métrica
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    'event_type, vel, rpm, load, g_by_axis, expected_value, expected_unit, expected_axis',
    [
        # Excesos de RPM: 296.418 filas del corpus.
        ('Excesos de RPM', 62.0, 2450.0, 78.0, NO_G, 2450.0, 'RPM', None),
        # Excesos de velocidad: 79.371 filas.
        ('Excesos de velocidad', 91.5, None, None, NO_G, 91.5, 'km/h', None),
        # Giros bruscos: 3.009 filas con G + 26 sin lectura.
        ('Giros bruscos', 48.0, None, None, _g(lateral=0.39), 0.39, 'G', 'lateral'),
        # Baches o Resaltos fuertes: 2.199 filas.
        ('Baches o Resaltos fuertes', 33.0, None, None, _g(vertical=0.55), 0.55, 'G', 'vertical'),
        # Aceleraciones bruscas: 425 filas.
        ('Aceleraciones bruscas', None, None, None, _g(longitudinal=0.31), 0.31, 'G', 'longitudinal'),
        # Frenadas bruscas: 338 filas, con el pico NEGATIVO.
        ('Frenadas bruscas', 74.0, None, None, _g(longitudinal=-0.44), -0.44, 'G', 'longitudinal'),
    ],
)
def test_event_value_por_tipo(
    event_type, vel, rpm, load, g_by_axis, expected_value, expected_unit, expected_axis
):
    metrics = build_event_metrics(event_type, vel, rpm, load, g_by_axis)

    assert metrics['event_value'] == expected_value
    assert metrics['event_value_unit'] == expected_unit
    assert metrics['g_axis'] == expected_axis
    assert metrics['velocidad_kmh'] == vel
    assert metrics['rpm'] == rpm
    assert metrics['carga_pct'] == load


def test_alias_singulares_de_config_tambien_mapean():
    """El fallback hardcodeado de `config` usa singular; master data, plural."""
    assert axis_for_event_type('Frenada Brusca') == 'longitudinal'
    assert axis_for_event_type('Aceleracion Brusca') == 'longitudinal'
    assert axis_for_event_type('Aceleración Brusca') == 'longitudinal'
    assert axis_for_event_type('Giro Brusco') == 'lateral'
    assert axis_for_event_type('Bache Fuerte') == 'vertical'


def test_event_type_rpm_sintetico_cae_en_rpm():
    """`_event_type_from_rule` inventa `RPM_xxxxxx` para reglas sin nombre."""
    metrics = build_event_metrics('RPM_abc123', 55.0, 2100.0, 40.0, NO_G)

    assert metrics['event_value'] == 2100.0
    assert metrics['event_value_unit'] == 'RPM'


# --------------------------------------------------------------------------
# 2. Ausencia de lectura → NULL, nunca 0
# --------------------------------------------------------------------------

def test_lectura_ausente_es_none_no_cero():
    """`N/A` en la observación viene de un hueco real; NULL != 0."""
    metrics = build_event_metrics('Aceleraciones bruscas', None, None, None, NO_G)

    assert metrics['velocidad_kmh'] is None
    assert metrics['rpm'] is None
    assert metrics['carga_pct'] is None
    assert metrics['g_force'] is None
    # Sin lectura en el eje del evento no hay eje que reportar…
    assert metrics['g_axis'] is None
    # …pero la unidad la define el TIPO, que sí se conoce.
    assert metrics['event_value'] is None
    assert metrics['event_value_unit'] == 'G'


def test_giro_brusco_sin_lectura_de_g():
    """26 filas del corpus son giros bruscos sin la parte de G Force."""
    metrics = build_event_metrics('Giros bruscos', 41.0, None, None, NO_G)

    assert metrics['g_force'] is None
    assert metrics['g_axis'] is None
    assert metrics['event_value'] is None


def test_cero_es_cero_no_none():
    """Un 0 medido debe sobrevivir como 0 en las columnas numéricas."""
    metrics = build_event_metrics('Excesos de velocidad', 0.0, None, None, NO_G)

    assert metrics['velocidad_kmh'] == 0.0
    assert metrics['event_value'] == 0.0


def test_to_g_propaga_nulos():
    assert to_g(None) is None
    assert to_g(float('nan')) is None
    assert to_g(-4.4) == -0.44


def test_peak_signed_sin_lecturas():
    assert peak_signed([]) is None
    assert peak_signed([None, float('nan')]) is None


# --------------------------------------------------------------------------
# 3. Regresión del defecto: max() subestima las frenadas
# --------------------------------------------------------------------------

def test_frenada_toma_el_pico_mas_negativo():
    """`max([-0.44, -0.10, -0.31])` da -0.10: la frenada MÁS SUAVE."""
    readings = [-0.44, -0.10, -0.31]

    assert peak_signed(readings) == -0.44
    assert max(readings) == -0.10  # el defecto que se corrige


def test_frenada_extremo_a_extremo_desde_lecturas_crudas():
    df_accel = pd.DataFrame([
        {'event_sk': 'E1', 'diagnostic_id': ACCEL_FWD_ID, 'data': -4.4},
        {'event_sk': 'E1', 'diagnostic_id': ACCEL_FWD_ID, 'data': -1.0},
        {'event_sk': 'E1', 'diagnostic_id': ACCEL_FWD_ID, 'data': -3.1},
    ])

    peaks = _merge_peaks(df_accel)
    g_by_axis = {axis: to_g(peaks['E1'].get(column))
                 for axis, column in PEAK_COLUMN_BY_AXIS.items()}
    metrics = build_event_metrics('Frenadas bruscas', 74.0, None, None, g_by_axis)

    assert metrics['g_force'] == -0.44
    assert metrics['event_value'] == -0.44
    assert metrics['event_value_unit'] == 'G'
    assert metrics['g_axis'] == 'longitudinal'


def test_empate_de_magnitud_es_determinista():
    """+4,0 y -4,0 tienen la misma magnitud: gana el mayor, no el orden."""
    assert peak_signed([-4.0, 4.0]) == 4.0
    assert peak_signed([4.0, -4.0]) == 4.0


# --------------------------------------------------------------------------
# 4. Varios ejes en el mismo evento: cada uno a su columna
# --------------------------------------------------------------------------

def _merge_peaks(df_accel: pd.DataFrame) -> dict:
    """Colapsa los agregados por eje en {event_sk: {columna: valor}}."""
    merged: dict = {}
    for agg in _accel_peaks_by_axis(df_accel):
        if agg.empty:
            continue
        column = [c for c in agg.columns if c != 'event_sk'][0]
        for _, row in agg.iterrows():
            merged.setdefault(row['event_sk'], {})[column] = row[column]
    return merged


def test_cada_eje_va_a_su_columna():
    df_accel = pd.DataFrame([
        {'event_sk': 'E1', 'diagnostic_id': ACCEL_FWD_ID, 'data': -1.2},
        {'event_sk': 'E1', 'diagnostic_id': ACCEL_SIDE_ID, 'data': 3.9},
        {'event_sk': 'E1', 'diagnostic_id': ACCEL_UP_ID, 'data': -8.0},
    ])

    peaks = _merge_peaks(df_accel)['E1']

    assert peaks[PEAK_COLUMN_BY_AXIS['longitudinal']] == -1.2
    assert peaks[PEAK_COLUMN_BY_AXIS['lateral']] == 3.9
    assert peaks[PEAK_COLUMN_BY_AXIS['vertical']] == -8.0


def test_elige_el_eje_del_tipo_no_el_de_mayor_magnitud():
    """Un giro brusco reporta el eje LATERAL aunque el vertical sea mayor."""
    df_accel = pd.DataFrame([
        {'event_sk': 'E1', 'diagnostic_id': ACCEL_FWD_ID, 'data': -1.2},
        {'event_sk': 'E1', 'diagnostic_id': ACCEL_SIDE_ID, 'data': 3.9},
        {'event_sk': 'E1', 'diagnostic_id': ACCEL_UP_ID, 'data': -8.0},
    ])
    peaks = _merge_peaks(df_accel)['E1']
    g_by_axis = {axis: to_g(peaks.get(column))
                 for axis, column in PEAK_COLUMN_BY_AXIS.items()}

    metrics = build_event_metrics('Giros bruscos', 48.0, None, None, g_by_axis)

    assert metrics['g_axis'] == 'lateral'
    assert metrics['g_force'] == 0.39
    assert metrics['event_value'] == 0.39


def test_agregado_por_eje_ignora_eventos_de_otro_eje():
    df_accel = pd.DataFrame([
        {'event_sk': 'E1', 'diagnostic_id': ACCEL_FWD_ID, 'data': -4.4},
        {'event_sk': 'E2', 'diagnostic_id': ACCEL_SIDE_ID, 'data': 2.0},
    ])

    peaks = _merge_peaks(df_accel)

    assert PEAK_COLUMN_BY_AXIS['lateral'] not in peaks['E1']
    assert PEAK_COLUMN_BY_AXIS['longitudinal'] not in peaks['E2']


def test_accel_vacio_no_revienta():
    empty = pd.DataFrame(columns=['event_sk', 'diagnostic_id', 'data'])

    aggs = _accel_peaks_by_axis(empty)

    assert len(aggs) == 3
    assert all(agg.empty for agg in aggs)


def test_lecturas_no_numericas_se_descartan():
    df_accel = pd.DataFrame([
        {'event_sk': 'E1', 'diagnostic_id': ACCEL_FWD_ID, 'data': None},
    ])

    assert _merge_peaks(df_accel) == {}


# --------------------------------------------------------------------------
# 5. Tipo desconocido: no se inventa una métrica
# --------------------------------------------------------------------------

def test_tipo_desconocido_deja_metrica_en_null():
    metrics = build_event_metrics(
        'Zona restringida', 60.0, None, None, _g(lateral=0.9)
    )

    assert metrics['event_value'] is None
    assert metrics['event_value_unit'] is None
    assert metrics['g_axis'] is None
    assert metrics['g_force'] is None
    # Las lecturas genéricas sí se conservan.
    assert metrics['velocidad_kmh'] == 60.0


# --------------------------------------------------------------------------
# 6. `Observacion Corta` byte a byte igual: los 7 patrones del corpus
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    'event_type, vel, rpm, load, g_force, expected',
    [
        ('Excesos de RPM', 62.0, 2450.0, 78.0, None,
         '2450.0 RPM, 62.0 km/h, Carga: 78.0%'),
        ('Excesos de velocidad', 91.5, None, None, None,
         '91.5 km/h'),
        ('Giros bruscos', 48.0, None, None, 0.39,
         '48.0 km/h, Aceleración de lado a lado: 0.39 G Force'),
        ('Giros bruscos', 48.0, None, None, None,
         '48.0 km/h'),
        ('Baches o Resaltos fuertes', 33.0, None, None, 0.55,
         '33.0 km/h, Aceleración vertical: 0.55 G Force'),
        ('Aceleraciones bruscas', None, None, None, 0.31,
         'N/A km/h, Aceleración hacia delante o frenado: 0.31 G Force'),
        ('Frenadas bruscas', 74.0, None, None, -0.44,
         '74.0 km/h, Aceleración hacia delante o frenado: -0.44 G Force'),
    ],
)
def test_observacion_corta_no_cambia(event_type, vel, rpm, load, g_force, expected):
    assert _build_observacion(event_type, vel, rpm, load, g_force) == expected


def test_observacion_corta_convierte_cero_en_na():
    """INVARIANTE VIGENTE, NO UN ARREGLO PENDIENTE.

    `or 'N/A'` es falsy con 0, así que una velocidad medida de 0 km/h se publica
    como `'N/A'`. El string ya está así en el corpus; corregirlo cambiaría la
    historia visible. Las columnas numéricas nuevas SÍ distinguen 0 de NULL, que
    es justamente para lo que existen.
    """
    assert _build_observacion('Excesos de velocidad', 0.0, None, None, None) == 'N/A km/h'
    assert _build_observacion('Excesos de RPM', 0.0, 0.0, 0.0, None) == (
        'N/A RPM, N/A km/h, Carga: N/A%'
    )

    metrics = build_event_metrics('Excesos de RPM', 0.0, 0.0, 0.0, NO_G)
    assert metrics['velocidad_kmh'] == 0.0
    assert metrics['rpm'] == 0.0
    assert metrics['carga_pct'] == 0.0
