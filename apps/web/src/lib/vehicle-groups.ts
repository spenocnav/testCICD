import { useQuery } from '@tanstack/react-query';

import { api } from '@/lib/api-client';
import type { FleetVehicleGroup } from '@/lib/types';

/**
 * Grupos internos de vehículos por flota (categorías/subcategorías que el
 * cliente define en Navi Vehículos, ej. Bavaria: Regional -> CEDI).
 *
 * El árbol viaja plano (`parent_id`) y acá se derivan las operaciones que
 * todas las pantallas comparten: orden jerárquico con profundidad, expansión
 * nodo -> descendientes (filtrar por una categoría incluye sus subcategorías)
 * y la ruta legible de un nodo.
 */

// Raíz 'vehicles': FleetProvider ya invalida/descarta esa raíz al cambiar de
// flota, así que el catálogo sigue el alcance sin trabajo extra.
const GROUPS_KEY = ['vehicles', 'groups'] as const;

export function useVehicleGroups(enabled = true) {
  return useQuery({
    queryKey: GROUPS_KEY,
    queryFn: ({ signal }) =>
      api.get<FleetVehicleGroup[]>('/api/v1/vehicles/groups', { signal }),
    staleTime: 5 * 60_000,
    enabled,
  });
}

/**
 * Decide si el filtro de grupos puede ofrecerse con el alcance actual.
 *
 * Los grupos son la organización interna de UN cliente. Con más de una flota
 * en el alcance, filtrar por un nodo excluiría en silencio a las demás flotas
 * —también cuando varias tienen grupos: cada árbol pertenece a su cliente y
 * mezclarlos en un dropdown produce números que no describen a nadie—. Misma
 * disciplina que `origen="mixto"` en la calibración de calificación: antes de
 * mostrar algo incoherente, se pide elegir una sola flota.
 */
export function groupFilterAvailability(
  effectiveFleetCount: number,
  groups: FleetVehicleGroup[],
): boolean {
  if (effectiveFleetCount !== 1) {
    return false;
  }
  return groups.some((group) => group.is_active);
}

export interface GroupTreeNode extends FleetVehicleGroup {
  depth: number;
}

/** Aplana el árbol en orden jerárquico (padres antes que hijos, con sangría). */
export function orderGroupTree(groups: FleetVehicleGroup[]): GroupTreeNode[] {
  const byParent = new Map<string, FleetVehicleGroup[]>();
  for (const group of groups) {
    const key = group.parent_id ?? '';
    const bucket = byParent.get(key);
    if (bucket) {
      bucket.push(group);
    } else {
      byParent.set(key, [group]);
    }
  }
  const ordered: GroupTreeNode[] = [];
  const walk = (parentKey: string, depth: number) => {
    const children = byParent.get(parentKey) ?? [];
    children.sort((a, b) => a.name.localeCompare(b.name, 'es'));
    for (const child of children) {
      ordered.push({ ...child, depth });
      walk(child.id, depth + 1);
    }
  };
  walk('', 0);
  // Nodos huérfanos (padre desactivado/faltante en la réplica): al final, como raíz.
  const seen = new Set(ordered.map((g) => g.id));
  for (const group of groups) {
    if (!seen.has(group.id)) {
      ordered.push({ ...group, depth: 0 });
    }
  }
  return ordered;
}

/**
 * Expande la selección a nodo + descendientes: filtrar por "Regional" debe
 * incluir sus CEDI. Devuelve ids únicos.
 */
export function expandGroupIds(groups: FleetVehicleGroup[], selected: string[]): string[] {
  if (!selected.length) {
    return [];
  }
  const childrenOf = new Map<string, string[]>();
  for (const group of groups) {
    if (group.parent_id) {
      const bucket = childrenOf.get(group.parent_id);
      if (bucket) {
        bucket.push(group.id);
      } else {
        childrenOf.set(group.parent_id, [group.id]);
      }
    }
  }
  const expanded = new Set<string>();
  const stack = [...selected];
  while (stack.length) {
    const id = stack.pop() as string;
    if (expanded.has(id)) {
      continue;
    }
    expanded.add(id);
    for (const child of childrenOf.get(id) ?? []) {
      stack.push(child);
    }
  }
  return [...expanded];
}

/**
 * Ancestro raíz de un nodo (para el rollup "por categoría"): sube por
 * `parent_id` hasta la raíz. Un nodo raíz se devuelve a sí mismo; un id
 * desconocido devuelve null (cae al bucket "sin grupo" antes que inventar).
 */
export function rootAncestorId(groups: FleetVehicleGroup[], groupId: string): string | null {
  const byId = new Map(groups.map((g) => [g.id, g]));
  let current = byId.get(groupId);
  if (!current) {
    return null;
  }
  let guard = 0;
  while (current.parent_id && guard < 6) {
    const parent = byId.get(current.parent_id);
    if (!parent) {
      break;
    }
    current = parent;
    guard += 1;
  }
  return current.id;
}

/** Ruta legible de un nodo: "Regional Antioquia / CEDI Medellín". */
export function groupPath(groups: FleetVehicleGroup[], groupId: string | null): string | null {
  if (!groupId) {
    return null;
  }
  const byId = new Map(groups.map((g) => [g.id, g]));
  const parts: string[] = [];
  let current = byId.get(groupId);
  let guard = 0;
  while (current && guard < 6) {
    parts.unshift(current.name);
    current = current.parent_id ? byId.get(current.parent_id) : undefined;
    guard += 1;
  }
  return parts.length ? parts.join(' / ') : null;
}
