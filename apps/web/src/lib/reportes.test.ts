import { describe, expect, it } from 'vitest';

import {
  getDailyDateBounds,
  getDefaultDailyDateRange,
  limitDailyDateRange,
  normalizeCalificacionConfig,
  normalizeRankingMetric,
  promedioQgenVisible,
  RALENTI_DURATION_BUCKETS,
  RALENTI_RPM_BUCKETS,
  ralentiBucketRange,
  ralentiDominantBucketLabel,
  ralentiEventBucket,
  ralentiEventSummary,
  ralentiHorasLabel,
  ralentiPctScale,
  ralentiTabAvailable,
  rankingMetricOptions,
  rendimientoRankingMetric,
} from './reportes';
import type { CalificacionCalibracion } from './types';

describe('getDailyDateBounds', () => {
  it('permite seleccionar las últimas 30 fechas incluyendo hoy', () => {
    expect(getDailyDateBounds('2026-07-23')).toEqual({
      minDate: '2026-06-24',
      maxDate: '2026-07-23',
    });
  });

  it('mantiene exactamente 30 fechas al atravesar meses o años', () => {
    expect(getDailyDateBounds('2025-03-01').minDate).toBe('2025-01-31');
    expect(getDailyDateBounds('2026-01-10').minDate).toBe('2025-12-12');
  });
});

describe('getDefaultDailyDateRange', () => {
  it('carga automáticamente los últimos siete días al seleccionar Diaria', () => {
    expect(getDefaultDailyDateRange('2026-07-23')).toEqual({
      dateFrom: '2026-07-17',
      dateTo: '2026-07-23',
    });
  });
});

describe('limitDailyDateRange', () => {
  const today = '2026-07-23';

  it('permite ampliar manualmente el rango hasta las 30 fechas disponibles', () => {
    expect(limitDailyDateRange('2026-06-24', '2026-07-23', today)).toEqual({
      dateFrom: '2026-06-24',
      dateTo: '2026-07-23',
    });
  });

  it('conserva rangos manuales mayores a siete días', () => {
    expect(limitDailyDateRange('2026-07-01', '2026-07-20', today)).toEqual({
      dateFrom: '2026-07-01',
      dateTo: '2026-07-20',
    });
  });

  it('conserva ambos extremos cuando están dentro de los últimos 30 días', () => {
    expect(limitDailyDateRange('2026-07-01', '2026-07-20', today, 'from')).toEqual({
      dateFrom: '2026-07-01',
      dateTo: '2026-07-20',
    });
  });

  it('permite llegar hasta hoy al seleccionar la fecha más antigua', () => {
    expect(limitDailyDateRange('2026-06-24', '2026-07-23', today, 'from')).toEqual({
      dateFrom: '2026-06-24',
      dateTo: '2026-07-23',
    });
  });

  it('recorta cualquier fecha fuera de los últimos 30 días o posterior a hoy', () => {
    expect(limitDailyDateRange('2025-01-01', '2027-01-01', today)).toEqual({
      dateFrom: '2026-06-24',
      dateTo: '2026-07-23',
    });
  });
});

describe('normalizeCalificacionConfig', () => {
  const calibracion: CalificacionCalibracion = {
    peso_qhs: 0.5,
    peso_qho: 0.5,
    peso_eficiente: 0.5,
    peso_ralenti: 0.3,
    peso_exceso_rpm: 0.2,
    efic_target: 0.7,
    ralenti_target: 0.1,
    ralenti_max: 0.3,
    eventos_cap: 15,
    rpm_cap_comercial_1000km: 150,
    rpm_cap_vocacional_100h: 1400,
    rpm_high_weight: 2,
    umbral_en_riesgo: 70,
    umbral_cumple: 85,
    qhs_event_weights: { 'frenadas bruscas': 1 },
    qhs_default_weight: 1,
  };

  it('conserva el mapa de penalizaciones y el catálogo que publica el backend', () => {
    const normalizada = normalizeCalificacionConfig({
      origen: 'flota',
      fleet_id: 'f1',
      actualizado_en: null,
      actualizado_por: null,
      config: { ...calibracion, penalizaciones: { sobrevelocidad_rpm: false } },
      defaults: calibracion,
      penalizaciones_disponibles: [
        { code: 'sobrevelocidad_rpm', nombre: 'Sobrevelocidad de RPM', descripcion: 'Anula.' },
      ],
    });

    expect(normalizada?.valores.penalizaciones).toEqual({ sobrevelocidad_rpm: false });
    expect(normalizada?.penalizaciones_disponibles).toHaveLength(1);
  });

  // El contrato se está desplegando: un API anterior no publica el catálogo y
  // la pantalla tiene que abrir igual, sin la sección de penalizaciones.
  it('deja el catálogo vacío cuando el API todavía no lo publica', () => {
    const normalizada = normalizeCalificacionConfig({
      origen: 'defecto',
      fleet_id: 'f1',
      actualizado_en: null,
      actualizado_por: null,
      config: calibracion,
    });

    expect(normalizada?.penalizaciones_disponibles).toEqual([]);
    expect(normalizada?.defaults).toEqual(calibracion);
  });
});

