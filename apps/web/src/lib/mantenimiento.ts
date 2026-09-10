import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/lib/api-client';

// ---------------------------------------------------------------------------
// Tipos (espejo de app/schemas/mantenimiento.py del API)
// ---------------------------------------------------------------------------
export interface MttoFilters {
  placas?: string[];
  date_from?: string;
  date_to?: string;
}

export interface ScopePlaca {
  plate: string;
  fleet: string;
  cd: string;
  /** Grupo interno del cliente (fleet_vehicle_groups.id); null = sin grupo. */
  vehicle_group_id: string | null;
}

/**
 * Rollup de disponibilidad por grupo interno HOJA (componentes aditivos:
 * los % los deriva el cliente tras el rollup por nivel del árbol).
 */
export interface GroupDisponibilidadBucket {
  group_id: string | null;
  placas: number;
  should_hours: number;
  downtime_hours: number;
}

export interface DisponibilidadSummary {
  placas: number;
  shouldHours: number;
  unavailableHoursMec: number;
  unavailableHoursProj: number;
  availabilityPctMec: number;
  availabilityPctProj: number;
  affectedVehiclesMec: number;
  affectedVehiclesProj: number;
  orderCount: number;
}

export interface IntervaloItem {
  start: string;
  end: string;
}

export interface OtDetalle {
  number: number;
  type: string | null;
  status: string | null;
  reason: string | null;
  start: string;
  technicalCompletion: string | null;
  overlapHours: number;
}

export interface PlacaDowntime {
  plate: string;
  hours: number;
  availabilityPct: number;
  cd: string;
  fleet: string;
  intervals: IntervaloItem[];
  orders: OtDetalle[];
}

export interface GrupoDisponibilidad {
  name: string;
  availabilityPct: number;
  unavailableHours: number;
  vehicles: number;
}

export interface DisponibilidadResponse {
  summary: DisponibilidadSummary;
  topPlacas: PlacaDowntime[];
  porFlota: GrupoDisponibilidad[];
  porCd: GrupoDisponibilidad[];
}

export interface DisponibilidadTimePoint {
  month: string;
  label: string;
  mecanica: number;
  proyecto: number;
  unavailableHours: number;
}

export interface PreventivoSummary {
  dueCount: number;
  executedCount: number;
  onTimeCount: number;
  lateExecuted: number;
  pending: number;
  overduePending: number;
  executionPct: number;
  onTimePct: number;
  byStatus: Record<string, number>;
}

export interface PreventivoTimePoint {
  month: string;
  label: string;
  ejecucion: number;
  puntualidad: number;
}

/**
 * Ocurrencia de rutina del cronograma (una fila por placa+rutina+fecha).
 * Las tareas de la rutina van en `tasks`; sirve para pendientes e histórico.
 */
export interface ProgramacionRutina {
  plate: string;
  fleet: string;
  cd: string;
  routine: string | null;
  tasks: string[];
  tipo: string | null;
  source: string | null;
  dateToExecute: string | null;
  daysToExecute: number | null;
  status: string | null;
  odometerToExecute: number | null;
  currentOdometer: number | null;
  odometerDiff: number | null;
  avgOdometerDay: number | null;
  etaOdometerDays: number | null;
  hourmeterToExecute: number | null;
  currentHourmeter: number | null;
  hourmeterDiff: number | null;
  avgHourmeterDay: number | null;
  etaHourmeterDays: number | null;
  woNumber: number | null;
  woExecutionDate: string | null;
}

export interface ConfiabilidadSummary {
  failureCount: number;
  vehiclesWithFailures: number;
  mttrHoursAvg: number | null;
  mttrHoursMedian: number | null;
  mttrSample: number;
  mtbfHoursAvg: number | null;
  mtbfHoursMedian: number | null;
  mtbfSample: number;
}

export interface ConfiabilidadTimePoint {
  month: string;
  label: string;
  fallas: number;
  mttr: number | null;
  mtbf: number | null;
}

export interface OrdenesResponse {
  createdOrStarted: number;
  openAtEnd: number;
  overdueOpen: number;
  currentlyOpen: number;
  currentlyOpenOrders: OpenOrderSummary[];
  avgTechnicalCycleHours: number | null;
  medianTechnicalCycleHours: number | null;
  avgFinalClosureLagHours: number | null;
  byStatus: Record<string, number>;
  byType: Record<string, number>;
  byFleet: Record<string, number>;
  byCd: Record<string, number>;
}

export interface OpenOrderSummary {
  number: number;
  plate: string;
  fleet: string;
  type: string | null;
  status: string | null;
  openHours: number;
  estimatedFinishDate: string | null;
  /** null = la OT no declara fecha estimada; no es lo mismo que estar en plazo. */
  overdue: boolean | null;
}

export interface OrdenesTypeTimePoint {
  month: string;
  label: string;
  counts: Record<string, number>;
}

