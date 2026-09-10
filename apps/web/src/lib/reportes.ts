import {
  keepPreviousData,
  queryOptions,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';

import { api } from '@/lib/api-client';
import type {
  AnalyticsVehicle,
  CalificacionCalibracion,
  CalificacionConfigEfectiva,
  CalificacionConfigHistoryItem,
  CalificacionConfigResponse,
  CalificacionResponse,
  CalificacionVehiculo,
  CombustibleDaily,
  CombustibleSummary,
  CombustibleTimePoint,
  FaultDimension,
  FaultEvent,
  FaultParetoItem,
  FaultSeverityBucket,
  FaultSummary,
  FactorCargaSummary,
  FaultTimePoint,
  FuelKind,
  Granularity,
  HabitoMapPoint,
  HabitoSummary,
  HabitoTimePoint,
  HabitoTypeBucket,
  LocationPoint,
  OperativoMonthlyPoint,
  PaginatedCombustible,
  PaginatedFallas,
  PaginatedHabitos,
  PaginatedTimeline,
  PaginatedRalentiEvents,
  PedalSummary,
  RalentiDurationKey,
  RalentiEvent,
  RalentiHeatCell,
  RalentiPlacaRow,
  RalentiRpmKey,
  RalentiSummary,
  RalentiTimePoint,
  RankingMetric,
  DistanceResolutionAction,
  UbicacionesPage,
  VehicleRankingItem,
} from '@/lib/types';

export function useResolveDistance() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      factRowId,
      action,
      reason,
      expectedFingerprint,
    }: {
      factRowId: string;
      action: DistanceResolutionAction;
      reason: string;
      expectedFingerprint: string;
    }) =>
      api.post<{ status: string; decision_id: string }>(
        `/api/v1/reportes/combustible/${factRowId}/distance-resolution`,
        { action, reason, expected_fingerprint: expectedFingerprint },
      ),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['reportes', 'combustible'] }),
        queryClient.invalidateQueries({ queryKey: ['reportes', 'calificacion'] }),
        queryClient.invalidateQueries({ queryKey: ['data-quality'] }),
      ]);
    },
  });
}

export interface CombustibleParams {
  fuel_kind?: FuelKind;
  vehicle_id?: string[];
  motor_type?: string[];
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
}

// Filtros que alimentan summary, series y ranking (sin paginación).
export interface ReportesFilters {
  fuel_kind?: FuelKind;
  vehicle_id?: string[];
  motor_type?: string[];
  date_from?: string;
  date_to?: string;
}

const DAILY_DEFAULT_DAYS = 7;
const DAILY_CALENDAR_MAX_DAYS = 30;
const DAY_IN_MS = 24 * 60 * 60 * 1000;