describe('promedioQgenVisible', () => {
  // El clic en el donut filtra la tabla y el gauge tiene que mostrar el
  // promedio de lo que quedó. Antes se calculaba como media simple, así que el
  // gauge cambiaba de fórmula al filtrar.
  it('usa los pesos del backend, no una media simple', () => {
    const promedio = promedioQgenVisible([
      { qgen: 100, peso_en_promedio: 0.9 },
      { qgen: 0, peso_en_promedio: 0.1 },
    ]);
    expect(promedio).toBeCloseTo(90, 6);
  });

  it('renormaliza los pesos sobre el subconjunto visible', () => {
    // Los pesos vienen normalizados sobre TODA la flota; al filtrar suman menos
    // de 1 y el promedio tiene que seguir siendo el del subconjunto.
    const promedio = promedioQgenVisible([
      { qgen: 80, peso_en_promedio: 0.2 },
      { qgen: 60, peso_en_promedio: 0.2 },
    ]);
    expect(promedio).toBeCloseTo(70, 6);
  });

  it('con pesos iguales es la media, que es el modo de promedio simple', () => {
    expect(
      promedioQgenVisible([
        { qgen: 90, peso_en_promedio: 0.5 },
        { qgen: 30, peso_en_promedio: 0.5 },
      ]),
    ).toBeCloseTo(60, 6);
  });

  it('ignora los vehículos sin Q General', () => {
    // Un `qgen` nulo es un vehículo sin exposición suficiente o sin datos: no
    // es un 0 y no puede bajar el promedio.
    expect(
      promedioQgenVisible([
        { qgen: 90, peso_en_promedio: 1 },
        { qgen: null, peso_en_promedio: 0 },
      ]),
    ).toBeCloseTo(90, 6);
  });

  it('cae a media simple cuando no hay pesos utilizables', () => {
    // Mismo respaldo que `_promedio_ponderado` en el backend: no descartar
    // vehículos que puntuaron pero no registraron exposición.
    expect(
      promedioQgenVisible([
        { qgen: 80, peso_en_promedio: 0 },
        { qgen: 40, peso_en_promedio: null },
      ]),
    ).toBeCloseTo(60, 6);
  });

  it('devuelve null sin nada que promediar', () => {
    expect(promedioQgenVisible([])).toBeNull();
    expect(promedioQgenVisible([{ qgen: null, peso_en_promedio: 0 }])).toBeNull();
  });
});

describe('rendimientoRankingMetric', () => {
  it('una flota comercial se mide por distancia por unidad de combustible', () => {
    expect(rendimientoRankingMetric(false, false)).toBe('km_gal');
    expect(rendimientoRankingMetric(true, false)).toBe('km_m3');
  });

  it('una flota vocacional se mide por consumo por hora', () => {
    expect(rendimientoRankingMetric(false, true)).toBe('gal_hr');
    expect(rendimientoRankingMetric(true, true)).toBe('m3_hr');
  });
});

describe('normalizeRankingMetric', () => {
  it('traduce cualquier métrica de rendimiento a la de la unidad y operación vigentes', () => {
    expect(normalizeRankingMetric('km_gal', false, true)).toBe('gal_hr');
    expect(normalizeRankingMetric('gal_hr', false, false)).toBe('km_gal');
    expect(normalizeRankingMetric('km_gal', true, false)).toBe('km_m3');
    expect(normalizeRankingMetric('m3_hr', false, true)).toBe('gal_hr');
  });

  it('deja intactas las métricas que no dependen de la unidad', () => {
    expect(normalizeRankingMetric('comb', true, true)).toBe('comb');
    expect(normalizeRankingMetric('pct_ralenti', false, true)).toBe('pct_ralenti');
  });
});

