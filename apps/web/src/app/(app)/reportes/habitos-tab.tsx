'use client';

import * as React from 'react';
import dynamic from 'next/dynamic';
import { toast } from 'sonner';
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  CarFront,
  Clock,
  Download,
  Loader2,
  Route,
} from 'lucide-react';

import { BarChartCard } from '@/components/charts/bar-chart-card';
import { CHART_TONES } from '@/components/charts/chart-theme';
import { LineChartCard } from '@/components/charts/line-chart-card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { UserPagination } from '@/components/users/user-pagination';
import { useGroupFilterAvailable } from '@/components/vehicles/group-filter';
import {
  GroupComparisonCard,
  type GroupComparisonRow,
} from '@/components/vehicles/group-comparison-card';
import {
  DEFAULT_HABITO_EVENT_SORT,
  fetchHabitoEvents,
  type CrossBucket,
  type HabitoEventSort,
  type HabitoEventSortKey,
  type HabitoFilters,
  type ReportesFilters,
  type RpmThresholdMode,
  useHabitoByType,
  useHabitoEvents,
  useHabitoMap,
  useHabitoRanking,
  useHabitosPorGrupo,
  useHabitoSummary,
  useHabitoTimeseries,
  useMotorTypes,
} from '@/lib/reportes';
import { extractErrorMessage } from '@/lib/api-client';

import type { Granularity, HabitoEvent, HabitoEventUnit, HabitoMapPoint } from '@/lib/types';
import { buildXlsx, type XlsxCell, type XlsxSheet } from '@/lib/xlsx';
import { fmt, fmtCompact, fmtInt, KpiCard } from './shared';

const PAGE_SIZE = 50;

/**
 * Los límites de RPM no son un número único: cada tipo de motor tiene su
 * velocidad gobernada y su sobrevelocidad máxima, y el backend resuelve el
 * umbral por vehículo. Aquí solo se elige CUÁL de los dos límites aplica.
 */
const RPM_MODE_META = {
  governed: {
    value: 'governed',
    short: 'gobernadas',
    label: 'Solo excesos de RPM gobernadas',
    badge: 'RPM > gobernadas',
    excelLabel: 'Solo excesos sobre la velocidad gobernada del motor',
  },
  overspeed: {
    value: 'overspeed',
    short: 'de sobrevelocidad',
    label: 'Solo excesos de RPM de sobrevelocidad',
    badge: 'RPM > sobrevelocidad',
    excelLabel: 'Solo excesos sobre la sobrevelocidad del motor',
  },
} as const satisfies Record<
  RpmThresholdMode,
  { value: RpmThresholdMode; short: string; label: string; badge: string; excelLabel: string }
>;

const RPM_MODES = [RPM_MODE_META.governed, RPM_MODE_META.overspeed];

// Carga diferida sin SSR: leaflet usa `window` y no debe renderizar en servidor.
const EventMapCard = dynamic(
  () => import('@/components/charts/event-map-card').then((m) => m.EventMapCard),
  {
    ssr: false,
    loading: () => <div className="bg-muted h-[516px] animate-pulse rounded-lg" />,
  },
);

/** metros → km legibles. */
function mtToKm(v: number | null | undefined): string {
  if (v == null) return '—';
  return `${fmt(v / 1000, 1)} km`;
}

function fmtSeconds(v: number | null | undefined): string {
  if (v == null) return '—';
  return `${fmt(v, 1)} s`;
}

function fmtKmh(v: number | null | undefined): string {
  if (v == null) return '—';
  return `${fmt(v, 1)} km/h`;
}

/**
 * Métrica que define el evento, con su unidad. Las RPM van en entero (no existe
 * media revolución útil); km/h y G llevan decimales porque distinguen un evento
 * de otro. El signo de los G se conserva: una frenada es negativa.
 */
function fmtEventValue(v: number | null, unit: HabitoEventUnit | null): string {
  if (v == null || unit == null) return '—';
  if (unit === 'RPM') return `${fmtInt(v)} RPM`;
  if (unit === 'G') return `${fmt(v, 2)} G`;
  return `${fmt(v, 1)} ${unit}`;
}

const G_AXIS_LABEL: Record<string, string> = {
  longitudinal: 'eje longitudinal',
  lateral: 'eje lateral',
  vertical: 'eje vertical',
};

/**
 * Métricas secundarias que no merecen columna propia (carga solo existe en
 * eventos de RPM; el eje solo en los de conducción brusca) pero sí explican el
 * valor mostrado.
 */
function eventValueHint(row: HabitoEvent): string | undefined {
  const parts: string[] = [];
  if (row.carga_pct != null) parts.push(`Carga: ${fmt(row.carga_pct, 1)} %`);
  if (row.g_force != null) {
    const axis = row.g_axis ? G_AXIS_LABEL[row.g_axis] : null;
    parts.push(`${fmt(row.g_force, 2)} G${axis ? ` en ${axis}` : ''}`);
  }
  if (row.rpm_value != null && row.event_value_unit !== 'RPM') {
    parts.push(`${fmtInt(row.rpm_value)} RPM`);
  }
  return parts.length > 0 ? parts.join(' · ') : undefined;
}