function parseDateOnly(value: string): Date | null {
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatDateOnly(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function clampDate(date: Date, min: Date, max: Date): Date {
  return new Date(Math.min(Math.max(date.getTime(), min.getTime()), max.getTime()));
}

/** Ventana móvil permitida en granularidad diaria: las últimas 30 fechas, incluyendo hoy. */
export function getDailyDateBounds(today: string): { minDate: string; maxDate: string } {
  const max = parseDateOnly(today);
  if (!max) return { minDate: today, maxDate: today };

  const min = new Date(max.getTime() - (DAILY_CALENDAR_MAX_DAYS - 1) * DAY_IN_MS);

  return { minDate: formatDateOnly(min), maxDate: today };
}

/** Rango que se carga automáticamente al seleccionar la opción «Diaria». */
export function getDefaultDailyDateRange(today: string): { dateFrom: string; dateTo: string } {
  const end = parseDateOnly(today);
  if (!end) return { dateFrom: today, dateTo: today };

  return {
    dateFrom: formatDateOnly(new Date(end.getTime() - (DAILY_DEFAULT_DAYS - 1) * DAY_IN_MS)),
    dateTo: today,
  };
}

/**
 * Restringe el rango elegido manualmente a las últimas 30 fechas disponibles.
 * `anchor` conserva el extremo que acaba de editar el usuario.
 */
export function limitDailyDateRange(
  dateFrom: string,
  dateTo: string,
  today: string,
  anchor: 'from' | 'to' = 'to',
): { dateFrom: string; dateTo: string } {
  const fromInput = parseDateOnly(dateFrom);
  const toInput = parseDateOnly(dateTo);
  const max = parseDateOnly(today);
  const bounds = getDailyDateBounds(today);
  const min = parseDateOnly(bounds.minDate);

  if (!fromInput || !toInput || !min || !max) return { dateFrom, dateTo };

  let from = clampDate(fromInput, min, max);
  let to = clampDate(toInput, min, max);
  const maxDistance = (DAILY_CALENDAR_MAX_DAYS - 1) * DAY_IN_MS;

  if (anchor === 'from') {
    if (to < from) to = new Date(from);
    if (to.getTime() - from.getTime() > maxDistance) {
      to = clampDate(new Date(from.getTime() + maxDistance), min, max);
    }
  } else {
    if (from > to) from = new Date(to);
    if (to.getTime() - from.getTime() > maxDistance) {
      from = clampDate(new Date(to.getTime() - maxDistance), min, max);
    }
  }

  return {
    dateFrom: formatDateOnly(from),
    dateTo: formatDateOnly(to),
  };
}

/** Bucket del eje X (fecha) ya resuelto a rango de fechas para el API. */
export interface CrossBucket {
  label: string;
  dateFrom: string;
  dateTo: string;
}

/**
 * Selección de cross-filter por clic en un gráfico. Estado APARTE de los filtros
 * del toolbar para poder hacer toggle/restore sin pisar el rango que eligió el
 * usuario. `category` se aplica en cada tab sobre su filtro extendido.
 */
export interface CrossFilter {
  vehicleId?: string;
  vehicleLabel?: string;
  bucket?: CrossBucket;
  category?: { key: 'severity' | 'event_type'; value: string };
}

/**
 * Mezcla el cross-filter sobre los filtros base. `exclude` indica la dimensión de
 * la que el propio gráfico es ORIGEN: no se filtra a sí mismo (muestra todas las
 * categorías y atenúa la seleccionada). La categoría se resuelve en cada tab.
 */
export function applyCross(
  base: ReportesFilters,
  cross: CrossFilter,
  exclude?: 'vehicle' | 'bucket',
): ReportesFilters {
  const out: ReportesFilters = { ...base };
  if (cross.vehicleId && exclude !== 'vehicle') {
    out.vehicle_id = [cross.vehicleId];
  }
  if (cross.bucket && exclude !== 'bucket') {
    out.date_from = cross.bucket.dateFrom;
    out.date_to = cross.bucket.dateTo;
  }
  return out;
}

export interface MotorTypeBucket {
  motor_type: string;
  n_vehiculos: number;
  /** Velocidad gobernada del motor; `null` si no está capturada. */
  governed_speed_rpm: number | null;
  /** Sobrevelocidad máxima del motor; `null` si no está capturada. */
  max_overspeed_rpm: number | null;
}

/*
 * Todos los `queryFn` de este archivo propagan el `AbortSignal` de TanStack
 * Query hasta `fetch`. Sin él, cambiar un filtro o de pestaña dejaba consultas
 * obsoletas —varios segundos cada una— corriendo hasta el final, ocupando
 * conexiones del pool de la API (5+5 sobre un solo worker uvicorn) para
 * producir resultados que ya nadie iba a ver. Con la señal, al desmontarse el
 * último observador la petición en vuelo se cancela y el retryer no reintenta.
 */

export function useMotorTypes(enabled = true) {
  return useQuery({
    queryKey: ['reportes', 'motor-types'],
    queryFn: ({ signal }) => api.get<MotorTypeBucket[]>('/api/v1/reportes/motor-types', { signal }),
    staleTime: 5 * 60_000,
    enabled,
  });
}

export function useVehiculos(enabled = true) {
  return useQuery({
    queryKey: ['reportes', 'vehiculos'],
    queryFn: ({ signal }) => api.get<AnalyticsVehicle[]>('/api/v1/reportes/vehiculos', { signal }),
    staleTime: 5 * 60_000,
    enabled,
  });
}

export function useCombustibleDaily(params: CombustibleParams, enabled = true) {
  return useQuery({
    queryKey: ['reportes', 'combustible', params],
    queryFn: ({ signal }) =>
      api.get<PaginatedCombustible>('/api/v1/reportes/combustible/daily', {
        query: {
          vehicle_id: params.vehicle_id,
          motor_type: params.motor_type,
          fuel_kind: params.fuel_kind,
          date_from: params.date_from,
          date_to: params.date_to,
          limit: params.limit ?? 50,
          offset: params.offset ?? 0,
        },
        signal,
      }),
    placeholderData: (prev) => prev,
    enabled,
  });
}

/** Descarga todas las filas diarias que coinciden con los filtros, por páginas. */
export async function fetchCombustibleDaily(
  params: CombustibleParams,
): Promise<CombustibleDaily[]> {
  const limit = 200;
  const rows: CombustibleDaily[] = [];
  let offset = 0;

  while (true) {
    const page = await api.get<PaginatedCombustible>('/api/v1/reportes/combustible/daily', {
      query: {
        vehicle_id: params.vehicle_id,
        motor_type: params.motor_type,
        fuel_kind: params.fuel_kind,
        date_from: params.date_from,
        date_to: params.date_to,
        limit,
        offset,
      },
    });
    rows.push(...page.items);
    offset += page.items.length;
    if (page.items.length === 0 || offset >= page.total) break;
  }

  return rows;
}

/*
 * ---------------------------------------------------------------------------
 * `<nombre>QueryOptions(...)`: UNA sola definición de `{ queryKey, queryFn }`
 * ---------------------------------------------------------------------------
 *
 * Las consultas que además se PRECARGAN (ver `use-tab-prefetch.ts`) declaran su
 * clave y su función en una factoría exportada, y tanto el hook como la
 * precarga la consumen.
 *
 * El motivo es concreto: `queryClient.prefetchQuery` guarda el resultado bajo
 * la clave que se le pasa, y el hook lo lee bajo la clave que él construye. Si
 * las dos claves difieren en un solo campo, la precarga no solo NO sirve —el
 * hook no encuentra nada y pide de nuevo—, sino que además gastó una consulta
 * cara contra la API: es estrictamente peor que no precargar. Duplicar el
 * literal de la clave en dos archivos es exactamente cómo se produce esa
 * divergencia, y no hay tipo ni prueba que la detecte, porque ambas claves son
 * arreglos válidos. Compartir la definición lo vuelve imposible por
 * construcción.
 *
 * Lo que NO entra en la factoría: `placeholderData`, `enabled`, `refetchInterval`
 * y demás opciones de comportamiento del observador. Solo tienen sentido para
 * un hook montado; una precarga no las necesita y `prefetchQuery` las ignora.
 */
export function combustibleSummaryQueryOptions(filters: ReportesFilters) {
  return queryOptions({
    queryKey: ['reportes', 'combustible', 'summary', filters],
    queryFn: ({ signal }) =>
      api.get<CombustibleSummary>('/api/v1/reportes/combustible/summary', {
        query: {
          vehicle_id: filters.vehicle_id,
          motor_type: filters.motor_type,
          fuel_kind: filters.fuel_kind,
          date_from: filters.date_from,
          date_to: filters.date_to,
        },
        signal,
      }),
  });
}

export function useCombustibleSummary(filters: ReportesFilters, enabled = true) {
  return useQuery({
    ...combustibleSummaryQueryOptions(filters),
    placeholderData: (prev) => prev,
    enabled,
  });
}

export function useCombustibleTimeseries(filters: ReportesFilters, granularity: Granularity) {
  return useQuery({
    queryKey: ['reportes', 'combustible', 'ts', granularity, filters],
    queryFn: ({ signal }) =>
      api.get<CombustibleTimePoint[]>('/api/v1/reportes/combustible/timeseries', {
        query: {
          granularity,
          fuel_kind: filters.fuel_kind,
          vehicle_id: filters.vehicle_id,
          motor_type: filters.motor_type,
          date_from: filters.date_from,
          date_to: filters.date_to,
        },
        signal,
      }),
    placeholderData: (prev) => prev,
  });
}

/**
 * Métrica de rendimiento del ranking de combustible.
 *
 * Una flota vocacional (todos sus vehículos con datos marcados así en Navi
 * Vehículos) trabaja por horas ECM y no por kilómetros: su rendimiento es el
 * consumo por hora, no la distancia por unidad de combustible. La unidad la
 * decide `fuel_kind`.
 */
export function rendimientoRankingMetric(isGas: boolean, vocacional: boolean): RankingMetric {
  if (vocacional) return isGas ? 'm3_hr' : 'gal_hr';
  return isGas ? 'km_m3' : 'km_gal';
}

const RENDIMIENTO_METRICS: ReadonlySet<RankingMetric> = new Set<RankingMetric>([
  'km_gal',
  'km_m3',
  'gal_hr',
  'm3_hr',
]);

/**
 * Traduce la métrica elegida al cambiar de unidad o de tipo de operación.
 *
 * El selector guarda UNA elección del usuario; si eligió "rendimiento" y la
 * pestaña pasa de gal a m³, o el alcance resulta vocacional, la elección se
 * conserva y sólo cambia la métrica concreta. El backend responde 422 a una
 * métrica de la otra unidad, así que esta traducción no es cosmética.
 */
export function normalizeRankingMetric(
  metric: RankingMetric,
  isGas: boolean,
  vocacional: boolean,
): RankingMetric {
  return RENDIMIENTO_METRICS.has(metric) ? rendimientoRankingMetric(isGas, vocacional) : metric;
}

export interface RankingMetricOption {
  value: RankingMetric;
  label: string;
  pct?: boolean;
  dec?: number;
}

/** Opciones del selector de métrica del ranking, con la de rendimiento ya resuelta. */
export function rankingMetricOptions(isGas: boolean, vocacional: boolean): RankingMetricOption[] {
  const fuelUnit = isGas ? 'm³' : 'gal';
  return [
    { value: 'comb', label: `Combustible (${fuelUnit})` },
    { value: 'kms_ecm', label: 'Kms ECM' },
    {
      value: rendimientoRankingMetric(isGas, vocacional),
      label: vocacional ? `${fuelUnit}/h` : `km/${fuelUnit}`,
      dec: 2,
    },
    { value: 'pct_exceso_rpm', label: '% Exceso RPM', pct: true },
    { value: 'pct_ralenti', label: '% Ralentí', pct: true },
  ];
}

export function useVehicleRanking(
  filters: ReportesFilters,
  metric: RankingMetric,
  limit = 10,
  sortOrder: 'asc' | 'desc' = 'desc',
  enabled = true,
) {
  return useQuery({
    queryKey: ['reportes', 'combustible', 'ranking', metric, limit, sortOrder, filters],
    queryFn: ({ signal }) =>
      api.get<VehicleRankingItem[]>('/api/v1/reportes/combustible/ranking', {
        query: {
          metric,
          sort_order: sortOrder,
          fuel_kind: filters.fuel_kind,
          limit,
          vehicle_id: filters.vehicle_id,
          motor_type: filters.motor_type,
          date_from: filters.date_from,
          date_to: filters.date_to,
        },
        signal,
      }),
    placeholderData: (prev) => prev,
    enabled,
  });
}

/**
 * Comparativo por grupo interno del cliente. Componentes ADITIVOS por grupo
 * hoja (`group_id` null = vehículos sin grupo); el rollup por nivel y la
 * derivación de la métrica los hace `GroupComparisonCard` en el cliente.
 * Deliberadamente fuera de la precarga por pestaña (`use-tab-prefetch`):
 * solo se consulta cuando el comparativo aplica (una flota y con grupos).
 */
export interface GroupCombustibleBucket {
  group_id: string | null;
  n_vehiculos: number;
  n_registros: number;
  kms: number;
  comb: number;
  hrs: number;
  comb_ralenti: number;
}

export function useCombustiblePorGrupo(filters: ReportesFilters, enabled: boolean) {
  return useQuery({
    queryKey: ['reportes', 'combustible', 'por-grupo', filters],
    queryFn: ({ signal }) =>
      api.get<GroupCombustibleBucket[]>('/api/v1/reportes/combustible/por-grupo', {
        query: {
          fuel_kind: filters.fuel_kind,
          vehicle_id: filters.vehicle_id,
          motor_type: filters.motor_type,
          date_from: filters.date_from,
          date_to: filters.date_to,
        },
        signal,
      }),
    placeholderData: (prev) => prev,
    enabled,
  });
}

// --- Calificación ---

export function calificacionQueryOptions(filters: ReportesFilters) {
  return queryOptions({
    queryKey: ['reportes', 'calificacion', filters],
    queryFn: ({ signal }) =>
      api.get<CalificacionResponse>('/api/v1/reportes/calificacion', {
        query: {
          vehicle_id: filters.vehicle_id,
          motor_type: filters.motor_type,
          date_from: filters.date_from,
          date_to: filters.date_to,
        },
        signal,
      }),
  });
}

export function useCalificacion(filters: ReportesFilters) {
  return useQuery({
    ...calificacionQueryOptions(filters),
    placeholderData: (prev) => prev,
  });
}

// --- Calibración de la calificación (por flota) ---

// Raíz propia y no `['reportes','calificacion', …]`: la invalidación de TanStack
// es por prefijo y la config no debe refetchearse cada vez que cambia un filtro
// de la pantalla. Sigue empezando por `reportes`, que es lo que `FleetProvider`
// invalida al cambiar la flota seleccionada.
const CALIFICACION_CONFIG_KEY = ['reportes', 'calificacion-config'] as const;
const CALIFICACION_CONFIG_PATH = '/api/v1/reportes/calificacion/config';

/**
 * Deja la respuesta con UNA sola clave de valores efectivos. El API publica
 * `config`; una versión anterior del contrato lo llamaba `valores`. Resolver la
 * ambigüedad aquí evita que cada componente tenga que conocerla.
 * Si faltaran los `defaults`, se cae a los valores vigentes: un formulario sin
 * referencia es mejor que una pantalla en blanco.
 * El catálogo de penalizaciones se normaliza a arreglo por la misma razón: un
 * API anterior a la función no lo publica, y la pantalla debe seguir abriendo.
 */
export function normalizeCalificacionConfig(
  raw: CalificacionConfigResponse,
): CalificacionConfigEfectiva | null {
  const valores = raw.config ?? raw.valores;
  if (!valores) return null;
  return {
    origen: raw.origen,
    fleet_id: raw.fleet_id,
    actualizado_en: raw.actualizado_en,
    actualizado_por: raw.actualizado_por,
    valores,
    defaults: raw.defaults ?? valores,
    penalizaciones_disponibles: raw.penalizaciones_disponibles ?? [],
  };
}

/**
 * Promedio de Q General de un subconjunto de filas de la tabla, con los MISMOS
 * pesos que usó el backend.
 *
 * Existe porque el clic en el donut filtra la tabla y el gauge tiene que
 * mostrar el promedio de lo que quedó visible. Calcularlo como media simple
 * —lo que hacía antes— cambiaba la fórmula del gauge al filtrar: la flota
 * agrega ponderando por exposición y el gauge pasaba a ponderar por vehículo.
 *
 * `peso_en_promedio` viene normalizado sobre TODA la flota; renormalizarlo
 * sobre el subconjunto da exactamente el promedio ponderado de ese
 * subconjunto. Cuando la flota eligió promedio simple el backend publica pesos
 * iguales y la cuenta se reduce por sí sola a la media.
 *
 * Un vehículo con `qgen` nulo no entra: o no tuvo exposición suficiente, o no
 * hay datos con los que calificarlo. Sin pesos utilizables cae a media simple,
 * el mismo respaldo que `_promedio_ponderado` en el backend, para no descartar
 * vehículos que puntuaron pero no registraron exposición.
 */
export function promedioQgenVisible(
  vehiculos: readonly Pick<CalificacionVehiculo, 'qgen' | 'peso_en_promedio'>[],
): number | null {
  const evaluados = vehiculos.filter(
    (vehiculo): vehiculo is typeof vehiculo & { qgen: number } => vehiculo.qgen != null,
  );
  if (evaluados.length === 0) return null;
  const totalPeso = evaluados.reduce((sum, v) => sum + (v.peso_en_promedio ?? 0), 0);
  if (totalPeso <= 0) {
    return evaluados.reduce((sum, v) => sum + v.qgen, 0) / evaluados.length;
  }
  return evaluados.reduce((sum, v) => sum + v.qgen * (v.peso_en_promedio ?? 0), 0) / totalPeso;
}

/**
 * Calibración vigente de UNA flota más los valores por defecto del sistema.
 * Sin `fleetId` no hay nada que pedir: el endpoint es por flota.
 */
export function useCalificacionConfig(fleetId: string | null, enabled = true) {
  return useQuery({
    queryKey: [...CALIFICACION_CONFIG_KEY, fleetId],
    queryFn: ({ signal }) =>
      api.get<CalificacionConfigResponse>(CALIFICACION_CONFIG_PATH, {
        query: { fleet_id: fleetId },
        signal,
      }),
    select: normalizeCalificacionConfig,
    enabled: enabled && !!fleetId,
    staleTime: 60_000,
  });
}

/**
 * Historial de cambios de la calibración. Perezoso: se habilita solo cuando el
 * usuario abre el panel, porque nadie lo necesita para calibrar. Exige
 * `reportes.edit` en el backend: es auditoría, no contexto de lectura.
 * El backend responde `{ items: [...] }`; se acepta también un arreglo suelto
 * para que la UI no dependa del envoltorio.
 */
export function useCalificacionConfigHistory(fleetId: string | null, enabled = false) {
  return useQuery({
    queryKey: [...CALIFICACION_CONFIG_KEY, 'history', fleetId],
    queryFn: ({ signal }) =>
      api.get<CalificacionConfigHistoryItem[] | { items?: CalificacionConfigHistoryItem[] }>(
        `${CALIFICACION_CONFIG_PATH}/history`,
        { query: { fleet_id: fleetId }, signal },
      ),
    select: (raw): CalificacionConfigHistoryItem[] => {
      if (Array.isArray(raw)) return raw;
      return raw?.items ?? [];
    },
    // Se releva en cada apertura del panel, sin heredar los 10 min del prefijo
    // `['reportes']`. Esos 10 min se justifican con que el ETL publica los
    // hechos una vez al día; esto no es un hecho del ETL sino una bitácora que
    // cambia cuando una persona guarda una calibración, posiblemente en otra
    // sesión, y mostrar una auditoría desactualizada sin avisar es peor que
    // repetir una consulta que costó 20 ms.
    staleTime: 0,
    enabled: enabled && !!fleetId,
  });
}

/**
 * Invalida la config Y la calificación: recalibrar cambia el puntaje histórico
 * de todos los vehículos de la flota, así que la pantalla que se está viendo
 * queda obsoleta en el mismo instante.
 */
function invalidateCalificacion(qc: ReturnType<typeof useQueryClient>) {
  return Promise.all([
    qc.invalidateQueries({ queryKey: CALIFICACION_CONFIG_KEY }),
    qc.invalidateQueries({ queryKey: ['reportes', 'calificacion'] }),
  ]);
}

/**
 * Guarda la calibración completa —parámetros numéricos y el mapa de
 * penalizaciones activas—. `fleet_id` va en la query, no en el cuerpo: el
 * cuerpo es exactamente la calibración y el destino es parte de la ruta.
 * El cuerpo se envía tal cual lo arma el editor: si `penalizaciones` no viaja,
 * el backend cae al default del registro, y eso es «activa», no «apagada».
 */
export function useSaveCalificacionConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ fleetId, valores }: { fleetId: string; valores: CalificacionCalibracion }) =>
      api.put<CalificacionConfigResponse>(CALIFICACION_CONFIG_PATH, valores, {
        query: { fleet_id: fleetId },
      }),
    onSuccess: () => invalidateCalificacion(qc),
  });
}