describe('rankingMetricOptions', () => {
  it('ofrece gal/h en vez de km/gal cuando la flota es vocacional', () => {
    const labels = rankingMetricOptions(false, true).map((o) => `${o.value}:${o.label}`);
    expect(labels).toContain('gal_hr:gal/h');
    expect(labels.some((l) => l.startsWith('km_gal'))).toBe(false);
  });

  it('conserva km/gal y km/m³ en flotas comerciales', () => {
    expect(rankingMetricOptions(false, false).map((o) => o.value)).toContain('km_gal');
    expect(rankingMetricOptions(true, false).map((o) => o.label)).toContain('km/m³');
  });

  it('nunca ofrece dos métricas con el mismo valor', () => {
    for (const isGas of [false, true]) {
      for (const vocacional of [false, true]) {
        const values = rankingMetricOptions(isGas, vocacional).map((o) => o.value);
        expect(new Set(values).size).toBe(values.length);
      }
    }
  });
});

describe('RALENTI_DURATION_BUCKETS / RALENTI_RPM_BUCKETS', () => {
  it('conservan el orden del backend y las etiquetas que publica', () => {
    expect(RALENTI_DURATION_BUCKETS.map((b) => b.key)).toEqual(['lt1', '1_5', '5_10', 'gt10']);
    expect(RALENTI_DURATION_BUCKETS.map((b) => b.label)).toEqual([
      '< 1 min',
      '1 – 5 min',
      '5 – 10 min',
      '> 10 min',
    ]);
    expect(RALENTI_RPM_BUCKETS.map((b) => b.key)).toEqual([
      'lt600',
      '600_800',
      '800_1000',
      '1000_1200',
      'gt1200',
      'sin_rpm',
    ]);
    expect(RALENTI_RPM_BUCKETS.at(-1)?.label).toBe('Sin RPM');
  });

  it('nunca repiten clave ni etiqueta', () => {
    const keys = RALENTI_RPM_BUCKETS.map((b) => b.key);
    const labels = RALENTI_RPM_BUCKETS.map((b) => b.label);
    expect(new Set(keys).size).toBe(keys.length);
    expect(new Set(labels).size).toBe(labels.length);
  });
});

describe('ralentiDominantBucketLabel', () => {
  it('traduce la clave del rango dominante a su etiqueta', () => {
    expect(ralentiDominantBucketLabel({ rango_dominante: 'gt10' })).toBe('> 10 min');
    expect(ralentiDominantBucketLabel({ rango_dominante: '1_5' })).toBe('1 – 5 min');
  });

  it('devuelve null sin rango dominante', () => {
    expect(ralentiDominantBucketLabel({ rango_dominante: null })).toBeNull();
  });
});

describe('ralentiPctScale', () => {
  it('detecta fracciones 0..1 y las lleva a porcentaje', () => {
    expect(ralentiPctScale([{ pct_minutos: 0.69 }, { pct_minutos: 0.31 }])).toBe(100);
  });

  it('deja intactos los porcentajes 0..100', () => {
    expect(ralentiPctScale([{ pct_minutos: 69 }, { pct_minutos: 31 }])).toBe(1);
  });

  it('sin eventos la escala es irrelevante y no falla', () => {
    expect(ralentiPctScale([{ pct_minutos: 0 }, { pct_minutos: 0 }])).toBe(100);
    expect(ralentiPctScale([])).toBe(100);
  });
});

describe('ralentiHorasLabel', () => {
  it('convierte minutos a horas y minutos legibles', () => {
    expect(ralentiHorasLabel(135)).toBe('2 h 15 min');
    expect(ralentiHorasLabel(120)).toBe('2 h');
    expect(ralentiHorasLabel(42.4)).toBe('42 min');
  });

  it('redondea y tolera ausencia de dato', () => {
    expect(ralentiHorasLabel(59.6)).toBe('1 h');
    expect(ralentiHorasLabel(null)).toBe('—');
    expect(ralentiHorasLabel(Number.NaN)).toBe('—');
  });
});

describe('ralentiTabAvailable', () => {
  it('exige al menos una flota y que TODAS tengan el módulo', () => {
    expect(ralentiTabAvailable([])).toBe(false);
    expect(ralentiTabAvailable([{ ralenti_analysis_enabled: true }])).toBe(true);
    expect(
      ralentiTabAvailable([
        { ralenti_analysis_enabled: true },
        { ralenti_analysis_enabled: false },
      ]),
    ).toBe(false);
  });

  it('una flota sin el campo (payload viejo) no habilita la pestaña', () => {
    expect(ralentiTabAvailable([{}])).toBe(false);
    expect(ralentiTabAvailable([{ ralenti_analysis_enabled: true }, {}])).toBe(false);
  });
});

