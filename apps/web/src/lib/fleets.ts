import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/lib/api-client';
import type { Fleet, SyncResult } from '@/lib/types';

const FLEETS_KEY = ['fleets'] as const;

export interface CreateFleetInput {
  code: string;
  name: string;
  is_active?: boolean;
}

export interface UpdateFleetInput {
  code?: string;
  name?: string;
  is_active?: boolean;
  ralenti_analysis_enabled?: boolean;
}

export function useFleets(onlyActive?: boolean, enabled = true) {
  return useQuery({
    queryKey: [...FLEETS_KEY, { onlyActive }],
    queryFn: () =>
      api.get<Fleet[]>('/api/v1/fleets', {
        query: { only_active: onlyActive },
      }),
    staleTime: 60_000,
    enabled,
  });
}

function invalidateFleets(qc: ReturnType<typeof useQueryClient>) {
  return qc.invalidateQueries({ queryKey: FLEETS_KEY });
}

export function useCreateFleet() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateFleetInput) => api.post<Fleet>('/api/v1/fleets', input),
    onSuccess: () => invalidateFleets(qc),
  });
}

export function useUpdateFleet() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...patch }: { id: string } & UpdateFleetInput) =>
      api.patch<Fleet>(`/api/v1/fleets/${id}`, patch),
    onSuccess: () => invalidateFleets(qc),
  });
}

export function useDeactivateFleet() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/api/v1/fleets/${id}`),
    onSuccess: () => invalidateFleets(qc),
  });
}

/**
 * Dispara el sync de data maestra desde Navi Vehículos. Al terminar invalida
 * todo lo que depende de la réplica (flotas, vehículos, bases) para refrescar.
 */
export function useSyncMasterData() {
  const qc = useQueryClient();
  return useMutation<SyncResult, unknown, boolean>({
    mutationFn: (full: boolean) =>
      api.post<SyncResult>('/api/v1/fleets/sync', undefined, { query: { full } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: FLEETS_KEY });
      qc.invalidateQueries({ queryKey: ['vehicles'] });
      qc.invalidateQueries({ queryKey: ['fleet-vehicles'] });
      qc.invalidateQueries({ queryKey: ['fleet-databases'] });
    },
  });
}
