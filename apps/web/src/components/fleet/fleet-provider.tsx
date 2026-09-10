'use client';

import * as React from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { setActiveFleetIds } from '@/lib/api-client';
import { useMe } from '@/lib/auth';
import type { Fleet } from '@/lib/types';

const STORAGE_KEY = 'portal-clientes.selected-fleet';

interface FleetContextValue {
  /** Flotas accesibles por el usuario (todas si es admin). */
  fleets: Fleet[];
  /** IDs de flotas seleccionadas. `[]` = "Todas las flotas visibles". */
  selectedFleetIds: string[];
  /** Flotas seleccionadas resueltas. */
  currentFleets: Fleet[];
  /** Alterna una flota (agrega/remueve). */
  toggleFleet: (id: string) => void;
  /** Setea las flotas activas. `[]` = "Todas las flotas visibles". */
  setFleetIds: (ids: string[]) => void;
  /** ¿El usuario puede elegir entre dos o más flotas? */
  canChoose: boolean;
  /** ¿Ve todas las flotas (admin)? */
  scopeGlobal: boolean;
}

const FleetContext = React.createContext<FleetContextValue | null>(null);
// Query roots que se revalidan al cambiar la flota seleccionada en el topbar
// (el backend filtra por `X-Fleet-Id` en estos endpoints).
const FLEET_SCOPED_QUERY_ROOTS = new Set([
  'reportes',
  'vehicles',
  'fleet-vehicles',
  'mantenimiento',
  'novedades',
  'data-quality',
]);

export function FleetProvider({ children }: { children: React.ReactNode }) {
  const { data } = useMe();
  const qc = useQueryClient();

  // Oculta flotas sin vehículos activos. Si TODAS están vacías, no bloquea el
  // selector (muestra todas) para no dejar al usuario sin scope.
  const fleets = React.useMemo(() => {
    const all = data?.fleets ?? [];
    const withVehicles = all.filter((f) => f.has_vehicles !== false);
    return withVehicles.length > 0 ? withVehicles : all;
  }, [data?.fleets]);
  const scopeGlobal = data?.fleet_scope_global ?? false;
  // El alcance global es una autorización, no una opción adicional del filtro.
  // Con una única flota real se fija automáticamente, incluso para admins.
  const canChoose = fleets.length >= 2;
  const singleFixed = fleets.length === 1;

  const [selectedFleetIds, setSelected] = React.useState<string[]>([]);
  const appliedFleetKeyRef = React.useRef<string | null>(null);
  // Hasta que `/me` responda no se conoce el alcance real. Mientras tanto NO se
  // toca `setActiveFleetIds`: el cliente HTTP usa la selección persistida en
  // localStorage, así el primer lote de queries ya sale con `X-Fleet-Id`.
  // Escribir `[]` aquí borraría ese fallback y la pantalla arrancaría global.
  const [hydrated, setHydrated] = React.useState(false);
  // Identidad del usuario hidratado: si cambia (login / logout), hay que
  // reiniciar el scope local aunque `fleets` sea idéntico al anterior.
  const userKey = data?.user.id ?? null;

  // Clave estable para re-hidratar solo cuando cambia el set de flotas.
  const fleetKey = fleets.map((f) => f.id).join(',');

  // Hidratar desde localStorage validando contra las flotas accesibles.
  // Se re-ejecuta cuando cambia el usuario o el set de flotas: así un login
  // nuevo (post `qc.clear()` + `clearFleetScope()`) parte de cero aunque
  // `localStorage` conserve la selección del usuario anterior.
  React.useEffect(() => {
    if (!data) return;
    // Cambio de usuario → reset duro de la selección y del flag "ya aplicado".
    setSelected([]);
    appliedFleetKeyRef.current = null;
    if (singleFixed) {
      setSelected([fleets[0]!.id]);
      setHydrated(true);
      return;
    }
    const stored = typeof window !== 'undefined' ? window.localStorage.getItem(STORAGE_KEY) : null;
    if (stored) {
      try {
        const parsed: string[] = JSON.parse(stored);
        const valid = parsed.filter((id) => fleets.some((f) => f.id === id));
        setSelected(valid);
      } catch {
        setSelected([]);
      }
    } else {
      setSelected([]);
    }
    setHydrated(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userKey, data ? fleetKey : null, singleFixed]);

  // Propagar al cliente HTTP + refetch de queries scopeadas al cambiar.
  React.useEffect(() => {
    if (!hydrated) return;

    // En UI, [] representa "Todas"; en HTTP se materializa como las flotas
    // visibles para no confundirlo con un alcance administrativo sin filtro.
    const effectiveFleetIds =
      selectedFleetIds.length > 0 ? selectedFleetIds : fleets.map((fleet) => fleet.id);
    const selectedKey = effectiveFleetIds.join(',');
    const previousKey = appliedFleetKeyRef.current;

    if (previousKey === selectedKey) return;

    setActiveFleetIds(effectiveFleetIds);

    // Invalida en cambios posteriores y también en la primera hidratación: si
    // el primer lote salió antes de conocer las flotas, hay que re-scopear
    // para sustituirlo por el alcance real del selector.
    if (previousKey !== null || selectedKey !== '') {
      const isFleetScoped = (query: { queryKey: readonly unknown[] }) => {
        const [root] = query.queryKey;
        return typeof root === 'string' && FLEET_SCOPED_QUERY_ROOTS.has(root);
      };

      // Las inactivas se DESCARTAN, no se invalidan. Invalidarlas solo las marca
      // rancias: la entrada sigue en caché con datos de la flota anterior y, al
      // volver a montarse, `placeholderData: (prev) => prev` los pinta mientras
      // refetchea. Eso es alcance equivocado en pantalla, no solo caché tibia.
      qc.removeQueries({ predicate: isFleetScoped, type: 'inactive' });

      // Las activas sí se invalidan: tienen observador montado y refetchean ya.
      qc.invalidateQueries({ predicate: isFleetScoped });
    }

    appliedFleetKeyRef.current = selectedKey;
  }, [fleetKey, fleets, hydrated, qc, selectedFleetIds]);

  const toggleFleet = React.useCallback((id: string) => {
    setSelected((prev) => {
      const next = prev.includes(id) ? prev.filter((fid) => fid !== id) : [...prev, id];
      if (typeof window !== 'undefined') {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      }
      return next;
    });
  }, []);

  const setFleetIds = React.useCallback((ids: string[]) => {
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
    }
    setSelected(ids);
  }, []);

  const value = React.useMemo<FleetContextValue>(() => {
    const currentFleets = fleets.filter((f) => selectedFleetIds.includes(f.id));
    return {
      fleets,
      selectedFleetIds,
      currentFleets,
      toggleFleet,
      setFleetIds,
      canChoose,
      scopeGlobal,
    };
  }, [fleets, selectedFleetIds, toggleFleet, setFleetIds, canChoose, scopeGlobal]);

  return <FleetContext.Provider value={value}>{children}</FleetContext.Provider>;
}

export function useFleetFilter(): FleetContextValue {
  const ctx = React.useContext(FleetContext);
  if (!ctx) throw new Error('useFleetFilter debe usarse dentro de <FleetProvider>');
  return ctx;
}