export interface OrdenDetalle {
  number: number;
  plate: string;
  fleet: string;
  cd: string;
  type: string | null;
  status: string | null;
  reason: string | null;
  detectedIssue: string | null;
  startDate: string | null;
  technicalCompletionDate: string | null;
  finalCompletionDate: string | null;
  estimatedFinishDate: string | null;
  costCenter: string | null;
  city: string | null;
  totalCost: number | null;
  vendor: string | null;
  affectsAvailability: boolean;
  warranty: boolean;
}

export interface WorkshopStageDefinition {
  key: string;
  label: string;
  shortLabel: string;
}

export interface WorkshopSegment {
  labelId: number | null;
  labelName: string;
  stageKey: string | null;
  stageLabel: string | null;
  start: string;
  end: string | null;
  hours: number;
  open: boolean;
  timeQuality: string;
  precise: boolean;
  inferred: boolean;
}

export interface WorkshopOrder {
  number: number;
  plate: string;
  fleet: string;
  cd: string;
  site: string;
  vehicle: string;
  status: string | null;
  openedAt: string | null;
  closedAt: string | null;
  totalHours: number;
  classifiedHours: number;
  unclassifiedHours: number;
  coveragePct: number;
  preciseHours: number;
  inferredHours: number;
  outOfCycleEventCount: number;
  stages: Record<string, number>;
  trackingCount: number;
  labelEventCount: number;
  currentLabelId: number | null;
  currentLabel: string | null;
  currentSince: string | null;
  segments: WorkshopSegment[];
}

export interface WorkshopMonthlyPoint {
  month: string;
  label: string;
  orders: number;
  total: number;
  recepcion: number;
  diagnostico: number;
  autorizacion: number;
  repuestos: number;
  reparacion: number;
  calidad: number;
  entrega: number;
}

export interface WorkshopSummary {
  stageTotals: Record<string, number>;
  monthly: WorkshopMonthlyPoint[];
  totalOrders: number;
  ordersWithLabels: number;
  totalHours: number;
  classifiedHours: number;
  unclassifiedHours: number;
  coveragePct: number;
  preciseHours: number;
  inferredHours: number;
  outOfCycleEventCount: number;
}

export interface TiemposTallerResponse {
  labelCatalogVersion: string;
  stageDefinitions: WorkshopStageDefinition[];
  summary: WorkshopSummary;
  items: WorkshopOrder[];
  total: number;
  limit: number;
  offset: number;
  truncated: boolean;
}

export interface RankingItem {
  name: string;
  value: number;
}

export interface RankingGroup {
  peorPuntualidad: RankingItem[];
  menorCumplimiento: RankingItem[];
  masFallas: RankingItem[];
  mttrMasLargo: RankingItem[];
  mttrMasCorto: RankingItem[];
}

export interface RankingsResponse {
  byPlaca: RankingGroup;
  byFleet: RankingGroup;
  /** Nº de flotas en el scope; el bloque de flota se muestra solo si es >=2. */
  fleetCount: number;
}

// ---------------------------------------------------------------------------
// Hooks (queryKey root = 'mantenimiento' → invalidado por el selector de flota)
// ---------------------------------------------------------------------------
const BASE = '/api/v1/mantenimiento';

function query(filters: MttoFilters) {
  return {
    placas: filters.placas,
    date_from: filters.date_from,
    date_to: filters.date_to,
  };
}

export function useMttoPlacas() {
  return useQuery({
    queryKey: ['mantenimiento', 'placas'],
    queryFn: () => api.get<ScopePlaca[]>(`${BASE}/placas`),
    staleTime: 5 * 60_000,
  });
}

export function useDisponibilidadPorGrupo(filters: MttoFilters, enabled = true) {
  return useQuery({
    queryKey: ['mantenimiento', 'disponibilidad', 'por-grupo', filters],
    queryFn: ({ signal }) =>
      api.get<GroupDisponibilidadBucket[]>(`${BASE}/disponibilidad/por-grupo`, {
        signal,
        query: query(filters),
      }),
    staleTime: 60_000,
    enabled,
  });
}

export function useDisponibilidad(filters: MttoFilters, top = 20, enabled = true) {
  return useQuery({
    queryKey: ['mantenimiento', 'disponibilidad', 'summary', filters, top],
    queryFn: () =>
      api.get<DisponibilidadResponse>(`${BASE}/disponibilidad/summary`, {
        query: { ...query(filters), top },
      }),
    placeholderData: (prev) => prev,
    enabled,
  });
}

export function useDisponibilidadTimeseries(filters: MttoFilters) {
  return useQuery({
    queryKey: ['mantenimiento', 'disponibilidad', 'timeseries', filters],
    queryFn: () =>
      api.get<DisponibilidadTimePoint[]>(`${BASE}/disponibilidad/timeseries`, {
        query: query(filters),
      }),
    placeholderData: (prev) => prev,
  });
}

export function usePreventivo(filters: MttoFilters) {
  return useQuery({
    queryKey: ['mantenimiento', 'preventivo', 'summary', filters],
    queryFn: () =>
      api.get<PreventivoSummary>(`${BASE}/preventivo/summary`, { query: query(filters) }),
    placeholderData: (prev) => prev,
  });
}