/** Devuelve la flota a los valores por defecto (el historial se conserva). */
export function useResetCalificacionConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (fleetId: string) =>
      api.delete<CalificacionConfigResponse>(CALIFICACION_CONFIG_PATH, {
        query: { fleet_id: fleetId },
      }),
    onSuccess: () => invalidateCalificacion(qc),
  });
}

// --- Hábitos operativos (rangos de RPM, ralentí, pedal) ---

export function operativosTimeseriesQueryOptions(
  filters: ReportesFilters,
  granularity: Granularity = 'monthly',
) {
  return queryOptions({
    queryKey: ['reportes', 'operativos', 'ts', granularity, filters],
    queryFn: ({ signal }) =>
      api.get<OperativoMonthlyPoint[]>('/api/v1/reportes/operativos/timeseries', {
        query: {
          granularity,
          vehicle_id: filters.vehicle_id,
          motor_type: filters.motor_type,
          date_from: filters.date_from,
          date_to: filters.date_to,
        },
        signal,
      }),
  });
}

export function useOperativosTimeseries(
  filters: ReportesFilters,
  granularity: Granularity = 'monthly',
) {
  return useQuery({
    ...operativosTimeseriesQueryOptions(filters, granularity),
    placeholderData: (prev) => prev,
  });
}

