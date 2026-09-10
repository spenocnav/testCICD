import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/lib/api-client';
import type {
  FleetMotor,
  GeotabDatabase,
  MotorCurvesResponse,
  PaginatedVehicles,
  Vehicle,
} from '@/lib/types';

const ACCESSIBLE_VEHICLES_KEY = ['vehicles'] as const;

export interface VehiclesListParams {
  search?: string;
  marca?: string;
  motor_type?: string;
  /** Grupos internos del cliente, ya expandidos a nodo + descendientes. */
  group_id?: string[];
  is_active?: boolean;
  sort_by?: VehicleSortKey;
  sort_order?: 'asc' | 'desc';
  limit?: number;
  offset?: number;
}

export type VehicleSortKey = 'plate' | 'fleet' | 'marca' | 'motor_type' | 'ano_modelo';

function vehiclesKey(fleetId: string) {
  return ['fleet-vehicles', fleetId] as const;
}

export function useAccessibleVehicles(includeInactive = false, enabled = true) {
  return useQuery({
    queryKey: [...ACCESSIBLE_VEHICLES_KEY, { includeInactive }],
    queryFn: () =>
      api.get<Vehicle[]>('/api/v1/vehicles', {
        query: { include_inactive: includeInactive },
      }),
    staleTime: 60_000,
    enabled,
  });
}

export function useAccessibleVehiclesPaginated(params: VehiclesListParams, enabled = true) {
  return useQuery({
    queryKey: [...ACCESSIBLE_VEHICLES_KEY, 'paginated', params],
    queryFn: () =>
      api.get<PaginatedVehicles>('/api/v1/vehicles/paginated', {
        query: {
          search: params.search,
          marca: params.marca,
          motor_type: params.motor_type,
          group_id: params.group_id,
          is_active: params.is_active,
          sort_by: params.sort_by,
          sort_order: params.sort_order,
          limit: params.limit ?? 20,
          offset: params.offset ?? 0,
        },
      }),
    placeholderData: (previous) => previous,
    staleTime: 60_000,
    enabled,
  });
}

/**
 * Curvas de par y potencia de los motores del alcance de flotas activo.
 *
 * El alcance lo pone el cliente HTTP (header de flota), así que la clave no lo
 * repite: la invalidación por cambio de flota ya la maneja `FleetProvider`.
 */
export function useMotorCurves(enabled = true) {
  return useQuery({
    queryKey: [...ACCESSIBLE_VEHICLES_KEY, 'motor-curves'],
    queryFn: ({ signal }) =>
      api.get<MotorCurvesResponse>('/api/v1/vehicles/motor-curves', { signal }),
    staleTime: 5 * 60_000,
    enabled,
  });
}

/** URL del documento. La sirve el API con las cookies de sesión del navegador. */
export function motorCurveFileUrl(curveId: string) {
  return `/api/v1/vehicles/motor-curves/${curveId}/file`;
}

export function useFleetVehicles(fleetId: string) {
  return useQuery({
    queryKey: vehiclesKey(fleetId),
    queryFn: () => api.get<Vehicle[]>(`/api/v1/fleets/${fleetId}/vehicles`),
    enabled: Boolean(fleetId),
    staleTime: 60_000,
  });
}

export function useFleetDatabases(fleetId: string) {
  return useQuery({
    queryKey: ['fleet-databases', fleetId],
    queryFn: () => api.get<GeotabDatabase[]>(`/api/v1/fleets/${fleetId}/databases`),
    enabled: Boolean(fleetId),
    staleTime: 60_000,
  });
}

export function useFleetMotors(fleetId: string) {
  return useQuery({
    queryKey: ['fleet-motors', fleetId],
    queryFn: () => api.get<FleetMotor[]>(`/api/v1/fleets/${fleetId}/motors`),
    enabled: Boolean(fleetId),
    staleTime: 60_000,
  });
}

export function useToggleVehicle(fleetId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      api.patch<Vehicle>(`/api/v1/fleets/${fleetId}/vehicles/${id}`, { is_active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: vehiclesKey(fleetId) }),
  });
}