export function usePreventivoTimeseries(filters: MttoFilters) {
  return useQuery({
    queryKey: ['mantenimiento', 'preventivo', 'timeseries', filters],
    queryFn: () =>
      api.get<PreventivoTimePoint[]>(`${BASE}/preventivo/timeseries`, {
        query: query(filters),
      }),
    placeholderData: (prev) => prev,
  });
}

export function useProximasProgramaciones(placas: string[] | undefined, horizonDays = 90) {
  return useQuery({
    queryKey: ['mantenimiento', 'programacion', 'proximas', placas, horizonDays],
    queryFn: () =>
      api.get<ProgramacionRutina[]>(`${BASE}/programacion/proximas`, {
        query: { placas, horizon_days: horizonDays },
      }),
    placeholderData: (prev) => prev,
  });
}

export function useHistoricoProgramaciones(filters: MttoFilters) {
  return useQuery({
    queryKey: ['mantenimiento', 'programacion', 'historico', filters],
    queryFn: () =>
      api.get<ProgramacionRutina[]>(`${BASE}/programacion/historico`, {
        query: query(filters),
      }),
    placeholderData: (prev) => prev,
  });
}

export function useConfiabilidad(filters: MttoFilters) {
  return useQuery({
    queryKey: ['mantenimiento', 'confiabilidad', 'summary', filters],
    queryFn: () =>
      api.get<ConfiabilidadSummary>(`${BASE}/confiabilidad/summary`, {
        query: query(filters),
      }),
    placeholderData: (prev) => prev,
  });
}

export function useConfiabilidadTimeseries(filters: MttoFilters) {
  return useQuery({
    queryKey: ['mantenimiento', 'confiabilidad', 'timeseries', filters],
    queryFn: () =>
      api.get<ConfiabilidadTimePoint[]>(`${BASE}/confiabilidad/timeseries`, {
        query: query(filters),
      }),
    placeholderData: (prev) => prev,
  });
}

export function useOrdenes(filters: MttoFilters, enabled = true) {
  return useQuery({
    queryKey: ['mantenimiento', 'ordenes', filters],
    queryFn: () => api.get<OrdenesResponse>(`${BASE}/ordenes`, { query: query(filters) }),
    placeholderData: (prev) => prev,
    enabled,
  });
}

export function useOrdenesTimeseries(filters: MttoFilters) {
  return useQuery({
    queryKey: ['mantenimiento', 'ordenes', 'timeseries', filters],
    queryFn: () =>
      api.get<OrdenesTypeTimePoint[]>(`${BASE}/ordenes/timeseries`, {
        query: query(filters),
      }),
    placeholderData: (prev) => prev,
  });
}

export function useOrdenesList(filters: MttoFilters, limit = 500) {
  return useQuery({
    queryKey: ['mantenimiento', 'ordenes', 'list', filters, limit],
    queryFn: () =>
      api.get<OrdenDetalle[]>(`${BASE}/ordenes/list`, {
        query: { ...query(filters), limit },
      }),
    placeholderData: (prev) => prev,
  });
}

export function useTiemposTaller(
  filters: MttoFilters,
  limit = 500,
  offset = 0,
  orderNumber?: number,
  enabled = true,
) {
  return useQuery({
    queryKey: ['mantenimiento', 'tiempos-taller', filters, limit, offset, orderNumber],
    queryFn: () =>
      api.get<TiemposTallerResponse>(`${BASE}/tiempos-taller`, {
        query: { ...query(filters), order_number: orderNumber, limit, offset },
      }),
    placeholderData: (prev) => prev,
    enabled,
  });
}

export function useRankings(filters: MttoFilters) {
  return useQuery({
    queryKey: ['mantenimiento', 'rankings', filters],
    queryFn: () => api.get<RankingsResponse>(`${BASE}/rankings`, { query: query(filters) }),
    placeholderData: (prev) => prev,
  });
}

export interface CloudfleetSyncResult {
  vehiclesFetched: number;
  vehiclesUpserted: number;
  vehiclesMarkedAbsent: number;
  workOrdersFetched: number;
  workOrdersUpserted: number;
  schedulesFetched: number;
  schedulesInserted: number;
  metersTargets: number;
  metersReadings: number;
  metersSent: number;
  metersSkipped: number;
  metersFailed: number;
  metersUncertain: number;
  metersReconciled: number;
}

/**
 * Fuerza una pasada de la réplica CloudFleet (OTs, cronogramas, vehículos) —
 * mismo trabajo del worker `cloudfleet-sync-worker`, a demanda. Al terminar
 * invalida todo el árbol de mantenimiento para refrescar los KPIs.
 */
export function useSyncCloudfleet() {
  const qc = useQueryClient();
  return useMutation<CloudfleetSyncResult, unknown, boolean | void>({
    mutationFn: (full) =>
      api.post<CloudfleetSyncResult>(`${BASE}/sync`, undefined, {
        query: { full: Boolean(full) },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mantenimiento'] }),
  });
}