export function usePedalSummary(filters: ReportesFilters) {
  return useQuery({
    queryKey: ['reportes', 'operativos', 'pedal', filters],
    queryFn: ({ signal }) =>
      api.get<PedalSummary>('/api/v1/reportes/operativos/pedal', {
        query: {
          vehicle_id: filters.vehicle_id,
          motor_type: filters.motor_type,
          date_from: filters.date_from,
          date_to: filters.date_to,
        },
        signal,
      }),
    placeholderData: (prev) => prev,
  });
}

export function useFactorCarga(filters: ReportesFilters, granularity: Granularity = 'monthly') {
  return useQuery({
    queryKey: ['reportes', 'operativos', 'factor-carga', granularity, filters],
    queryFn: ({ signal }) =>
      api.get<FactorCargaSummary>('/api/v1/reportes/operativos/factor-carga', {
        query: {
          granularity,
          vehicle_id: filters.vehicle_id,
          motor_type: filters.motor_type,
          date_from: filters.date_from,
          date_to: filters.date_to,
        },
        signal,
      }),
    placeholderData: (prev) => prev,
  });
}

// --- Hábitos seguros ---

/** Umbral de RPM resuelto por el motor de cada vehículo, no por un número fijo. */
export type RpmThresholdMode = 'governed' | 'overspeed';

export interface HabitoFilters extends ReportesFilters {
  event_type?: string;
  /** Umbral numérico plano. Sigue vivo en el API; la UI ya no lo usa. */
  rpm_min?: number;
  rpm_threshold?: RpmThresholdMode;
}

/**
 * Columnas por las que `/habitos/events` acepta ordenar. Los NULL van siempre al
 * final y el orden desempata por `event_sk`, así que la paginación es estable.
 */
export type HabitoEventSortKey =
  | 'fecha'
  | 'event_value'
  | 'rpm'
  | 'velocidad_kmh'
  | 'g_force'
  | 'duracion_evento'
  | 'distancia_evento_mt';

/**
 * Ordenamiento del detalle de eventos. Deliberadamente FUERA de
 * `HabitoFilters`: summary, by-type, timeseries, ranking y map comparten ese
 * objeto y lo incluyen en su query key, así que ordenar una tabla invalidaría
 * cinco consultas que no cambian de resultado. Solo `events` lo recibe.
 */
export interface HabitoEventSort {
  sort_by: HabitoEventSortKey;
  sort_dir: 'asc' | 'desc';
}

/** El orden actual del API: lo más reciente primero. */
export const DEFAULT_HABITO_EVENT_SORT: HabitoEventSort = {
  sort_by: 'fecha',
  sort_dir: 'desc',
};

export function habitoSummaryQueryOptions(filters: HabitoFilters) {
  return queryOptions({
    queryKey: ['reportes', 'habitos', 'summary', filters],
    queryFn: ({ signal }) =>
      api.get<HabitoSummary>('/api/v1/reportes/habitos/summary', {
        query: {
          vehicle_id: filters.vehicle_id,
          motor_type: filters.motor_type,
          date_from: filters.date_from,
          date_to: filters.date_to,
          event_type: filters.event_type,
          rpm_min: filters.rpm_min,
          rpm_threshold: filters.rpm_threshold,
        },
        signal,
      }),
  });
}

export function useHabitoSummary(filters: HabitoFilters) {
  return useQuery({
    ...habitoSummaryQueryOptions(filters),
    placeholderData: (prev) => prev,
  });
}

export function useHabitoByType(filters: HabitoFilters) {
  return useQuery({
    queryKey: ['reportes', 'habitos', 'by-type', filters],
    queryFn: ({ signal }) =>
      api.get<HabitoTypeBucket[]>('/api/v1/reportes/habitos/by-type', {
        query: {
          vehicle_id: filters.vehicle_id,
          motor_type: filters.motor_type,
          date_from: filters.date_from,
          date_to: filters.date_to,
          rpm_min: filters.rpm_min,
          rpm_threshold: filters.rpm_threshold,
        },
        signal,
      }),
    placeholderData: (prev) => prev,
  });
}

