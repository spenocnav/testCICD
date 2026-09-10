import type { GeotabRule } from './types';

export function administrativeSafeHabitRules(rules: GeotabRule[]): GeotabRule[] {
  const operationRules = rules.filter((rule) => rule.category === 'operacion');
  return rules.filter((rule) => {
    if (rule.category !== 'habito_seguro') return false;
    const isDerivedRpmApplication =
      rule.event_type === 'exceso_rpm' &&
      operationRules.some(
        (operation) => operation.motor_type === rule.motor_type && operation.band === 'exceso_rpm',
      );
    return !isDerivedRpmApplication;
  });
}
