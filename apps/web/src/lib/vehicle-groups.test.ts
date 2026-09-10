import { describe, expect, it } from 'vitest';

import {
  expandGroupIds,
  groupFilterAvailability,
  groupPath,
  orderGroupTree,
} from './vehicle-groups';
import type { FleetVehicleGroup } from './types';

function group(
  id: string,
  parentId: string | null,
  name: string,
  isActive = true,
): FleetVehicleGroup {
  return { id, fleet_id: 'f1', parent_id: parentId, name, is_active: isActive };
}

// Árbol tipo Bavaria: Regional -> CEDI.
const TREE: FleetVehicleGroup[] = [
  group('norte', null, 'Norte'),
  group('cucuta', 'norte', 'Cúcuta'),
  group('galapa', 'norte', 'Galapa'),
  group('sur', null, 'Sur'),
  group('pasto', 'sur', 'Pasto'),
];

describe('groupFilterAvailability', () => {
  it('solo con UNA flota efectiva en alcance y grupos activos', () => {
    expect(groupFilterAvailability(1, TREE)).toBe(true);
  });

  it('se oculta con varias flotas en alcance, tengan o no grupos', () => {
    // Este es el caso que motivó la regla: con Bavaria + otra flota, filtrar
    // por un nodo excluiría en silencio a la flota sin grupos.
    expect(groupFilterAvailability(2, TREE)).toBe(false);
    expect(groupFilterAvailability(5, TREE)).toBe(false);
  });

  it('se oculta sin grupos o con todos inactivos', () => {
    expect(groupFilterAvailability(1, [])).toBe(false);
    expect(
      groupFilterAvailability(1, [group('x', null, 'Viejo', false)]),
    ).toBe(false);
  });

  it('se oculta con alcance vacío (aún no hidratado)', () => {
    expect(groupFilterAvailability(0, TREE)).toBe(false);
  });
});

describe('expandGroupIds', () => {
  it('una categoría incluye sus subcategorías', () => {
    expect(new Set(expandGroupIds(TREE, ['norte']))).toEqual(
      new Set(['norte', 'cucuta', 'galapa']),
    );
  });

  it('un nodo hoja se expande solo a sí mismo', () => {
    expect(expandGroupIds(TREE, ['cucuta'])).toEqual(['cucuta']);
  });

  it('selección mixta deduplica', () => {
    const expanded = expandGroupIds(TREE, ['norte', 'cucuta']);
    expect(new Set(expanded)).toEqual(new Set(['norte', 'cucuta', 'galapa']));
    expect(expanded.length).toBe(3);
  });

  it('vacío se queda vacío (sin filtro, no "todo")', () => {
    expect(expandGroupIds(TREE, [])).toEqual([]);
  });
});

describe('orderGroupTree / groupPath', () => {
  it('ordena padres antes que hijos con profundidad', () => {
    const ordered = orderGroupTree(TREE);
    const names = ordered.map((g) => `${g.depth}:${g.name}`);
    expect(names).toEqual(['0:Norte', '1:Cúcuta', '1:Galapa', '0:Sur', '1:Pasto']);
  });

  it('la ruta legible encadena los padres', () => {
    expect(groupPath(TREE, 'cucuta')).toBe('Norte / Cúcuta');
    expect(groupPath(TREE, 'norte')).toBe('Norte');
    expect(groupPath(TREE, null)).toBeNull();
    expect(groupPath(TREE, 'no-existe')).toBeNull();
  });
});