export function useHabitoTimeseries(filters: HabitoFilters, granularity: Granularity = 'daily') {
  return useQuery({
    queryKey: ['reportes', 'habitos', 'ts', granularity, filters],
    queryFn: ({ signal }) =>
      api.get<HabitoTimePoint[]>('/api/v1/reportes/habitos/timeseries', {
        query: {
          granularity,
          vehicle_id: filters.vehicle_id,
          motor_type: filters.motor_type,
          date_from: filters.date_from,
          date_to: filters.date_to,
          event_type: filters.event_type,
          rpm_min: filters.rpm_min,
          rpm_threshold: filters.rpm_threshold,
        },
        signal,
      }),
    placeholderData: (prev) => prev,
  });
}

export function useHabitoRanking(filters: HabitoFilters, limit = 10) {
  return useQuery({
    queryKey: ['reportes', 'habitos', 'ranking', limit, filters],
    queryFn: ({ signal }) =>
      api.get<VehicleRankingItem[]>('/api/v1/reportes/habitos/ranking', {
        query: {
          limit,
          vehicle_id: filters.vehicle_id,
          motor_type: filters.motor_type,
          date_from: filters.date_from,
          date_to: filters.date_to,
          event_type: filters.event_type,
          rpm_min: filters.rpm_min,
          rpm_threshold: filters.rpm_threshold,
        },
        signal,
      }),
    placeholderData: (prev) => prev,
  });
}

export function useHabitoMap(filters: HabitoFilters, limit = 2000) {
  return useQuery({
    queryKey: ['reportes', 'habitos', 'map', limit, filters],
    queryFn: ({ signal }) =>
      api.get<HabitoMapPoint[]>('/api/v1/reportes/habitos/map', {
        query: {
          vehicle_id: filters.vehicle_id,
          motor_type: filters.motor_type,
          date_from: filters.date_from,
          date_to: filters.date_to,
          event_type: filters.event_type,
          rpm_min: filters.rpm_min,
          rpm_threshold: filters.rpm_threshold,
          limit,
        },
        signal,
      }),
    placeholderData: (prev) => prev,
  });
}

/** Ver `GroupCombustibleBucket`: mismas reglas (aditivo, hoja, null = sin grupo). */
export interface GroupHabitosBucket {
  group_id: string | null;
  n_vehiculos: number;
  n_eventos: number;
  kms: number;
}

export function useHabitosPorGrupo(filters: HabitoFilters, enabled: boolean) {
  return useQuery({
    queryKey: ['reportes', 'habitos', 'por-grupo', filters],
    queryFn: ({ signal }) =>
      api.get<GroupHabitosBucket[]>('/api/v1/reportes/habitos/por-grupo', {
        query: {
          vehicle_id: filters.vehicle_id,
          motor_type: filters.motor_type,
          date_from: filters.date_from,
          date_to: filters.date_to,
          event_type: filters.event_type,
        },
        signal,
      }),
    placeholderData: (prev) => prev,
    enabled,
  });
}

export function useHabitoEvents(
  filters: HabitoFilters,
  limit = 50,
  offset = 0,
  enabled = true,
  sort: HabitoEventSort = DEFAULT_HABITO_EVENT_SORT,
) {
  return useQuery({
    // El orden entra en la key como dos primitivas: cambiarlo debe refetchear
    // solo esta consulta.
    queryKey: [
      'reportes',
      'habitos',
      'events',
      limit,
      offset,
      sort.sort_by,
      sort.sort_dir,
      filters,
    ],
    queryFn: ({ signal }) =>
      api.get<PaginatedHabitos>('/api/v1/reportes/habitos/events', {
        query: {
          vehicle_id: filters.vehicle_id,
          motor_type: filters.motor_type,
          date_from: filters.date_from,
          date_to: filters.date_to,
          event_type: filters.event_type,
          rpm_min: filters.rpm_min,
          rpm_threshold: filters.rpm_threshold,
          sort_by: sort.sort_by,
          sort_dir: sort.sort_dir,
          limit,
          offset,
        },
        signal,
      }),
    placeholderData: (prev) => prev,
    enabled,
  });
}

/** Descarga todos los eventos de hábitos que coinciden con los filtros, por páginas. */
export async function fetchHabitoEvents(
  filters: HabitoFilters,
  sort: HabitoEventSort = DEFAULT_HABITO_EVENT_SORT,
): Promise<PaginatedHabitos['items']> {
  const limit = 200;
  const rows: PaginatedHabitos['items'] = [];
  let offset = 0;

  while (true) {
    const page = await api.get<PaginatedHabitos>('/api/v1/reportes/habitos/events', {
      query: {
        vehicle_id: filters.vehicle_id,
        motor_type: filters.motor_type,
        date_from: filters.date_from,
        date_to: filters.date_to,
        event_type: filters.event_type,
        rpm_min: filters.rpm_min,
        rpm_threshold: filters.rpm_threshold,
        sort_by: sort.sort_by,
        sort_dir: sort.sort_dir,
        limit,
        offset,
      },
    });
    rows.push(...page.items);
    offset += page.items.length;
    if (page.items.length === 0 || offset >= page.total) break;
  }

  return rows;
}

// --- Análisis Ralentí ---

/**
 * Filtros del módulo. Los dos rangos son cross-filters locales de la pestaña:
 * el resumen NO los recibe (las barras conservan el contexto completo) y sí los
 * reciben por-placa, mapa de calor, serie y detalle.
 */
export interface RalentiFilters extends ReportesFilters {
  duration_bucket?: RalentiDurationKey;
  rpm_bucket?: RalentiRpmKey;
}

/**
 * Rangos de duración en el orden en que el backend los publica. Las etiquetas
 * replican las del API para que la UI pueda traducir `label` → `key` al hacer
 * clic en un gráfico sin depender de la posición.
 */
export const RALENTI_DURATION_BUCKETS: readonly { key: RalentiDurationKey; label: string }[] = [
  { key: 'lt1', label: '< 1 min' },
  { key: '1_5', label: '1 – 5 min' },
  { key: '5_10', label: '5 – 10 min' },
  { key: 'gt10', label: '> 10 min' },
];

export const RALENTI_RPM_BUCKETS: readonly { key: RalentiRpmKey; label: string }[] = [
  { key: 'lt600', label: '< 600 RPM' },
  { key: '600_800', label: '600 – 800 RPM' },
  { key: '800_1000', label: '800 – 1.000 RPM' },
  { key: '1000_1200', label: '1.000 – 1.200 RPM' },
  { key: 'gt1200', label: '> 1.200 RPM' },
  { key: 'sin_rpm', label: 'Sin RPM' },
];

/** Etiqueta legible del rango que acumula más tiempo en ralentí de una placa. */
export function ralentiDominantBucketLabel(
  row: Pick<RalentiPlacaRow, 'rango_dominante'>,
): string | null {
  if (row.rango_dominante == null) return null;
  return RALENTI_DURATION_BUCKETS.find((b) => b.key === row.rango_dominante)?.label ?? null;
}

/** Zona en la que el backend corta los días de ralentí (`dim_date` local). */
const RALENTI_BUCKET_TIME_ZONE = 'America/Bogota';

/**
 * Bucket de la tendencia al que pertenece un episodio, a partir de su
 * `inicio` ISO (UTC). Devuelve `YYYY-MM-DD` en diaria y `YYYY-MM` en mensual,
 * con el día resuelto en la zona local del reporte para que coincida con la
 * etiqueta que publica la serie. Un instante ilegible devuelve `null`.
 */