describe('ralentiBucketRange', () => {
  it('un bucket diario es ese mismo día', () => {
    expect(ralentiBucketRange('2026-08-14', {})).toEqual({
      label: '2026-08-14',
      dateFrom: '2026-08-14',
      dateTo: '2026-08-14',
    });
  });

  it('un bucket mensual cubre el mes completo cuando cabe en el rango global', () => {
    expect(
      ralentiBucketRange('2026-08', { date_from: '2026-06-01', date_to: '2026-09-02' }),
    ).toEqual({ label: '2026-08', dateFrom: '2026-08-01', dateTo: '2026-08-31' });
  });

  it('recorta el mes al rango global en los bordes', () => {
    expect(
      ralentiBucketRange('2026-06', { date_from: '2026-06-10', date_to: '2026-09-02' }),
    ).toEqual({ label: '2026-06', dateFrom: '2026-06-10', dateTo: '2026-06-30' });
    expect(
      ralentiBucketRange('2026-09', { date_from: '2026-06-10', date_to: '2026-09-02' }),
    ).toEqual({ label: '2026-09', dateFrom: '2026-09-01', dateTo: '2026-09-02' });
  });

  it('respeta febrero y los años bisiestos', () => {
    expect(ralentiBucketRange('2028-02', {})?.dateTo).toBe('2028-02-29');
    expect(ralentiBucketRange('2026-02', {})?.dateTo).toBe('2026-02-28');
  });

  it('devuelve null ante una etiqueta que no es día ni mes, o un mes fuera del rango', () => {
    expect(ralentiBucketRange('agosto', {})).toBeNull();
    expect(ralentiBucketRange('2026-13', {})).toBeNull();
    expect(ralentiBucketRange('2026-05', { date_from: '2026-06-01' })).toBeNull();
  });
});

describe('ralentiEventBucket', () => {
  it('resuelve el día en hora de Bogotá, no en UTC', () => {
    // 03:30 UTC del 15 son las 22:30 del 14 en Bogotá.
    expect(ralentiEventBucket('2026-08-15T03:30:00Z', 'daily')).toBe('2026-08-14');
    expect(ralentiEventBucket('2026-08-15T12:00:00Z', 'daily')).toBe('2026-08-15');
  });

  it('en mensual devuelve el mes del día local', () => {
    expect(ralentiEventBucket('2026-09-01T02:00:00Z', 'monthly')).toBe('2026-08');
    expect(ralentiEventBucket('2026-09-01T12:00:00Z', 'monthly')).toBe('2026-09');
  });

  it('devuelve null ante un instante ilegible', () => {
    expect(ralentiEventBucket('ayer', 'daily')).toBeNull();
  });
});

describe('ralentiEventSummary', () => {
  const event = {
    event_sk: 'e1',
    vehicle_id: 'v1',
    placa: 'WPK491',
    inicio: '2026-08-14T15:32:00Z',
    fin: '2026-08-14T15:39:30Z',
    duracion_min: 7.5,
    eventos_fuente: 12,
    rpm_promedio: 710,
    rpm_maximo: 790,
    latitud: 3.4,
    longitud: -76.5,
    duration_bucket: '5_10',
    rpm_bucket: '600_800',
  };

  it('concentra el 100 % en el rango de duración y de RPM del episodio', () => {
    const summary = ralentiEventSummary(event);
    expect(summary.total_eventos).toBe(1);
    expect(summary.total_minutos).toBe(7.5);
    expect(summary.vehiculos).toBe(1);
    const hit = summary.por_duracion.find((b) => b.bucket === '5_10')!;
    expect(hit.eventos).toBe(1);
    expect(hit.pct_minutos).toBe(100);
    expect(hit.duracion_promedio_min).toBe(7.5);
    expect(summary.por_duracion.filter((b) => b.eventos > 0)).toHaveLength(1);
    expect(summary.por_rpm.filter((b) => b.eventos > 0).map((b) => b.bucket)).toEqual(['600_800']);
  });

  it('publica todos los rangos, en el orden del catálogo, y la escala se resuelve a porcentaje', () => {
    const summary = ralentiEventSummary(event);
    expect(summary.por_duracion.map((b) => b.bucket)).toEqual(
      RALENTI_DURATION_BUCKETS.map((b) => b.key),
    );
    expect(summary.por_rpm.map((b) => b.bucket)).toEqual(RALENTI_RPM_BUCKETS.map((b) => b.key));
    expect(ralentiPctScale(summary.por_duracion)).toBe(1);
  });
});
