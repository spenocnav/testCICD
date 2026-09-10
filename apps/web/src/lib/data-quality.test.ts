import { describe, expect, it } from 'vitest';

import { healthScoreTone, metricTone } from './data-quality-status';

describe('healthScoreTone', () => {
  it('clasifica los límites del puntaje', () => {
    expect(healthScoreTone(100)).toBe('success');
    expect(healthScoreTone(90)).toBe('success');
    expect(healthScoreTone(89.9)).toBe('warning');
    expect(healthScoreTone(70)).toBe('warning');
    expect(healthScoreTone(69.9)).toBe('critical');
  });
});

describe('metricTone', () => {
  it('muestra éxito cuando no hay incidencias', () => {
    expect(
      metricTone({
        key: 'missing_device',
        label: 'Dispositivo',
        count: 0,
        severity: 'critical',
        description: 'Test',
        examples: [],
      }),
    ).toBe('success');
  });

  it('conserva la severidad cuando hay incidencias', () => {
    expect(
      metricTone({
        key: 'stale_vehicle',
        label: 'Réplica',
        count: 2,
        severity: 'warning',
        description: 'Test',
        examples: [],
      }),
    ).toBe('warning');
  });
});