export function ralentiEventBucket(inicio: string, granularity: Granularity): string | null {
  const instant = new Date(inicio);
  if (Number.isNaN(instant.getTime())) return null;
  // `en-CA` formatea como `YYYY-MM-DD`, que es justo la forma del bucket diario.
  const day = new Intl.DateTimeFormat('en-CA', {
    timeZone: RALENTI_BUCKET_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(instant);
  return granularity === 'monthly' ? day.slice(0, 7) : day;
}

/**
 * Resuelve el bucket clicado en la tendencia de ralentí a un rango de fechas.
 * El backend publica `YYYY-MM-DD` en diaria y `YYYY-MM` en mensual; un mes se
 * recorta al rango global para no pedir fuera de lo que el usuario eligió, y
 * un valor que no tenga ninguna de las dos formas devuelve `null` en vez de
 * inventar un rango.
 */
export function ralentiBucketRange(bucket: string, base: ReportesFilters): CrossBucket | null {
  if (/^\d{4}-\d{2}-\d{2}$/.test(bucket)) {
    return { label: bucket, dateFrom: bucket, dateTo: bucket };
  }
  const month = /^(\d{4})-(\d{2})$/.exec(bucket);
  if (!month) return null;
  const year = Number(month[1]);
  const mm = Number(month[2]);
  if (mm < 1 || mm > 12) return null;
  const lastDay = new Date(Date.UTC(year, mm, 0)).getUTCDate();
  let dateFrom = `${bucket}-01`;
  let dateTo = `${bucket}-${String(lastDay).padStart(2, '0')}`;
  if (base.date_from && dateFrom < base.date_from) dateFrom = base.date_from;
  if (base.date_to && dateTo > base.date_to) dateTo = base.date_to;
  if (dateFrom > dateTo) return null;
  return { label: bucket, dateFrom, dateTo };
}

/**
 * Factor que lleva los `pct_*` de los rangos a porcentaje 0..100.
 *
 * Los rangos llegan completos, así que la suma de sus porcentajes es el total:
 * ~1 si el backend publica fracciones (la convención del resto de reportes) o
 * ~100 si publica porcentajes. Decidirlo por la suma evita mostrar "0,7 %"
 * donde debía decir "69 %" —o "6.900 %"— si la convención cambia de lado.
 * Con cero eventos todos los porcentajes son 0 y da igual la escala.
 */
export function ralentiPctScale(buckets: readonly { pct_minutos: number }[]): 1 | 100 {
  const total = buckets.reduce(
    (sum, b) => sum + (Number.isFinite(b.pct_minutos) ? b.pct_minutos : 0),
    0,
  );
  return total > 1.5 ? 1 : 100;
}

/**
 * Resumen equivalente a UN episodio, para que los gráficos por rango describan
 * el episodio seleccionado en el detalle sin pedir nada al backend: su rango de
 * duración y de RPM concentran el 100 % y los demás quedan en 0. Los `pct_*`
 * van en escala 0..100 a propósito: una suma de 100 hace que `ralentiPctScale`
 * resuelva la escala sin ambigüedad (un 1,0 podría leerse como 1 %).
 */
export function ralentiEventSummary(event: RalentiEvent): RalentiSummary {
  const share = (matches: boolean) => (matches ? 100 : 0);
  return {
    total_eventos: 1,
    total_minutos: event.duracion_min,
    duracion_promedio_min: event.duracion_min,
    vehiculos: 1,
    por_duracion: RALENTI_DURATION_BUCKETS.map(({ key, label }) => {
      const matches = key === event.duration_bucket;
      return {
        bucket: key,
        label,
        eventos: matches ? 1 : 0,
        minutos: matches ? event.duracion_min : 0,
        pct_eventos: share(matches),
        pct_minutos: share(matches),
        duracion_promedio_min: matches ? event.duracion_min : null,
      };
    }),
    por_rpm: RALENTI_RPM_BUCKETS.map(({ key, label }) => {
      const matches = key === event.rpm_bucket;
      return {
        bucket: key,
        label,
        eventos: matches ? 1 : 0,
        minutos: matches ? event.duracion_min : 0,
        pct_eventos: share(matches),
        pct_minutos: share(matches),
      };
    }),
  };
}

/** Minutos totales como "h min" legible: 135 → "2 h 15 min"; 42 → "42 min". */
export function ralentiHorasLabel(minutos: number | null | undefined): string {
  if (minutos == null || !Number.isFinite(minutos)) return '—';
  const total = Math.round(minutos);
  const horas = Math.floor(total / 60);
  const resto = total % 60;
  if (horas === 0) return `${resto} min`;
  return resto === 0 ? `${horas} h` : `${horas} h ${resto} min`;
}

/**
 * ¿La pestaña Análisis Ralentí está disponible para el alcance actual?
 * Sólo si hay al menos una flota efectiva y TODAS tienen el módulo contratado:
 * mezclar una flota con extracción y otra sin ella daría cifras que no
 * describen a nadie (misma disciplina que el filtro de grupos).
 */
export function ralentiTabAvailable(
  fleets: readonly { ralenti_analysis_enabled?: boolean }[],
): boolean {
  return fleets.length > 0 && fleets.every((f) => f.ralenti_analysis_enabled === true);
}

/** Parámetros comunes a todos los endpoints de ralentí. */
function ralentiQuery(filters: RalentiFilters) {
  return {
    vehicle_id: filters.vehicle_id,
    motor_type: filters.motor_type,
    date_from: filters.date_from,
    date_to: filters.date_to,
    duration_bucket: filters.duration_bucket,
    rpm_bucket: filters.rpm_bucket,
  };
}

export function ralentiSummaryQueryOptions(filters: RalentiFilters) {
  return queryOptions({
    queryKey: ['reportes', 'ralenti', 'summary', filters],
    queryFn: ({ signal }) =>
      api.get<RalentiSummary>('/api/v1/reportes/ralenti/summary', {
        query: ralentiQuery(filters),
        signal,
      }),
  });
}

export function useRalentiSummary(filters: RalentiFilters) {
  return useQuery({
    ...ralentiSummaryQueryOptions(filters),
    placeholderData: (prev) => prev,
  });
}

export function useRalentiPorPlaca(filters: RalentiFilters) {
  return useQuery({
    queryKey: ['reportes', 'ralenti', 'por-placa', filters],
    queryFn: ({ signal }) =>
      api.get<RalentiPlacaRow[]>('/api/v1/reportes/ralenti/por-placa', {
        query: ralentiQuery(filters),
        signal,
      }),
    placeholderData: (prev) => prev,
  });
}

export function useRalentiHeatmap(filters: RalentiFilters, precision = 3) {
  return useQuery({
    queryKey: ['reportes', 'ralenti', 'heatmap', precision, filters],
    queryFn: ({ signal }) =>
      api.get<RalentiHeatCell[]>('/api/v1/reportes/ralenti/heatmap', {
        query: { ...ralentiQuery(filters), precision },
        signal,
      }),
    placeholderData: (prev) => prev,
  });
}

export function useRalentiTimeseries(filters: RalentiFilters, granularity: Granularity = 'daily') {
  return useQuery({
    queryKey: ['reportes', 'ralenti', 'ts', granularity, filters],
    queryFn: ({ signal }) =>
      api.get<RalentiTimePoint[]>('/api/v1/reportes/ralenti/timeseries', {
        query: { ...ralentiQuery(filters), granularity },
        signal,
      }),
    placeholderData: (prev) => prev,
  });
}

/** Columnas por las que `/ralenti/events` acepta ordenar. */
export type RalentiEventSortKey = 'inicio' | 'duracion_segundos' | 'rpm_promedio';

/** Fuera de `RalentiFilters` por la misma razón que `HabitoEventSort`: sólo `events` lo recibe. */
export interface RalentiEventSort {
  sort_by: RalentiEventSortKey;
  sort_dir: 'asc' | 'desc';
}

export const DEFAULT_RALENTI_EVENT_SORT: RalentiEventSort = {
  sort_by: 'inicio',
  sort_dir: 'desc',
};

export interface RalentiEventsPage {
  limit: number;
  offset: number;
  sort: RalentiEventSort;
}

export function useRalentiEvents(filters: RalentiFilters, page: RalentiEventsPage, enabled = true) {
  return useQuery({
    queryKey: [
      'reportes',
      'ralenti',
      'events',
      page.limit,
      page.offset,
      page.sort.sort_by,
      page.sort.sort_dir,
      filters,
    ],
    queryFn: ({ signal }) =>
      api.get<PaginatedRalentiEvents>('/api/v1/reportes/ralenti/events', {
        query: {
          ...ralentiQuery(filters),
          sort_by: page.sort.sort_by,
          sort_dir: page.sort.sort_dir,
          limit: page.limit,
          offset: page.offset,
        },
        signal,
      }),
    placeholderData: (prev) => prev,
    enabled,
  });
}

/** Descarga todos los episodios de ralentí que coinciden con los filtros, por páginas. */
export async function fetchRalentiEvents(
  filters: RalentiFilters,
  sort: RalentiEventSort = DEFAULT_RALENTI_EVENT_SORT,
): Promise<PaginatedRalentiEvents['items']> {
  const limit = 200;
  const rows: PaginatedRalentiEvents['items'] = [];
  let offset = 0;

  while (true) {
    const page = await api.get<PaginatedRalentiEvents>('/api/v1/reportes/ralenti/events', {
      query: {
        ...ralentiQuery(filters),
        sort_by: sort.sort_by,
        sort_dir: sort.sort_dir,
        limit,
        offset,
      },
    });
    rows.push(...page.items);
    offset += page.items.length;
    if (page.items.length === 0 || offset >= page.total) break;
  }

  return rows;
}

// --- Fallas / alertas técnicas ---

export type FaultMatchReview = 'direct' | 'ambiguous' | 'no_match';

export interface FaultFilters extends ReportesFilters {
  severity?: string | string[];
  fault_dimension?: FaultDimension;
  fault_value?: string | string[];
  /** Cardinalidad exacta de candidatas Cummins para revisión Navifault. */
  match_review?: FaultMatchReview;
  management_state?: 'all' | 'pending' | 'escalada' | 'pendiente_registro' | 'repeated' | 'managed';
  /**
   * Excluye las fallas originadas en el dispositivo telemático. Es OPT-IN por
   * petición y sin default en el frontend a propósito: estos endpoints los
   * comparten dos pantallas con propósitos distintos. El tab de Fallas de
   * reportes muestra el histórico DEL VEHÍCULO, así que el ruido del telemático
   * sobra; Navifault trabaja sobre todo lo que llega y necesita esos eventos.
   * Ponerlo como default (aquí o en el backend) le recorta la ventana de 24 h a
   * Navifault en silencio. Solo el tab de Fallas debe enviarlo en `true`.
   */
  exclude_telematics?: boolean;
}

/**
 * Alcance del tab de Fallas de reportes: sin las fallas del dispositivo
 * telemático.
 *
 * Vive aquí, y no dentro del tab, porque lo consumen DOS caminos que tienen que
 * coincidir: los cinco objetos de filtros del propio tab y la precarga por
 * intención de `use-tab-prefetch.ts`. Si divergieran, la precarga guardaría la
 * respuesta bajo una clave que el montaje nunca pide: una consulta cara
 * desperdiciada y el usuario esperando igual.
 *
 * Es explícito y no un default del backend porque `/reportes/fallas/*` lo
 * comparte Navifault, que necesita ver todo lo que llega.
 */
export const FALLAS_REPORT_SCOPE = { exclude_telematics: true } as const;

export function faultSummaryQueryOptions(filters: FaultFilters) {
  return queryOptions({
    queryKey: ['reportes', 'fallas', 'summary', filters],
    queryFn: ({ signal }) =>
      api.get<FaultSummary>('/api/v1/reportes/fallas/summary', {
        query: {
          vehicle_id: filters.vehicle_id,
          motor_type: filters.motor_type,
          date_from: filters.date_from,
          date_to: filters.date_to,
          severity: filters.severity,
          fault_dimension: filters.fault_dimension,
          fault_value: filters.fault_value,
          match_review: filters.match_review,
          exclude_telematics: filters.exclude_telematics,
        },
        signal,
      }),
  });
}

export function useFaultSummary(filters: FaultFilters, refetchIntervalMs?: number | false) {
  return useQuery({
    ...faultSummaryQueryOptions(filters),
    refetchInterval: refetchIntervalMs,
    placeholderData: (prev) => prev,
  });
}

export function useFaultBySeverity(filters: FaultFilters) {
  return useQuery({
    queryKey: ['reportes', 'fallas', 'by-severity', filters],
    queryFn: ({ signal }) =>
      api.get<FaultSeverityBucket[]>('/api/v1/reportes/fallas/by-severity', {
        query: {
          vehicle_id: filters.vehicle_id,
          motor_type: filters.motor_type,
          date_from: filters.date_from,
          date_to: filters.date_to,
          fault_dimension: filters.fault_dimension,
          fault_value: filters.fault_value,
          match_review: filters.match_review,
          exclude_telematics: filters.exclude_telematics,
        },
        signal,
      }),
    placeholderData: (prev) => prev,
  });
}

export function useFaultPareto(filters: FaultFilters, dimension: FaultDimension, limit = 10) {
  return useQuery({
    queryKey: ['reportes', 'fallas', 'pareto', dimension, limit, filters],
    queryFn: ({ signal }) =>
      api.get<FaultParetoItem[]>('/api/v1/reportes/fallas/pareto', {
        query: {
          dimension,
          limit,
          vehicle_id: filters.vehicle_id,
          motor_type: filters.motor_type,
          date_from: filters.date_from,
          date_to: filters.date_to,
          severity: filters.severity,
          match_review: filters.match_review,
          exclude_telematics: filters.exclude_telematics,
        },
        signal,
      }),
    placeholderData: (prev) => prev,
  });
}

export function useFaultTimeseries(filters: FaultFilters, granularity: Granularity = 'daily') {
  return useQuery({
    queryKey: ['reportes', 'fallas', 'ts', granularity, filters],
    queryFn: ({ signal }) =>
      api.get<FaultTimePoint[]>('/api/v1/reportes/fallas/timeseries', {
        query: {
          granularity,
          vehicle_id: filters.vehicle_id,
          motor_type: filters.motor_type,
          date_from: filters.date_from,
          date_to: filters.date_to,
          severity: filters.severity,
          fault_dimension: filters.fault_dimension,
          fault_value: filters.fault_value,
          match_review: filters.match_review,
          exclude_telematics: filters.exclude_telematics,
        },
        signal,
      }),
    placeholderData: (prev) => prev,
  });
}

export function useFaultRanking(filters: FaultFilters, limit = 10) {
  return useQuery({
    queryKey: ['reportes', 'fallas', 'ranking', limit, filters],
    queryFn: ({ signal }) =>
      api.get<VehicleRankingItem[]>('/api/v1/reportes/fallas/ranking', {
        query: {
          limit,
          vehicle_id: filters.vehicle_id,
          motor_type: filters.motor_type,
          date_from: filters.date_from,
          date_to: filters.date_to,
          severity: filters.severity,
          fault_dimension: filters.fault_dimension,
          fault_value: filters.fault_value,
          match_review: filters.match_review,
          exclude_telematics: filters.exclude_telematics,
        },
        signal,
      }),
    placeholderData: (prev) => prev,
  });
}

/** Ver `GroupCombustibleBucket`: mismas reglas (aditivo, hoja, null = sin grupo). */
export interface GroupFallasBucket {
  group_id: string | null;
  n_vehiculos: number;
  n_fallas: number;
  n_eventos: number;
  n_urgentes: number;
}

export function useFallasPorGrupo(filters: FaultFilters, enabled: boolean) {
  return useQuery({
    queryKey: ['reportes', 'fallas', 'por-grupo', filters],
    queryFn: ({ signal }) =>
      api.get<GroupFallasBucket[]>('/api/v1/reportes/fallas/por-grupo', {
        query: {
          vehicle_id: filters.vehicle_id,
          motor_type: filters.motor_type,
          date_from: filters.date_from,
          date_to: filters.date_to,
          exclude_telematics: filters.exclude_telematics,
        },
        signal,
      }),
    placeholderData: (prev) => prev,
    enabled,
  });
}

export function useFaultEvents(
  filters: FaultFilters,
  onlyUrgent: boolean,
  limit = 50,
  offset = 0,
  enabled = true,
  refetchIntervalMs?: number | false,
) {
  return useQuery({
    queryKey: ['reportes', 'fallas', 'events', onlyUrgent, limit, offset, filters],
    queryFn: ({ signal }) =>
      api.get<PaginatedFallas>('/api/v1/reportes/fallas/events', {
        query: {
          vehicle_id: filters.vehicle_id,
          motor_type: filters.motor_type,
          date_from: filters.date_from,
          date_to: filters.date_to,
          severity: filters.severity,
          fault_dimension: filters.fault_dimension,
          fault_value: filters.fault_value,
          match_review: filters.match_review,
          exclude_telematics: filters.exclude_telematics,
          management_state: filters.management_state,
          only_urgent: onlyUrgent,
          limit,
          offset,
        },
        signal,
      }),
    enabled,
    refetchInterval: refetchIntervalMs,
    placeholderData: (prev) => prev,
  });
}

/** Descarga todos los eventos de fallas que coinciden con los filtros, recorriendo todas las páginas. */
export async function fetchFaultEvents(
  filters: FaultFilters,
  onlyUrgent = false,
  maxRecords = 50000,
): Promise<FaultEvent[]> {
  const limit = 200;
  const rows: FaultEvent[] = [];
  let offset = 0;

  while (true) {
    const page = await api.get<PaginatedFallas>('/api/v1/reportes/fallas/events', {
      query: {
        vehicle_id: filters.vehicle_id,
        motor_type: filters.motor_type,
        date_from: filters.date_from,
        date_to: filters.date_to,
        severity: filters.severity,
        fault_dimension: filters.fault_dimension,
        fault_value: filters.fault_value,
        match_review: filters.match_review,
        exclude_telematics: filters.exclude_telematics,
        management_state: filters.management_state,
        only_urgent: onlyUrgent,
        limit,
        offset,
      },
    });
    rows.push(...page.items);
    offset += page.items.length;
    if (page.items.length === 0 || offset >= page.total || rows.length >= maxRecords) break;
  }

  return rows;
}

export interface FaultTimelineParams {
  vehicle_id?: string | null;
  codigo_diagnostico?: number | null;
  codigo_modo_de_falla?: number | null;
  diagnostico?: string | null;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
  enabled?: boolean;
}

/**
 * Línea de tiempo de una falla. Lo consume EXCLUSIVAMENTE Navifault
 * (`fault-detail-dialog.tsx`), así que NO recibe `exclude_telematics`: Navifault
 * necesita ver todo lo que llega. No "unificar" esto con `FaultFilters`.
 */
export function useFaultTimeline(params: FaultTimelineParams) {
  const { enabled = true, ...rest } = params;
  return useQuery({
    queryKey: ['reportes', 'fallas', 'timeline', rest],
    queryFn: ({ signal }) =>
      api.get<PaginatedTimeline>('/api/v1/reportes/fallas/timeline', {
        query: {
          vehicle_id: rest.vehicle_id || undefined,
          codigo_diagnostico: rest.codigo_diagnostico != null ? rest.codigo_diagnostico : undefined,
          codigo_modo_de_falla:
            rest.codigo_modo_de_falla != null ? rest.codigo_modo_de_falla : undefined,
          diagnostico: rest.diagnostico || undefined,
          date_from: rest.date_from || undefined,
          date_to: rest.date_to || undefined,
          limit: rest.limit ?? 10,
          offset: rest.offset ?? 0,
        },
        signal,
      }),
    enabled: Boolean(params.vehicle_id) && enabled,
    placeholderData: keepPreviousData,
  });
}

// ---------------------------------------------------------------------------
// Ubicaciones (informe personalizado)
// ---------------------------------------------------------------------------

/** Ventana máxima del informe; el backend la vuelve a validar. */
export const UBICACIONES_MAX_DAYS = 31;
/** Muestreos que acepta el backend, en minutos. */
export const UBICACIONES_SAMPLE_CHOICES = [5, 10, 15, 30] as const;
export const UBICACIONES_DEFAULT_SAMPLE = 10;

export interface UbicacionesParams {
  vehicle_id: string;
  date_from: string;
  date_to: string;
  sample_minutes?: number;
}

/** Días (inclusive) entre dos fechas `YYYY-MM-DD`; null si alguna es inválida. */
export function countDaysInclusive(dateFrom: string, dateTo: string): number | null {
  const from = parseDateOnly(dateFrom);
  const to = parseDateOnly(dateTo);
  if (!from || !to) return null;
  return Math.floor((to.getTime() - from.getTime()) / DAY_IN_MS) + 1;
}

/**
 * Recorre el rango pidiendo UN día por petición.
 *
 * Cada día se consulta a MyGeotab en el momento y se geocodifica allá, así que
 * nunca se pide el rango completo de golpe ni se guarda nada: las filas llegan
 * al llamante, que decide qué hacer con ellas.
 */
export async function fetchUbicaciones(
  params: UbicacionesParams,
  options: {
    signal?: AbortSignal;
    onDay?: (day: string, rows: LocationPoint[], loaded: number) => void;
  } = {},
): Promise<LocationPoint[]> {
  const rows: LocationPoint[] = [];
  let cursor: string | null = null;

  while (true) {
    const page: UbicacionesPage = await api.get<UbicacionesPage>('/api/v1/reportes/ubicaciones', {
      query: {
        vehicle_id: params.vehicle_id,
        date_from: params.date_from,
        date_to: params.date_to,
        sample_minutes: params.sample_minutes ?? UBICACIONES_DEFAULT_SAMPLE,
        cursor: cursor ?? undefined,
      },
      signal: options.signal,
    });
    rows.push(...page.items);
    options.onDay?.(page.day, page.items, rows.length);
    cursor = page.next_cursor;
    if (!cursor) return rows;
  }
}

export type { AnalyticsVehicle, CombustibleDaily };
