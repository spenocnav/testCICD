import { useMutation, useQuery } from '@tanstack/react-query';

import { api } from '@/lib/api-client';
import type {
  DataQualitySummary,
  DistanceAnomalyList,
  DistanceReviewStatus,
  SyncRunKind,
  SyncRunList,
  TrackingHealth,
} from '@/lib/types';

export function useDistanceAnomalies(
  enabled = true,
  reviewStatus?: DistanceReviewStatus,
  limit = 50,
  offset = 0,
) {
  return useQuery({
    queryKey: ['data-quality', 'distance-anomalies', reviewStatus ?? 'all', limit, offset],
    queryFn: () =>
      api.get<DistanceAnomalyList>('/api/v1/data-quality/distance-anomalies', {
        query: { review_status: reviewStatus, limit, offset },
      }),
    enabled,
    placeholderData: (previous) => previous,
  });
}

export function useDataQualitySummary(enabled = true) {
  return useQuery({
    queryKey: ['data-quality', 'summary'],
    queryFn: () => api.get<DataQualitySummary>('/api/v1/data-quality/summary'),
    enabled,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

/** Salud del pipeline de etiquetas. Refresca más seguido que el resto de la
 * pantalla porque es justamente el indicador de "¿sigue vivo ahora mismo?". */
export function useTrackingHealth(enabled = true) {
  return useQuery({
    queryKey: ['data-quality', 'tracking-health'],
    queryFn: () => api.get<TrackingHealth>('/api/v1/data-quality/tracking-etiquetas'),
    enabled,
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
}

/** Encola una corrida del ETL de reportes; el worker la toma en segundos.
 * 409 = ya hay una pendiente/corriendo. */
export function useTriggerReportesEtl() {
  return useMutation({
    mutationFn: () =>
      api.post<{ status: string; request_id: string }>('/api/v1/data-quality/reportes/trigger'),
  });
}

export function useSyncRuns(enabled = true, kind?: SyncRunKind, limit = 10, offset = 0) {
  return useQuery({
    queryKey: ['data-quality', 'sync-runs', kind ?? 'all', limit, offset],
    queryFn: () =>
      api.get<SyncRunList>('/api/v1/data-quality/sync-runs', {
        query: { kind, limit, offset },
      }),
    enabled,
    // Al cambiar de página se conserva la anterior en pantalla en vez de
    // parpadear a skeleton.
    placeholderData: (previous) => previous,
    staleTime: 15_000,
    // Mientras hay una corrida en vuelo se refresca seguido para que el estado
    // y el transcurrido no se queden viejos; en reposo vuelve a 30s para no
    // pegarle a la API sin motivo.
    refetchInterval: (query) => ((query.state.data?.active.length ?? 0) > 0 ? 5_000 : 30_000),
  });
}