/**
 * Métrica que ordena cada columna clicable. El nombre se usa para avisar cuando
 * la columna ordenada no tiene datos: el ETL aún no reprocesó el histórico y
 * ordenar por una columna vacía parecería un error de la tabla.
 */
const SORT_METRICS: Partial<
  Record<HabitoEventSortKey, { label: string; get: (row: HabitoEvent) => number | null }>
> = {
  event_value: { label: 'valor', get: (row) => row.event_value },
  rpm: { label: 'RPM', get: (row) => row.rpm_value },
  velocidad_kmh: { label: 'velocidad', get: (row) => row.velocidad_kmh },
  g_force: { label: 'fuerza G', get: (row) => row.g_force },
  duracion_evento: { label: 'duración', get: (row) => row.duracion_evento },
  distancia_evento_mt: { label: 'distancia', get: (row) => row.distancia_evento_mt },
};

/** Nombre visible de cada orden; se reutiliza en el encabezado y en el Excel. */
const SORT_LABELS: Record<HabitoEventSortKey, string> = {
  fecha: 'Fecha/Hora',
  event_value: 'Valor',
  rpm: 'RPM',
  velocidad_kmh: 'Velocidad',
  g_force: 'Fuerza G',
  duracion_evento: 'Duración',
  distancia_evento_mt: 'Distancia',
};

/** Encabezado ordenable: primer clic descendente, el segundo invierte. */
function SortableEventHead({
  label,
  column,
  sort,
  onSort,
  align = 'left',
}: {
  label: string;
  column: HabitoEventSortKey;
  sort: HabitoEventSort;
  onSort: (column: HabitoEventSortKey) => void;
  align?: 'left' | 'right';
}) {
  const active = sort.sort_by === column;
  return (
    <TableHead
      className={align === 'right' ? 'text-right' : undefined}
      aria-sort={active ? (sort.sort_dir === 'asc' ? 'ascending' : 'descending') : 'none'}
    >
      <button
        type="button"
        className={`hover:text-foreground inline-flex items-center gap-1 font-semibold ${
          align === 'right' ? 'flex-row-reverse' : ''
        }`}
        onClick={() => onSort(column)}
        aria-label={`Ordenar por ${label}`}
      >
        {active ? (
          sort.sort_dir === 'asc' ? (
            <ArrowUp className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <ArrowDown className="h-3.5 w-3.5" aria-hidden />
          )
        ) : (
          <ArrowUpDown className="text-muted-foreground h-3.5 w-3.5" aria-hidden />
        )}
        {label}
      </button>
    </TableHead>
  );
}

/**
 * Alerta de motor a partir de los límites que el backend resuelve por vehículo.
 * Sin RPM del evento o sin límite gobernado capturado no se pinta nada.
 */
function getRpmAlert(row: HabitoEvent): { severe: boolean; title: string } | null {
  const rpm = row.rpm_value;
  const governed = row.rpm_governed_limit;
  if (rpm == null || governed == null || rpm <= governed) return null;

  const overspeed = row.rpm_overspeed_limit;
  const severe = overspeed != null && rpm > overspeed;
  const engine = row.motor_type ? `del ${row.motor_type}` : 'del motor';
  const title = severe
    ? `${fmtInt(rpm)} RPM · supera la gobernada ${engine} (${fmtInt(governed)}) y también ` +
      `la sobrevelocidad máxima (${fmtInt(overspeed)}): régimen por encima de la capacidad ` +
      'mecánica del motor'
    : `${fmtInt(rpm)} RPM · supera la gobernada ${engine} (${fmtInt(governed)}): el motor ` +
      'trabaja por encima de su régimen nominal';

  return { severe, title };
}

/** Motor de Material Design Icons (Apache-2.0): https://pictogrammers.com/library/mdi/icon/engine/ */
const ENGINE_PATH =
  'M7 4v2h3v2H7l-2 2v3H3v-3H1v8h2v-3h2v3h3l2 2h8v-4h2v3h3V9h-3v3h-2V8h-6V6h3V4H7Z';

/** Motor íntegro: el régimen superó la velocidad gobernada. */
function EngineAlertIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className="h-4 w-4" aria-hidden>
      <path d={ENGINE_PATH} />
    </svg>
  );
}

/**
 * Motor fracturado: el régimen superó también la sobrevelocidad máxima.
 *
 * Misma silueta que `EngineAlertIcon`, partida en dos mitades por una grieta.
 * La grieta es una máscara y NO un trazo del color de fondo: la fila cambia
 * de fondo al pasar el mouse, al seleccionarse y cuando hay otra fila seleccionada,
 * así que un trazo opaco se vería como una cicatriz de color equivocado.
 *
 * El id de la máscara sale de `useId` saneado, mismo idiom que
 * `components/charts/bar-chart-card.tsx`: la tabla pinta hasta 50 filas y los ids
 * duplicados harían que todas resolvieran contra la primera máscara del documento.
 */
