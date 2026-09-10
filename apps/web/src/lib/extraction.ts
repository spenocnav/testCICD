import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/lib/api-client';
import type { VehicleExtractionState } from '@/lib/types';

/** Etiquetas legibles de los datasets del worker ETL. */
export const DATASET_LABELS: Record<string, string> = {
  analisis_combustible: 'Combustible',
  factor_carga: 'Factor de carga',
  posicion_pedal: 'Posición del pedal',
  habito_seguro: 'Hábitos seguros',
  alertas: 'Alertas / fallas',
  altimetria: 'Altimetría',
  consumo_def: 'Consumo DEF',
  ubicaciones: 'Ubicaciones',
};

function stateKey(fleetId: string, vehicleId: string) {
  return ['vehicle-extraction-state', fleetId, vehicleId] as const;
}

export function useVehicleExtractionState(fleetId: string, vehicleId: string, enabled = true) {
  return useQuery({
    queryKey: stateKey(fleetId, vehicleId),
    queryFn: () =>
      api.get<VehicleExtractionState[]>(
        `/api/v1/fleets/${fleetId}/vehicles/${vehicleId}/extraction-state`,
      ),
    enabled: enabled && Boolean(fleetId) && Boolean(vehicleId),
    staleTime: 30_000,
  });
}

export interface BackfillInput {
  from_date: string; // ISO-8601
  dataset?: string | null; // null/undefined = todos
}

export function useRequestBackfill(fleetId: string, vehicleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: BackfillInput) =>
      api.post<VehicleExtractionState[]>(
        `/api/v1/fleets/${fleetId}/vehicles/${vehicleId}/backfill`,
        input,
      ),
    onSuccess: (data) => {
      qc.setQueryData(stateKey(fleetId, vehicleId), data);
    },
  });
}

/** Datasets seleccionables, en orden de presentación. */
export const DATASET_KEYS = Object.keys(DATASET_LABELS);

export interface BulkBackfillInput {
  from_date: string; // ISO-8601
  vehicle_ids?: string[] | null; // null/undefined = todas las accesibles
  datasets?: string[] | null; // null/undefined = todos
}

export interface BulkBackfillResult {
  vehicles: number;
  datasets: number;
  rows: number;
}

/**
 * Reprocesamiento masivo de los vehículos de una flota: solo deja el estado
 * listo; el worker lo toma en su próxima ejecución.
 */
export function useFleetBulkBackfill(fleetId: string) {
  return useMutation({
    mutationFn: (input: BulkBackfillInput) =>
      api.post<BulkBackfillResult>(`/api/v1/fleets/${fleetId}/vehicles/backfill`, input),
  });
}
