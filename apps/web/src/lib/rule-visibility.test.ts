import { describe, expect, it } from 'vitest';

import { administrativeSafeHabitRules } from './rule-visibility';
import type { GeotabRule } from './types';

let sequence = 0;

function rule(overrides: Partial<GeotabRule>): GeotabRule {
  sequence += 1;
  return {
    id: `rule-${sequence}`,
    rule_id: 'geotab-rpm',
    name: 'RPM motor X13',
    category: 'operacion',
    event_type: null,
    motor_type: 'X13',
    band: 'exceso_rpm',
    is_descenso: false,
    description: null,
    is_active: true,
    ...overrides,
  };
}

describe('administrativeSafeHabitRules', () => {
  it('oculta la aplicación derivada cuando existe la operación gemela', () => {
    const operation = rule({});
    const derived = rule({
      rule_id: 'legacy-derived-rpm-id',
      category: 'habito_seguro',
      event_type: 'exceso_rpm',
      band: null,
      description: 'Excesos de RPM',
    });

    expect(administrativeSafeHabitRules([operation, derived])).toEqual([]);
  });

  it('conserva la aplicación histórica si no existe la operación gemela', () => {
    const legacy = rule({
      category: 'habito_seguro',
      event_type: 'exceso_rpm',
      motor_type: null,
      band: null,
      description: 'Excesos de RPM',
    });

    expect(administrativeSafeHabitRules([legacy])).toEqual([legacy]);
  });

  it('no oculta una aplicación de otro motor ni otros hábitos', () => {
    const operation = rule({});
    const otherMotor = rule({
      category: 'habito_seguro',
      event_type: 'exceso_rpm',
      motor_type: 'X15',
      band: null,
      description: 'Excesos de RPM',
    });
    const braking = rule({
      rule_id: 'geotab-braking',
      name: 'Frenada',
      category: 'habito_seguro',
      event_type: 'frenada',
      motor_type: null,
      band: null,
      description: 'Frenadas bruscas',
    });

    expect(administrativeSafeHabitRules([operation, otherMotor, braking])).toEqual([
      otherMotor,
      braking,
    ]);
  });
});
