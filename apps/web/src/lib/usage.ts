import { useQuery } from '@tanstack/react-query';

import { api } from '@/lib/api-client';

/** Contratos de /api/v1/usage/* (espejo de app/schemas/usage.py). */

export interface UsageDailyPoint {
  day: string;
  requests: number;
  users: number;
}

export interface UsageUserRow {
  user_id: string;
  email: string | null;
  full_name: string | null;
  is_active: boolean | null;
  requests: number;
  last_seen: string;
  top_section: string | null;
}

export interface UsageSectionRow {
  section: string;
  requests: number;
  users: number;
}

export interface UsageFleetRow {
  fleet_id: string;
  fleet_name: string | null;
  requests: number;
  users: number;
}

export interface UsageRouteRow {
  method: string;
  route: string;
  requests: number;
  avg_ms: number;
  p95_ms: number;
}

export interface UsageSummary {
  start_date: string;
  end_date: string;
  total_requests: number;
  active_users: number;
  daily: UsageDailyPoint[];
  users: UsageUserRow[];
  sections: UsageSectionRow[];
  fleets: UsageFleetRow[];
  routes: UsageRouteRow[];
}

export interface UsageUserDetail {
  user_id: string;
  email: string | null;
  full_name: string | null;
  is_active: boolean | null;
  start_date: string;
  end_date: string;
  total_requests: number;
  last_seen: string | null;
  daily: UsageDailyPoint[];
  sections: UsageSectionRow[];
  fleets: UsageFleetRow[];
  routes: UsageRouteRow[];
}

export interface UsageRange {
  startDate: string;
  endDate: string;
}

/** Etiquetas de las secciones del API tal como las conoce el usuario. */
export const SECTION_LABELS: Record<string, string> = {
  reportes: 'Reportes',
  navifault: 'Navifault',
  mantenimiento: 'Informe Mtto',
  novedades: 'Novedades',
  vehicles: 'Vehículos',
  fleets: 'Flotas',
  users: 'Usuarios',
  roles: 'Roles y permisos',
  permissions: 'Roles y permisos',
  modules: 'Módulos',
  'data-quality': 'Calidad de datos',
  usage: 'Uso del portal',
};

export function sectionLabel(section: string | null): string {
  if (!section) return '—';
  return SECTION_LABELS[section] ?? section;
}

const USAGE_KEY = ['usage'] as const;

export function useUsageSummary(range: UsageRange) {
  return useQuery({
    queryKey: [...USAGE_KEY, 'summary', range.startDate, range.endDate],
    queryFn: ({ signal }) =>
      api.get<UsageSummary>('/api/v1/usage/summary', {
        query: { start_date: range.startDate, end_date: range.endDate },
        signal,
      }),
  });
}

export function useUsageUserDetail(userId: string | null, range: UsageRange) {
  return useQuery({
    queryKey: [...USAGE_KEY, 'user', userId, range.startDate, range.endDate],
    enabled: userId !== null,
    queryFn: ({ signal }) =>
      api.get<UsageUserDetail>(`/api/v1/usage/users/${userId}`, {
        query: { start_date: range.startDate, end_date: range.endDate },
        signal,
      }),
  });
}
