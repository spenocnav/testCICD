import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef } from 'react';

import { api } from '@/lib/api-client';
import type { NovedadPriority, NovedadRead, PaginatedNovedades } from '@/lib/types';
import { newIdempotencyKey } from '@/lib/utils';

const NOVEDADES_KEY = ['novedades'] as const;

export interface NovedadesListParams {
  plate?: string;
  /** Grupos internos del cliente, ya expandidos a nodo + descendientes. */
  group_id?: string[];
  date_from?: string;
  date_to?: string;
  /** `true` = sólo resueltas en CloudFleet; `false` = abiertas o sin verificar. */
  external_done?: boolean;
  limit?: number;
  offset?: number;
}

/**
 * Conteos por grupo interno HOJA del vehículo de la novedad (componentes
 * aditivos; el rollup por nivel del árbol lo hace el cliente).
 */
export interface GroupNovedadesBucket {
  group_id: string | null;
  total: number;
  abiertas: number;
  resueltas: number;
}

export interface CreateNovedadInput {
  vehicle_id: string;
  reported_at: string;
  priority: NovedadPriority;
  odometer?: number | null;
  comment?: string | null;
  images: File[];
}

function detailKey(id: string) {
  return ['novedades', id] as const;
}

function toFormData(input: CreateNovedadInput): FormData {
  const form = new FormData();
  form.append('vehicle_id', input.vehicle_id);
  form.append('reported_at', input.reported_at);
  form.append('priority', input.priority);
  if (input.odometer != null) form.append('odometer', String(input.odometer));
  if (input.comment) form.append('comment', input.comment);
  for (const image of input.images) {
    form.append('images', image);
  }
  return form;
}

export function useNovedades(params: NovedadesListParams, enabled = true) {
  return useQuery({
    queryKey: [...NOVEDADES_KEY, 'list', params],
    queryFn: ({ signal }) =>
      api.get<PaginatedNovedades>('/api/v1/novedades', {
        signal,
        query: {
          plate: params.plate,
          group_id: params.group_id,
          date_from: params.date_from,
          date_to: params.date_to,
          external_done: params.external_done,
          limit: params.limit ?? 20,
          offset: params.offset ?? 0,
        },
      }),
    placeholderData: (previous) => previous,
    staleTime: 30_000,
    enabled,
  });
}

export function useNovedadesPorGrupo(
  params: Pick<NovedadesListParams, 'date_from' | 'date_to'>,
  enabled = true,
) {
  return useQuery({
    queryKey: [...NOVEDADES_KEY, 'por-grupo', params],
    queryFn: ({ signal }) =>
      api.get<GroupNovedadesBucket[]>('/api/v1/novedades/por-grupo', {
        signal,
        query: {
          date_from: params.date_from,
          date_to: params.date_to,
        },
      }),
    staleTime: 60_000,
    enabled,
  });
}

export function useNovedad(id: string) {
  return useQuery({
    queryKey: detailKey(id),
    queryFn: () => api.get<NovedadRead>(`/api/v1/novedades/${id}`),
    enabled: Boolean(id),
  });
}

export function useCreateNovedad() {
  const qc = useQueryClient();
  const idempotencyKeys = useRef(new WeakMap<CreateNovedadInput, string>());
  return useMutation({
    // `mutationKey` permite a TanStack agrupar reintentos del mismo intento
    // lógico. El `idempotencyKey` se conserva entre reintentos automáticos
    // de esta mutation; un nuevo `mutate()` genera uno nuevo.
    mutationKey: ['createNovedad'],
    mutationFn: (input: CreateNovedadInput) => {
      let idempotencyKey = idempotencyKeys.current.get(input);
      if (!idempotencyKey) {
        idempotencyKey = newIdempotencyKey();
        idempotencyKeys.current.set(input, idempotencyKey);
      }
      return api.post<NovedadRead>('/api/v1/novedades', toFormData(input), {
        headers: { 'Idempotency-Key': idempotencyKey },
      });
    },
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: NOVEDADES_KEY });
      qc.setQueryData(detailKey(data.id), data);
    },
    onSettled: (_data, _error, input) => {
      idempotencyKeys.current.delete(input);
    },
  });
}

export function useRetryNovedad(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: ['retryNovedad', id],
    mutationFn: () => api.post<NovedadRead>(`/api/v1/novedades/${id}/retry`),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: NOVEDADES_KEY });
      qc.setQueryData(detailKey(data.id), data);
    },
  });
}

/** Borrado administrativo (sólo rol admin). La issue en CloudFleet queda. */
export function useDeleteNovedad(id?: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: ['deleteNovedad', id],
    mutationFn: (targetId?: string) => {
      const resolvedId = targetId ?? id;
      if (!resolvedId) throw new Error('Falta el identificador de la novedad');
      return api.delete<void>(`/api/v1/novedades/${resolvedId}`);
    },
    onSuccess: (_data, targetId) => {
      const resolvedId = targetId ?? id;
      if (resolvedId) qc.removeQueries({ queryKey: detailKey(resolvedId) });
      qc.invalidateQueries({ queryKey: NOVEDADES_KEY });
    },
  });
}