function EngineBrokenIcon() {
  const maskId = `engine-crack-${React.useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden>
      <mask id={maskId} maskUnits="userSpaceOnUse" x="0" y="0" width="24" height="24">
        <rect x="0" y="0" width="24" height="24" fill="white" />
        {/* Una sola grieta, ancha, y sin ramas laterales. Verificado renderizando a
            16 px reales: las ramas se comen el cuerpo y el motor deja de
            reconocerse, y por debajo de 2.2 la grieta se vuelve ruido gris en vez
            de una partidura. 2.5 en un viewBox de 24 son ~1,7 px a 16 px.
            La traza empieza en y=3.6 y no más arriba porque la silueta arranca en
            y=4: por encima quedaría flotando fuera del motor. */}
        <path
          d="M11.5 3.6 9.8 8.6 13.2 12.2 10.2 15.6 12.2 20.6"
          fill="none"
          stroke="black"
          strokeWidth="2.5"
          strokeLinecap="butt"
        />
      </mask>
      <path d={ENGINE_PATH} fill="currentColor" mask={`url(#${maskId})`} />
    </svg>
  );
}

export function HabitosTab({
  filters,
  granularity,
  onSelectGroup,
}: {
  filters: ReportesFilters;
  granularity: Granularity;
  /** Clic en una barra del comparativo por grupo → aplicar ese grupo al filtro global. */
  onSelectGroup?: (groupId: string) => void;
}) {
  const [eventType, setEventType] = React.useState('');
  const [rpmMode, setRpmMode] = React.useState<RpmThresholdMode | null>(null);
  const [vehicleFilter, setVehicleFilter] = React.useState<{
    id: string;
    label: string;
  } | null>(null);
  const [periodFilter, setPeriodFilter] = React.useState<CrossBucket | null>(null);
  const [offset, setOffset] = React.useState(0);
  const [sort, setSort] = React.useState<HabitoEventSort>(DEFAULT_HABITO_EVENT_SORT);
  const [showDetail, setShowDetail] = React.useState(false);
  const [isExporting, setIsExporting] = React.useState(false);
  const mapSectionRef = React.useRef<HTMLDivElement>(null);
  const [selectedEventPoints, setSelectedEventPoints] = React.useState<Map<string, HabitoMapPoint>>(
    () => new Map(),
  );

  React.useEffect(
    () => setOffset(0),
    [filters, eventType, rpmMode, vehicleFilter?.id, periodFilter, sort],
  );
  React.useEffect(() => setPeriodFilter(null), [granularity, filters.date_from, filters.date_to]);
  React.useEffect(() => {
    setSelectedEventPoints(new Map());
  }, [
    filters.date_from,
    filters.date_to,
    filters.motor_type,
    filters.vehicle_id,
    eventType,
    rpmMode,
    vehicleFilter?.id,
    periodFilter?.label,
  ]);

  const dateScopedFilters: ReportesFilters = periodFilter
    ? {
        ...filters,
        date_from: periodFilter.dateFrom,
        date_to: periodFilter.dateTo,
      }
    : filters;
  const scopedVehicleIds = vehicleFilter ? [vehicleFilter.id] : filters.vehicle_id;
  const habFilters: HabitoFilters = {
    ...dateScopedFilters,
    vehicle_id: scopedVehicleIds,
    event_type: eventType || undefined,
    rpm_threshold: rpmMode ?? undefined,
  };
  const typeDistributionFilters: HabitoFilters = {
    ...dateScopedFilters,
    vehicle_id: scopedVehicleIds,
    rpm_threshold: rpmMode ?? undefined,
  };
  const rankingFilters: HabitoFilters = {
    ...dateScopedFilters,
    event_type: eventType || undefined,
    rpm_threshold: rpmMode ?? undefined,
  };
  const seriesFilters: HabitoFilters = {
    ...filters,
    vehicle_id: scopedVehicleIds,
    event_type: eventType || undefined,
    rpm_threshold: rpmMode ?? undefined,
  };

  // Comparativo por grupo interno. Sigue al filtro visible de tipo de evento y
  // al vehículo/periodo seleccionados; el endpoint no acepta `rpm_threshold`,
  // así que ese filtro queda fuera del objeto (y de la query key) a propósito.
  const groupFilters: HabitoFilters = {
    ...dateScopedFilters,
    vehicle_id: scopedVehicleIds,
    event_type: eventType || undefined,
  };

  const summaryQ = useHabitoSummary(habFilters);
  // Conserva todos los tipos como origen del cross-filter, pero responde al
  // vehículo seleccionado desde el ranking.
  const byTypeQ = useHabitoByType(typeDistributionFilters);
  // La tendencia conserva el rango completo porque es el origen del filtro
  // temporal; sí responde a los filtros de tipo y vehículo.
  const seriesQ = useHabitoTimeseries(seriesFilters, granularity);
  // El ranking permanece completo para poder cambiar o quitar el vehículo
  // seleccionado, pero sí responde al tipo de evento activo.
  const rankingQ = useHabitoRanking(rankingFilters);
  const mapQ = useHabitoMap(habFilters);
  const eventsQ = useHabitoEvents(habFilters, PAGE_SIZE, offset, showDetail, sort);
  // Solo se consulta cuando el comparativo aplica (una flota y con grupos);
  // queda fuera de la precarga por pestaña a propósito.
  const groupsAvailable = useGroupFilterAvailable();
  const porGrupoQ = useHabitosPorGrupo(groupFilters, groupsAvailable);
  const groupRows: GroupComparisonRow[] = React.useMemo(
    () =>
      (porGrupoQ.data ?? []).map((b) => ({
        groupId: b.group_id,
        values: {
          n_eventos: b.n_eventos,
          kms: b.kms,
          n_vehiculos: b.n_vehiculos,
        },
      })),
    [porGrupoQ.data],
  );

  const handleSort = React.useCallback((column: HabitoEventSortKey) => {
    setSort((current) =>
      current.sort_by === column
        ? { sort_by: column, sort_dir: current.sort_dir === 'desc' ? 'asc' : 'desc' }
        : { sort_by: column, sort_dir: 'desc' },
    );
  }, []);

  const eventRows = eventsQ.data?.items ?? [];
  // El histórico aún no está reprocesado: casi todas las métricas nuevas llegan
  // NULL. Si la columna ordenada está vacía en toda la página hay que decirlo,
  // porque un orden sin efecto visible se lee como tabla rota.
  const emptySortMetric = React.useMemo(() => {
    const metric = SORT_METRICS[sort.sort_by];
    if (!metric || eventRows.length === 0) return null;
    return eventRows.every((row) => metric.get(row) == null) ? metric.label : null;
  }, [eventRows, sort.sort_by]);
  const mixedUnitsWarning = sort.sort_by === 'event_value' && !eventType;

  // Con el filtro activo, los motores sin el límite capturado quedan fuera
  // (fail-closed en backend). El catálogo solo se consulta si hace falta avisar.
  const motorTypesQ = useMotorTypes(rpmMode != null);
  const motorsWithoutLimit = React.useMemo(() => {
    if (!rpmMode) return [] as string[];
    return (motorTypesQ.data ?? [])
      .filter(
        (bucket) =>
          (rpmMode === 'governed' ? bucket.governed_speed_rpm : bucket.max_overspeed_rpm) == null,
      )
      .map((bucket) => bucket.motor_type);
  }, [motorTypesQ.data, rpmMode]);
  const motorsWithoutLimitNotice =
    motorsWithoutLimit.length === 0
      ? null
      : `${motorsWithoutLimit.length} ${
          motorsWithoutLimit.length === 1 ? 'motor' : 'motores'
        } sin el dato capturado (${motorsWithoutLimit.slice(0, 6).join(', ')}${
          motorsWithoutLimit.length > 6 ? ` y ${motorsWithoutLimit.length - 6} más` : ''
        }): sus vehículos quedan fuera de este filtro.`;

  const selectedPoints = React.useMemo(
    () => Array.from(selectedEventPoints.values()),
    [selectedEventPoints],
  );
  const mapPoints = selectedPoints.length > 0 ? selectedPoints : (mapQ.data ?? []);

  const selectEventOnMap = React.useCallback(
    (
      row: HabitoEvent,
      event: React.MouseEvent<HTMLTableRowElement> | React.KeyboardEvent<HTMLTableRowElement>,
    ) => {
      if (row.latitud == null || row.longitud == null) {
        toast.error('Este evento no tiene coordenadas para ubicarlo en el mapa.');
        return;
      }

      const isSelected = selectedEventPoints.has(row.event_sk);
      const addToSelection = event.ctrlKey || event.metaKey;
      const point: HabitoMapPoint = {
        event_sk: row.event_sk,
        placa: row.placa,
        event_type: row.event_type,
        fecha_y_hora_del_evento: row.fecha_y_hora_del_evento,
        duracion_evento: row.duracion_evento,
        observacion_corta: row.observacion_corta,
        latitud: row.latitud,
        longitud: row.longitud,
      };

      setSelectedEventPoints((current) => {
        const next = new Map(current);
        if (isSelected) {
          next.delete(row.event_sk);
        } else {
          if (!addToSelection) next.clear();
          next.set(row.event_sk, point);
        }
        return next;
      });

      if (!isSelected) {
        window.requestAnimationFrame(() => {
          mapSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
      }
    },
    [selectedEventPoints],
  );

  const s = summaryQ.data;
  const byType = byTypeQ.data ?? [];
  const series = seriesQ.data ?? [];

  const byTypeData = byType.map((b) => ({ label: b.event_type, value: b.n_eventos }));
  const rankingData = (rankingQ.data ?? []).map((r) => ({
    id: r.vehicle_id,
    label: r.vehicle_label ?? r.placa ?? r.vehicle_id ?? '—',
    value: r.value ?? 0,
  }));

  const exportExcel = async () => {
    setIsExporting(true);
    try {
      const rows = await fetchHabitoEvents(habFilters, sort);
      const toCells = (row: unknown[], style?: number): XlsxCell[] =>
        row.map((value) => ({ value: value as XlsxCell['value'], style }));
      // Con `overspeed` el límite comparable es la sobrevelocidad; en cualquier
      // otro caso, la velocidad gobernada del motor de ese vehículo.
      const limitHeader =
        rpmMode === 'overspeed' ? 'Límite RPM (sobrevelocidad)' : 'Límite RPM (gobernada)';
      // Estilos por índice de columna: 4 = dos decimales, 6 = entero con
      // miles. `Valor` queda en formato general porque su unidad cambia por
      // tipo de evento (RPM entero, G con decimales).
      const decimalColumns = new Set([3, 4, 11, 12, 13]);
      const integerColumns = new Set([7, 8]);
      const detailRows: XlsxCell[][] = [
        toCells(
          [
            'Fecha/Hora',
            'Placa',
            'Tipo',
            'Duración (s)',
            'Distancia (km)',
            'Observación',
            'Motor',
            'RPM',
            limitHeader,
            'Valor del evento',
            'Unidad',
            'Velocidad (km/h)',
            'Carga (%)',
            'Fuerza G',
            'Eje G',
          ],
          3,
        ),
        ...rows.map((row) =>
          toCells([
            row.fecha_y_hora_del_evento
              ? new Date(row.fecha_y_hora_del_evento).toLocaleString('es-CO')
              : (row.fecha ?? ''),
            row.placa ?? '',
            row.event_type ?? '',
            row.duracion_evento,
            row.distancia_evento_mt == null ? null : row.distancia_evento_mt / 1000,
            row.observacion_corta ?? '',
            row.motor_type ?? '',
            row.rpm_value,
            rpmMode === 'overspeed' ? row.rpm_overspeed_limit : row.rpm_governed_limit,
            row.event_value,
            row.event_value_unit ?? '',
            row.velocidad_kmh,
            row.carga_pct,
            row.g_force,
            row.g_axis ?? '',
          ]).map((cell, index) => ({
            ...cell,
            style: decimalColumns.has(index) ? 4 : integerColumns.has(index) ? 6 : cell.style,
          })),
        ),
      ];
      const summaryRows: XlsxCell[][] = [
        [{ value: 'Hábitos seguros', style: 1 }],
        [{ value: 'Filtros aplicados', style: 2 }],
        toCells(['Desde', habFilters.date_from ?? 'Todos']),
        toCells(['Hasta', habFilters.date_to ?? 'Todos']),
        toCells(['Tipo de evento', habFilters.event_type ?? 'Todos']),
        toCells(['Filtro RPM', rpmMode ? RPM_MODE_META[rpmMode].excelLabel : 'Todos']),
        toCells([
          'Orden aplicado',
          `${SORT_LABELS[sort.sort_by]}, ${
            sort.sort_dir === 'asc' ? 'ascendente' : 'descendente'
          } (los eventos sin valor van al final)`,
        ]),
        toCells(['Vehículos incluidos', habFilters.vehicle_id?.length ?? s?.n_vehiculos ?? 0]).map(
          (cell, index) => ({ ...cell, style: index === 1 ? 6 : cell.style }),
        ),
        [{ value: 'Indicadores', style: 2 }],
        toCells(['Eventos', s?.n_eventos]).map((cell, index) => ({
          ...cell,
          style: index === 1 ? 6 : cell.style,
        })),
        toCells(['Tipos de evento', s?.n_tipos]).map((cell, index) => ({
          ...cell,
          style: index === 1 ? 6 : cell.style,
        })),
        toCells(['Duración promedio (s)', s?.duracion_promedio], 4),
        toCells(
          [
            'Distancia total (km)',
            s?.distancia_total_mt == null ? null : s.distancia_total_mt / 1000,
          ],
          4,
        ),
      ];
      const workbook = buildXlsx([
        { name: 'Resumen', rows: summaryRows },
        { name: 'Detalle Eventos', rows: detailRows },
      ] satisfies XlsxSheet[]);
      const url = URL.createObjectURL(workbook);
      const link = document.createElement('a');
      link.href = url;
      link.download = `detalle_habitos_seguros_${habFilters.date_from ?? 'periodo'}.xlsx`;
      link.click();
      URL.revokeObjectURL(url);
      toast.success(`${rows.length} eventos exportados.`);
    } catch (err) {
      toast.error(extractErrorMessage(err, 'No se pudieron exportar los datos'));
    } finally {
      setIsExporting(false);
    }
  };

  const pickPeriod = React.useCallback(
    (label: string, payload?: Record<string, unknown>) => {
      const rawPeriod = Number(payload?.periodo);
      let dateFrom = label;
      let dateTo = label;

      if (granularity === 'monthly' && Number.isFinite(rawPeriod)) {
        const year = Math.floor(rawPeriod / 100);
        const month = rawPeriod % 100;
        const monthText = String(month).padStart(2, '0');
        const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
        dateFrom = `${year}-${monthText}-01`;
        dateTo = `${year}-${monthText}-${String(lastDay).padStart(2, '0')}`;
      } else if (Number.isFinite(rawPeriod)) {
        const periodText = String(rawPeriod);
        if (periodText.length === 8) {
          dateFrom = `${periodText.slice(0, 4)}-${periodText.slice(4, 6)}-${periodText.slice(6)}`;
          dateTo = dateFrom;
        }
      }

      if (filters.date_from && dateFrom < filters.date_from) dateFrom = filters.date_from;
      if (filters.date_to && dateTo > filters.date_to) dateTo = filters.date_to;

      const next = { label, dateFrom, dateTo };
      setPeriodFilter((current) => (current?.label === label ? null : next));
    },
    [filters.date_from, filters.date_to, granularity],
  );

  return (
    <>
      {/* Filtro por tipo de evento */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <span className="text-muted-foreground text-xs font-medium uppercase tracking-wide">
          Tipo de evento
        </span>
        <select
          className="border-input bg-background h-8 rounded-md border px-2 text-xs"
          value={eventType}
          onChange={(e) => setEventType(e.target.value)}
          aria-label="Tipo de evento"
        >
          <option value="">Todos los tipos</option>
          {byType.map((b) => (
            <option key={b.event_type} value={b.event_type}>
              {b.event_type}
            </option>
          ))}
        </select>
        <div
          role="group"
          aria-label="Solo excesos de RPM"
          className="border-input flex h-8 items-center gap-3 rounded-md border px-2"
        >
          <span className="text-muted-foreground text-xs">Solo excesos de RPM</span>
          {RPM_MODES.map((option) => (
            <label
              key={option.value}
              className="text-muted-foreground flex items-center gap-1.5 text-xs"
              title={option.label}
            >
              <input
                type="checkbox"
                checked={rpmMode === option.value}
                aria-label={option.label}
                onChange={(e) => {
                  const enabled = e.target.checked;
                  // Mutuamente excluyentes: marcar uno apaga el otro y volver a
                  // marcar el activo limpia el filtro.
                  setRpmMode(enabled ? option.value : null);
                  if (enabled) setEventType('');
                }}
                className="accent-accent-blue"
              />
              {option.short}
            </label>
          ))}
        </div>
        {vehicleFilter && (
          <button
            type="button"
            onClick={() => setVehicleFilter(null)}
            className="bg-accent-blue/10 text-accent-blue hover:bg-accent-blue/20 rounded-pill px-3 py-1 text-xs font-semibold transition-colors"
            aria-label={`Quitar filtro de vehículo ${vehicleFilter.label}`}
          >
            Vehículo: {vehicleFilter.label} ×
          </button>
        )}
      </div>

      {motorsWithoutLimitNotice && (
        <p className="text-muted-foreground mb-4 text-xs">{motorsWithoutLimitNotice}</p>
      )}

      {(periodFilter || vehicleFilter || eventType || rpmMode) && (
        <div
          role="status"
          className="border-accent-blue/30 bg-accent-blue/5 text-accent-blue mb-4 flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-xs"
        >
          <span className="font-semibold">Filtros de gráficos:</span>
          {periodFilter && <Badge variant="info">Periodo: {periodFilter.label}</Badge>}
          {vehicleFilter && <Badge variant="info">Placa: {vehicleFilter.label}</Badge>}
          {eventType && <Badge variant="info">Tipo: {eventType}</Badge>}
          {rpmMode && <Badge variant="info">{RPM_MODE_META[rpmMode].badge}</Badge>}
          <button
            type="button"
            onClick={() => {
              setPeriodFilter(null);
              setVehicleFilter(null);
              setEventType('');
              setRpmMode(null);
            }}
            className="ml-auto font-semibold underline-offset-2 hover:underline"
          >
            Limpiar
          </button>
        </div>
      )}

      {/* KPI cards */}
      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
        <KpiCard
          label="Eventos"
          value={s ? fmtCompact(s.n_eventos) : '—'}
          exact={s ? fmtInt(s.n_eventos) : undefined}
          hint="en el periodo"
          trend={series.map((p) => p.n_eventos)}
        />
        <KpiCard
          label="Vehículos"
          value={s ? String(s.n_vehiculos) : '—'}
          hint="con eventos"
          icon={<CarFront size={32} />}
        />
        <KpiCard
          label="Duración prom."
          value={fmtSeconds(s?.duracion_promedio)}
          hint="por evento"
          icon={<Clock size={32} />}
        />
        <KpiCard
          label="Distancia total"
          value={
            s?.distancia_total_mt != null ? `${fmtCompact(s.distancia_total_mt / 1000)} km` : '—'
          }
          exact={mtToKm(s?.distancia_total_mt)}
          hint="recorrida en eventos"
          icon={<Route size={32} />}
        />
      </div>

      {/* Comparativo por grupo interno del cliente (solo con una flota en
          alcance y con grupos; el componente se oculta solo). */}
      <div className="mb-6">
        <GroupComparisonCard
          title="Eventos por 1.000 km por grupo"
          subtitle="Densidad de eventos ponderada por kilómetros; clic en una barra filtra la pestaña."
          rows={groupRows}
          derive={(t) => ((t.kms ?? 0) > 0 ? ((t.n_eventos ?? 0) / (t.kms ?? 1)) * 1000 : null)}
          format={(value) => fmt(value, 1)}
          detail={(t) =>
            `${fmtInt(t.n_vehiculos ?? 0)} placas · ${fmtInt(t.n_eventos ?? 0)} eventos · ${fmtInt(
              t.kms ?? 0,
            )} km`
          }
          onSelectGroup={onSelectGroup}
          sortDesc
          isLoading={porGrupoQ.isLoading}
        />
      </div>

      {/* Distribución por tipo + tendencia */}
      <div className="mb-6 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <BarChartCard
          title="Eventos por tipo"
          subtitle="Clic en una barra para filtrar las demás visualizaciones"
          data={byTypeData}
          categoryKey="label"
          valueKey="value"
          valueName="Eventos"
          color={CHART_TONES.blueMuted}
          isLoading={byTypeQ.isLoading}
          format={(v) => fmtInt(v)}
          selectedId={eventType || null}
          onBarClick={(item) => {
            const selectedType = item.label;
            if (typeof selectedType === 'string' && selectedType) {
              setEventType((current) => (current === selectedType ? '' : selectedType));
            }
          }}
        />
        <LineChartCard
          title="Tendencia de eventos"
          subtitle={`Eventos por ${granularity === 'daily' ? 'día' : 'mes'} · clic para filtrar`}
          data={series}
          xKey="label"
          isLoading={seriesQ.isLoading}
          selectedBucket={periodFilter?.label ?? null}
          onBucketClick={pickPeriod}
          series={[
            {
              key: 'n_eventos',
              name: 'Eventos',
              color: CHART_TONES.redMuted,
              format: (v) => fmtInt(v),
            },
          ]}
        />
      </div>

      {/* Ranking por vehículo */}
      <div className="mb-6">
        <BarChartCard
          title="Ranking por vehículo"
          subtitle="Clic en una barra para filtrar tendencia, tipos, mapa y detalle"
          data={rankingData}
          categoryKey="label"
          valueKey="value"
          valueName="Eventos"
          categoryLabelAlign="start"
          color={CHART_TONES.grayMuted}
          isLoading={rankingQ.isLoading}
          format={(v) => fmtInt(v)}
          selectedId={vehicleFilter?.id ?? null}
          onBarClick={(item) => {
            const id = item.id;
            const label = item.label;
            if (typeof id === 'string' && id) {
              setVehicleFilter((current) =>
                current?.id === id ? null : { id, label: typeof label === 'string' ? label : id },
              );
            }
          }}
        />
      </div>

      {/* Mapa de eventos georreferenciados */}
      <div ref={mapSectionRef} className="mb-6 scroll-mt-24">
        <EventMapCard
          title="Mapa de eventos"
          subtitle={
            selectedPoints.length > 0
              ? `${selectedPoints.length} evento${selectedPoints.length === 1 ? '' : 's'} seleccionado${selectedPoints.length === 1 ? '' : 's'}`
              : `${mapQ.data?.length ?? 0} eventos ubicados en el periodo filtrado`
          }
          points={mapPoints}
          isLoading={selectedPoints.length === 0 && mapQ.isLoading}
        />
      </div>

      {/* Tabla de eventos */}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-heading text-sm font-bold tracking-tight">Detalle de eventos</h2>
          {periodFilter && <Badge variant="info">Periodo: {periodFilter.label}</Badge>}
          {vehicleFilter && <Badge variant="info">Vehículo: {vehicleFilter.label}</Badge>}
          {eventType && <Badge variant="info">Tipo: {eventType}</Badge>}
          {rpmMode && <Badge variant="info">{RPM_MODE_META[rpmMode].badge}</Badge>}
          {selectedPoints.length > 0 && (
            <Badge variant="info">
              {selectedPoints.length} seleccionado{selectedPoints.length === 1 ? '' : 's'}
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setShowDetail((visible) => !visible)}
            aria-expanded={showDetail}
          >
            {showDetail ? 'Ocultar detalle' : 'Mostrar detalle'}
          </Button>
          {showDetail && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={exportExcel}
              disabled={isExporting || eventsQ.isLoading}
            >
              {isExporting ? (
                <Loader2 className="animate-spin" aria-hidden />
              ) : (
                <Download aria-hidden />
              )}
              {isExporting ? 'Exportando…' : 'Exportar Excel'}
            </Button>
          )}
        </div>
      </div>
      {showDetail && eventsQ.isError && (
        <p className="text-destructive text-sm">Error al cargar datos. Intenta de nuevo.</p>
      )}
      {showDetail && mixedUnitsWarning && (
        <p className="text-muted-foreground mb-2 text-xs">
          El valor mezcla unidades entre tipos de evento (RPM, km/h y G): filtra un tipo para
          comparar magnitudes equivalentes.
        </p>
      )}
      {showDetail && emptySortMetric && (
        <p className="text-muted-foreground mb-2 text-xs">
          Sin datos de {emptySortMetric} en estas filas: el histórico todavía no se ha reprocesado y
          los eventos sin valor se ordenan al final.
        </p>
      )}

      {showDetail && eventsQ.isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </div>
      ) : showDetail ? (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <SortableEventHead
                  label={SORT_LABELS.fecha}
                  column="fecha"
                  sort={sort}
                  onSort={handleSort}
                />
                <TableHead>Placa</TableHead>
                <TableHead>Tipo</TableHead>
                <SortableEventHead
                  label={SORT_LABELS.event_value}
                  column="event_value"
                  sort={sort}
                  onSort={handleSort}
                  align="right"
                />
                <SortableEventHead
                  label={SORT_LABELS.velocidad_kmh}
                  column="velocidad_kmh"
                  sort={sort}
                  onSort={handleSort}
                  align="right"
                />
                <SortableEventHead
                  label={SORT_LABELS.duracion_evento}
                  column="duracion_evento"
                  sort={sort}
                  onSort={handleSort}
                  align="right"
                />
                <SortableEventHead
                  label={SORT_LABELS.distancia_evento_mt}
                  column="distancia_evento_mt"
                  sort={sort}
                  onSort={handleSort}
                  align="right"
                />
                <TableHead>Observación</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {eventsQ.data?.items.length === 0 && (
                <TableRow>
                  <TableCell colSpan={8} className="text-muted-foreground text-center">
                    Sin eventos para los filtros seleccionados.
                  </TableCell>
                </TableRow>
              )}
              {eventsQ.data?.items.map((row) => {
                const rpmAlert = getRpmAlert(row);
                const isSelected = selectedEventPoints.has(row.event_sk);
                return (
                  <TableRow
                    key={row.event_sk}
                    tabIndex={0}
                    aria-selected={isSelected}
                    onClick={(event) => selectEventOnMap(row, event)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        selectEventOnMap(row, event);
                      }
                    }}
                    className={
                      isSelected
                        ? 'bg-accent-blue/10 hover:bg-accent-blue/15 cursor-pointer'
                        : selectedPoints.length > 0
                          ? 'bg-muted/30 text-muted-foreground cursor-pointer opacity-50 hover:opacity-75'
                          : 'hover:bg-muted/50 cursor-pointer'
                    }
                  >
                    <TableCell className="whitespace-nowrap">
                      {row.fecha_y_hora_del_evento
                        ? new Date(row.fecha_y_hora_del_evento).toLocaleString('es-CO')
                        : (row.fecha ?? '—')}
                    </TableCell>
                    <TableCell className="font-medium">{row.placa ?? '—'}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        {row.event_type ? <Badge variant="outline">{row.event_type}</Badge> : '—'}
                        {rpmAlert && (
                          <span
                            className={
                              rpmAlert.severe
                                ? 'text-destructive inline-flex motion-safe:animate-pulse'
                                : 'text-destructive inline-flex'
                            }
                            title={rpmAlert.title}
                            aria-label={rpmAlert.title}
                          >
                            {rpmAlert.severe ? <EngineBrokenIcon /> : <EngineAlertIcon />}
                          </span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell
                      className="whitespace-nowrap text-right tabular-nums"
                      title={eventValueHint(row)}
                    >
                      {fmtEventValue(row.event_value, row.event_value_unit)}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-right tabular-nums">
                      {fmtKmh(row.velocidad_kmh)}
                    </TableCell>
                    <TableCell className="text-right">{fmtSeconds(row.duracion_evento)}</TableCell>
                    <TableCell className="text-right">{mtToKm(row.distancia_evento_mt)}</TableCell>
                    <TableCell className="text-muted-foreground max-w-xs truncate text-xs">
                      {row.observacion_corta ?? '—'}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>

          <UserPagination
            total={eventsQ.data?.total ?? 0}
            limit={PAGE_SIZE}
            offset={offset}
            onOffsetChange={setOffset}
          />
        </>
      ) : null}
    </>
  );
}
