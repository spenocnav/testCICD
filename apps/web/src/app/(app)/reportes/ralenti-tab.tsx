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
  Hourglass,
  Loader2,
  Search,
  Timer,
  X,
} from 'lucide-react';

import { ComposedChartCard } from '@/components/charts/composed-chart-card';
import { CHART_COLORS, CHART_TONES, SERIES_PALETTE } from '@/components/charts/chart-theme';
import { DonutChartCard } from '@/components/charts/donut-chart-card';
import type { RalentiHeatWeight } from '@/components/charts/ralenti-heat-map';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
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
import { extractErrorMessage } from '@/lib/api-client';
import {
  type CrossBucket,
  DEFAULT_RALENTI_EVENT_SORT,
  fetchRalentiEvents,
  RALENTI_DURATION_BUCKETS,
  RALENTI_RPM_BUCKETS,
  type RalentiEventSort,
  type RalentiEventSortKey,
  type RalentiFilters,
  ralentiBucketRange,
  ralentiDominantBucketLabel,
  ralentiEventBucket,
  ralentiEventSummary,
  ralentiHorasLabel,
  ralentiPctScale,
  type ReportesFilters,
  useRalentiEvents,
  useRalentiHeatmap,
  useRalentiPorPlaca,
  useRalentiSummary,
  useRalentiTimeseries,
} from '@/lib/reportes';
import type {
  Granularity,
  RalentiDurationKey,
  RalentiHeatCell,
  RalentiEvent,
  RalentiPlacaRow,
  RalentiRpmKey,
} from '@/lib/types';
import { cn } from '@/lib/utils';
import { buildXlsx, type XlsxCell, type XlsxSheet } from '@/lib/xlsx';

import { fmt, fmtCompact, fmtInt, KpiCard } from './shared';

const PAGE_SIZE = 50;

/** Filas visibles de la tabla por placa antes de pedir "Mostrar todas". */
const PLACA_ROWS_COLLAPSED = 12;

/** La grilla del mapa de calor es fija: 3 decimales ≈ 110 m por celda. */
const HEATMAP_PRECISION = 3;

// Carga diferida sin SSR: leaflet usa `window` y no debe renderizar en servidor.
const RalentiHeatMap = dynamic(
  () => import('@/components/charts/ralenti-heat-map').then((m) => m.RalentiHeatMap),
  {
    ssr: false,
    loading: () => <div className="bg-muted h-[516px] animate-pulse rounded-lg" />,
  },
);

/** Colores secuenciales de los cuatro rangos de duración: de corto a largo. */
const DURATION_COLORS: string[] = [
  CHART_COLORS.blue,
  CHART_TONES.blueMuted,
  CHART_TONES.yellowDark,
  CHART_TONES.redMuted,
];

/** Severidad visual del rango dominante: más tiempo por episodio, más alerta. */
const DURATION_BADGE_VARIANT: Record<
  RalentiDurationKey,
  'success' | 'info' | 'warning' | 'destructive'
> = {
  lt1: 'success',
  '1_5': 'info',
  '5_10': 'warning',
  gt10: 'destructive',
};

function durationLabel(key: string | null | undefined): string {
  if (!key) return '—';
  return RALENTI_DURATION_BUCKETS.find((b) => b.key === key)?.label ?? key;
}

function rpmLabel(key: string | null | undefined): string {
  if (!key) return '—';
  return RALENTI_RPM_BUCKETS.find((b) => b.key === key)?.label ?? key;
}

function fmtMinutos(v: number | null | undefined): string {
  if (v == null) return '—';
  return `${fmt(v, 1)} min`;
}

function fmtRpm(v: number | null | undefined): string {
  if (v == null) return '—';
  return `${fmtInt(v)} RPM`;
}

function fmtCoords(lat: number | null, lon: number | null): string {
  if (lat == null || lon == null) return '—';
  return `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
}

/** Chip de un cross-filter local; la X lo limpia. */
function FilterChip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <span className="bg-accent-blue/10 text-accent-blue inline-flex items-center gap-1 rounded-full px-3 py-1 text-xs font-semibold">
      {label}
      <button
        type="button"
        onClick={onClear}
        aria-label={`Quitar filtro ${label}`}
        className="hover:bg-accent-blue/15 -mr-1 rounded-full p-0.5 transition"
      >
        <X className="h-3 w-3" aria-hidden />
      </button>
    </span>
  );
}

/** Encabezado ordenable genérico: primer clic descendente, el segundo invierte. */
function SortableHead<K extends string>({
  label,
  column,
  sort,
  onSort,
  align = 'left',
}: {
  label: string;
  column: K;
  sort: { key: K; dir: 'asc' | 'desc' };
  onSort: (column: K) => void;
  align?: 'left' | 'right';
}) {
  const active = sort.key === column;
  return (
    <TableHead
      className={align === 'right' ? 'text-right' : undefined}
      aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
    >
      <button
        type="button"
        className={`hover:text-foreground inline-flex items-center gap-1 whitespace-nowrap font-semibold ${
          align === 'right' ? 'flex-row-reverse' : ''
        }`}
        onClick={() => onSort(column)}
        aria-label={`Ordenar por ${label}`}
      >
        {active ? (
          sort.dir === 'asc' ? (
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

/** Nombre visible de cada orden del detalle; se reutiliza en encabezado y Excel. */
const EVENT_SORT_LABELS: Record<RalentiEventSortKey, string> = {
  inicio: 'Inicio',
  duracion_segundos: 'Duración',
  rpm_promedio: 'RPM prom.',
};

type PlacaSortKey =
  | 'placa'
  | 'eventos_lt1'
  | 'eventos_1_5'
  | 'eventos_5_10'
  | 'eventos_gt10'
  | 'total_eventos'
  | 'total_minutos'
  | 'rango_dominante';

interface PlacaSort {
  key: PlacaSortKey;
  dir: 'asc' | 'desc';
}

/** Orden de los rangos para que "Rango con más tiempo" ordene de corto a largo. */
const DURATION_RANK: Record<RalentiDurationKey, number> = { lt1: 0, '1_5': 1, '5_10': 2, gt10: 3 };

function comparePlacas(a: RalentiPlacaRow, b: RalentiPlacaRow, sort: PlacaSort): number {
  const dir = sort.dir === 'asc' ? 1 : -1;
  if (sort.key === 'placa') {
    return dir * (a.placa ?? a.vehicle_id).localeCompare(b.placa ?? b.vehicle_id, 'es-CO');
  }
  if (sort.key === 'rango_dominante') {
    const ra = a.rango_dominante ? DURATION_RANK[a.rango_dominante] : -1;
    const rb = b.rango_dominante ? DURATION_RANK[b.rango_dominante] : -1;
    if (ra !== rb) return dir * (ra - rb);
  } else if (a[sort.key] !== b[sort.key]) {
    return dir * (a[sort.key] - b[sort.key]);
  }
  // Desempate estable: más eventos primero y después la placa.
  if (a.total_eventos !== b.total_eventos) return b.total_eventos - a.total_eventos;
  return (a.placa ?? a.vehicle_id).localeCompare(b.placa ?? b.vehicle_id, 'es-CO');
}

export function RalentiTab({
  filters,
  granularity,
  onSelectVehicle,
}: {
  filters: ReportesFilters;
  granularity: Granularity;
  /** Clic en una fila de la tabla por placa; recibe el vehículo y su etiqueta. */
  onSelectVehicle?: (vehicleId: string, label: string) => void;
}) {
  const [durationBucket, setDurationBucket] = React.useState<RalentiDurationKey | null>(null);
  const [rpmBucket, setRpmBucket] = React.useState<RalentiRpmKey | null>(null);
  const [selectedVehicle, setSelectedVehicle] = React.useState<{
    id: string;
    label: string;
  } | null>(null);
  // Bucket (día o mes) clicado en la tendencia; acota el resto de la pestaña.
  const [dateBucket, setDateBucket] = React.useState<CrossBucket | null>(null);
  // Episodio clicado en la tabla de detalle; acota mapa, KPI y tendencia a él.
  const [selectedEvent, setSelectedEvent] = React.useState<RalentiEvent | null>(null);
  const [heatWeight, setHeatWeight] = React.useState<RalentiHeatWeight>('minutos');
  const [placaSort, setPlacaSort] = React.useState<PlacaSort>({
    key: 'total_eventos',
    dir: 'desc',
  });
  const [placaSearch, setPlacaSearch] = React.useState('');
  const [showAllPlacas, setShowAllPlacas] = React.useState(false);
  const [offset, setOffset] = React.useState(0);
  const [sort, setSort] = React.useState<RalentiEventSort>(DEFAULT_RALENTI_EVENT_SORT);
  const [showDetail, setShowDetail] = React.useState(false);
  const [isExporting, setIsExporting] = React.useState(false);

  React.useEffect(
    () => setOffset(0),
    [filters, durationBucket, rpmBucket, selectedVehicle?.id, dateBucket?.label, sort],
  );
  // El episodio resaltado se suelta cuando cambia cualquier filtro por encima
  // de él: podría no pertenecer ya al conjunto que se está mirando.
  React.useEffect(
    () => setSelectedEvent(null),
    [filters, durationBucket, rpmBucket, selectedVehicle?.id, dateBucket?.label],
  );
  // Un vehículo resaltado deja de tener sentido si el alcance global cambia de
  // vehículos: podría haber quedado fuera de la lista.
  React.useEffect(() => setSelectedVehicle(null), [filters.vehicle_id]);
  // Y un bucket de fecha, si cambia el rango global o la granularidad: el
  // punto clicado ya no está en la serie.
  React.useEffect(() => setDateBucket(null), [filters.date_from, filters.date_to, granularity]);

  // El bucket clicado en la tendencia recorta el rango global para todo lo
  // demás. Sin bucket se reutiliza el objeto `filters` tal cual: es la clave
  // que precarga `use-tab-prefetch.ts` y ambas deben coincidir.
  const rangeFilters: ReportesFilters = dateBucket
    ? { ...filters, date_from: dateBucket.dateFrom, date_to: dateBucket.dateTo }
    : filters;

  // La tabla por placa no se acota al vehículo resaltado: desde ella se cambia o
  // se quita la selección. Resumen, mapa, serie y detalle sí lo siguen.
  const withVehicle = <T extends ReportesFilters>(base: T): T =>
    selectedVehicle ? { ...base, vehicle_id: [selectedVehicle.id] } : base;

  // El resumen recibe los filtros globales, el rango del bucket y la placa,
  // pero NUNCA los rangos de duración y RPM: sus gráficos son el origen de esos
  // dos cross-filters y deben conservar el contexto completo para poder cambiar
  // la selección. Sin nada cruzado es el objeto `filters` tal cual, que es la
  // clave que precarga `use-tab-prefetch.ts`.
  const summaryQ = useRalentiSummary(withVehicle(rangeFilters));

  const withCross = (base: ReportesFilters): RalentiFilters => ({
    ...base,
    duration_bucket: durationBucket ?? undefined,
    rpm_bucket: rpmBucket ?? undefined,
  });

  const crossFilters = withCross(rangeFilters);
  const scopedFilters = withVehicle(crossFilters);
  // La tendencia es el ORIGEN del bucket de fecha: conserva el rango global
  // completo y resalta el punto elegido, como las demás series del módulo.
  const seriesFilters = withVehicle(withCross(filters));

  const porPlacaQ = useRalentiPorPlaca(crossFilters);
  const heatQ = useRalentiHeatmap(scopedFilters, HEATMAP_PRECISION);
  const seriesQ = useRalentiTimeseries(seriesFilters, granularity);
  const eventsQ = useRalentiEvents(scopedFilters, { limit: PAGE_SIZE, offset, sort }, showDetail);

  // Con un episodio seleccionado los gráficos por rango lo describen a él; sus
  // cifras salen de la fila y no cuesta una consulta.
  const s = React.useMemo(
    () => (selectedEvent ? ralentiEventSummary(selectedEvent) : summaryQ.data),
    [selectedEvent, summaryQ.data],
  );
  const pctScale = React.useMemo(() => ralentiPctScale(s?.por_duracion ?? []), [s?.por_duracion]);
  const hasCross =
    durationBucket != null ||
    rpmBucket != null ||
    selectedVehicle != null ||
    dateBucket != null ||
    selectedEvent != null;

  // Con un cross-filter activo los KPI se derivan de la tabla por placa, que sí
  // recibe los rangos: sumar sus filas es exacto y no cuesta una consulta más.
  const placaRows = React.useMemo(() => porPlacaQ.data ?? [], [porPlacaQ.data]);
  const kpi = React.useMemo(() => {
    // Un episodio suelto no necesita consulta: sus cifras son las de su fila.
    if (selectedEvent) {
      return {
        eventos: 1,
        minutos: selectedEvent.duracion_min,
        promedio: selectedEvent.duracion_min,
        vehiculos: 1,
      };
    }
    if (!hasCross) {
      return s
        ? {
            eventos: s.total_eventos,
            minutos: s.total_minutos,
            promedio: s.duracion_promedio_min,
            vehiculos: s.vehiculos,
          }
        : null;
    }
    const rows = selectedVehicle
      ? placaRows.filter((r) => r.vehicle_id === selectedVehicle.id)
      : placaRows;
    if (porPlacaQ.data == null) return null;
    const eventos = rows.reduce((acc, r) => acc + r.total_eventos, 0);
    const minutos = rows.reduce((acc, r) => acc + r.total_minutos, 0);
    return {
      eventos,
      minutos,
      promedio: eventos > 0 ? minutos / eventos : null,
      vehiculos: rows.filter((r) => r.total_eventos > 0).length,
    };
  }, [hasCross, s, placaRows, porPlacaQ.data, selectedVehicle, selectedEvent]);

  // El mapa de un episodio suelto es su propia posición; no hay nada que pedir.
  const heatCells = React.useMemo<RalentiHeatCell[]>(() => {
    if (!selectedEvent) return heatQ.data ?? [];
    if (selectedEvent.latitud == null || selectedEvent.longitud == null) return [];
    return [
      {
        latitud: selectedEvent.latitud,
        longitud: selectedEvent.longitud,
        eventos: 1,
        minutos: selectedEvent.duracion_min,
      },
    ];
  }, [selectedEvent, heatQ.data]);
  // En la tendencia el episodio se marca en su día o mes; el bucket clicado a
  // mano gana si existe, porque es la selección explícita del usuario.
  const highlightedBucket =
    dateBucket?.label ??
    (selectedEvent ? ralentiEventBucket(selectedEvent.inicio, granularity) : null);

  const series = seriesQ.data ?? [];

  // Barras: % del tiempo total por rango; línea: duración promedio por rango.
  const durationChartData = React.useMemo(
    () =>
      (s?.por_duracion ?? []).map((b) => ({
        key: b.bucket,
        label: b.label,
        pct_minutos: b.pct_minutos * pctScale,
        duracion_promedio_min: b.duracion_promedio_min,
        eventos: b.eventos,
      })),
    [s?.por_duracion, pctScale],
  );
  const totalEventos = s?.total_eventos ?? 0;
  const durationDonutData = React.useMemo(
    () =>
      (s?.por_duracion ?? [])
        .filter((b) => b.eventos > 0)
        .map((b) => ({ label: b.label, value: b.eventos })),
    [s?.por_duracion],
  );
  const durationDonutColors = React.useMemo(
    () =>
      (s?.por_duracion ?? [])
        .filter((b) => b.eventos > 0)
        .map((b) => DURATION_COLORS[DURATION_RANK[b.bucket]] ?? CHART_COLORS.gray),
    [s?.por_duracion],
  );
  // `sin_rpm` (y cualquier rango vacío) no se pinta: un segmento de 0 no aporta
  // y `Sin RPM` sólo debe verse cuando de verdad hay episodios sin lectura.
  const rpmDonutData = React.useMemo(
    () =>
      (s?.por_rpm ?? [])
        .filter((b) => b.eventos > 0)
        .map((b) => ({ label: b.label, value: b.eventos })),
    [s?.por_rpm],
  );
  const rpmDonutColors = React.useMemo(
    () =>
      (s?.por_rpm ?? [])
        .filter((b) => b.eventos > 0)
        .map((b) =>
          b.bucket === 'sin_rpm'
            ? CHART_TONES.grayMuted
            : (SERIES_PALETTE[RALENTI_RPM_BUCKETS.findIndex((r) => r.key === b.bucket)] ??
              CHART_COLORS.blue),
        ),
    [s?.por_rpm],
  );

  const toggleDurationByLabel = React.useCallback((label: string) => {
    const key = RALENTI_DURATION_BUCKETS.find((b) => b.label === label)?.key;
    if (!key) return;
    setDurationBucket((current) => (current === key ? null : key));
  }, []);
  const toggleRpmByLabel = React.useCallback((label: string) => {
    const key = RALENTI_RPM_BUCKETS.find((b) => b.label === label)?.key;
    if (!key) return;
    setRpmBucket((current) => (current === key ? null : key));
  }, []);
  const toggleDateBucket = React.useCallback(
    (label: string) => {
      const next = ralentiBucketRange(label, filters);
      if (!next) return;
      setDateBucket((current) => (current?.label === next.label ? null : next));
    },
    [filters],
  );

  const handlePlacaSort = React.useCallback((column: PlacaSortKey) => {
    setPlacaSort((current) =>
      current.key === column
        ? { key: column, dir: current.dir === 'desc' ? 'asc' : 'desc' }
        : // La placa se lee de la A a la Z; las cifras, de mayor a menor.
          { key: column, dir: column === 'placa' ? 'asc' : 'desc' },
    );
  }, []);

  const normalizedPlacaSearch = placaSearch.trim().toLowerCase();
  const sortedPlacas = React.useMemo(() => {
    const filtered = normalizedPlacaSearch
      ? placaRows.filter((r) =>
          (r.placa ?? r.vehicle_id).toLowerCase().includes(normalizedPlacaSearch),
        )
      : placaRows;
    return [...filtered].sort((a, b) => comparePlacas(a, b, placaSort));
  }, [placaRows, normalizedPlacaSearch, placaSort]);
  const maxPlacaEventos = React.useMemo(
    () => placaRows.reduce((acc, r) => Math.max(acc, r.total_eventos), 0),
    [placaRows],
  );
  const visiblePlacas = showAllPlacas ? sortedPlacas : sortedPlacas.slice(0, PLACA_ROWS_COLLAPSED);

  const selectEvent = React.useCallback((row: RalentiEvent) => {
    setSelectedEvent((current) => (current?.event_sk === row.event_sk ? null : row));
  }, []);

  const selectPlaca = React.useCallback(
    (row: RalentiPlacaRow) => {
      const label = row.placa ?? row.vehicle_id;
      setSelectedVehicle((current) =>
        current?.id === row.vehicle_id ? null : { id: row.vehicle_id, label },
      );
      onSelectVehicle?.(row.vehicle_id, label);
    },
    [onSelectVehicle],
  );

  const handleEventSort = React.useCallback((column: RalentiEventSortKey) => {
    setSort((current) =>
      current.sort_by === column
        ? { sort_by: column, sort_dir: current.sort_dir === 'desc' ? 'asc' : 'desc' }
        : { sort_by: column, sort_dir: 'desc' },
    );
  }, []);
  const eventSort = { key: sort.sort_by, dir: sort.sort_dir };

  const exportExcel = async () => {
    setIsExporting(true);
    try {
      const rows = await fetchRalentiEvents(scopedFilters, sort);
      const toCells = (row: unknown[], style?: number): XlsxCell[] =>
        row.map((value) => ({ value: value as XlsxCell['value'], style }));
      // Estilos por índice de columna: 4 = dos decimales, 6 = entero con miles.
      const decimalColumns = new Set([3, 8, 9]);
      const integerColumns = new Set([4, 5, 6]);
      const detailRows: XlsxCell[][] = [
        toCells(
          [
            'Placa',
            'Inicio',
            'Fin',
            'Duración (min)',
            'Registros fuente',
            'RPM promedio',
            'RPM máximo',
            'Rango de duración',
            'Latitud',
            'Longitud',
            'Rango de RPM',
          ],
          3,
        ),
        ...rows.map((row) =>
          toCells([
            row.placa ?? '',
            new Date(row.inicio).toLocaleString('es-CO'),
            new Date(row.fin).toLocaleString('es-CO'),
            row.duracion_min,
            row.eventos_fuente,
            row.rpm_promedio,
            row.rpm_maximo,
            durationLabel(row.duration_bucket),
            row.latitud,
            row.longitud,
            rpmLabel(row.rpm_bucket),
          ]).map((cell, index) => ({
            ...cell,
            style: decimalColumns.has(index) ? 4 : integerColumns.has(index) ? 6 : cell.style,
          })),
        ),
      ];
      const withStyle = (cells: XlsxCell[], style: number): XlsxCell[] =>
        cells.map((cell, index) => ({ ...cell, style: index === 1 ? style : cell.style }));
      const summaryRows: XlsxCell[][] = [
        [{ value: 'Análisis Ralentí', style: 1 }],
        [{ value: 'Filtros aplicados', style: 2 }],
        toCells(['Desde', scopedFilters.date_from ?? 'Todos']),
        toCells(['Hasta', scopedFilters.date_to ?? 'Todos']),
        toCells(['Rango de duración', durationBucket ? durationLabel(durationBucket) : 'Todos']),
        toCells(['Rango de RPM', rpmBucket ? rpmLabel(rpmBucket) : 'Todos']),
        toCells(['Vehículo', selectedVehicle?.label ?? 'Todos']),
        toCells([
          'Orden aplicado',
          `${EVENT_SORT_LABELS[sort.sort_by]}, ${sort.sort_dir === 'asc' ? 'ascendente' : 'descendente'}`,
        ]),
        [{ value: 'Indicadores', style: 2 }],
        withStyle(toCells(['Episodios', kpi?.eventos ?? null]), 6),
        withStyle(toCells(['Duración total (min)', kpi?.minutos ?? null]), 4),
        withStyle(toCells(['Duración promedio (min)', kpi?.promedio ?? null]), 4),
        withStyle(toCells(['Vehículos con ralentí', kpi?.vehiculos ?? null]), 6),
      ];
      const workbook = buildXlsx([
        { name: 'Resumen', rows: summaryRows },
        { name: 'Detalle Ralentí', rows: detailRows },
      ] satisfies XlsxSheet[]);
      const url = URL.createObjectURL(workbook);
      const link = document.createElement('a');
      link.href = url;
      link.download = `detalle_ralenti_${scopedFilters.date_from ?? 'periodo'}.xlsx`;
      link.click();
      URL.revokeObjectURL(url);
      toast.success(`${rows.length} episodios exportados.`);
    } catch (err) {
      toast.error(extractErrorMessage(err, 'No se pudieron exportar los datos'));
    } finally {
      setIsExporting(false);
    }
  };

  const clearCross = () => {
    setDurationBucket(null);
    setRpmBucket(null);
    setSelectedVehicle(null);
    setDateBucket(null);
    setSelectedEvent(null);
  };
  const selectedEventLabel = selectedEvent
    ? `Episodio: ${selectedEvent.placa ?? selectedEvent.vehicle_id} · ${new Date(
        selectedEvent.inicio,
      ).toLocaleString('es-CO', { dateStyle: 'short', timeStyle: 'short' })}`
    : null;
  const dateBucketLabel = dateBucket
    ? `${granularity === 'daily' ? 'Día' : 'Mes'}: ${dateBucket.label}`
    : null;

  const selectedDurationLabel = durationBucket ? durationLabel(durationBucket) : null;
  const selectedRpmLabel = rpmBucket ? rpmLabel(rpmBucket) : null;
  const isEmptyRange = s != null && s.total_eventos === 0;
  const seriesLabelsVisible = series.length <= 12;

  return (
    <>
      {/* Cross-filters locales (clic en gráficos y en la tabla por placa) */}
      {hasCross && (
        <div
          role="status"
          className="border-accent-blue/30 bg-accent-blue/5 text-accent-blue mb-4 flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-xs"
        >
          <span className="font-semibold">Filtros de gráficos:</span>
          {selectedDurationLabel && (
            <FilterChip
              label={`Duración: ${selectedDurationLabel}`}
              onClear={() => setDurationBucket(null)}
            />
          )}
          {selectedRpmLabel && (
            <FilterChip label={`RPM: ${selectedRpmLabel}`} onClear={() => setRpmBucket(null)} />
          )}
          {selectedVehicle && (
            <FilterChip
              label={`Placa: ${selectedVehicle.label}`}
              onClear={() => setSelectedVehicle(null)}
            />
          )}
          {dateBucketLabel && (
            <FilterChip label={dateBucketLabel} onClear={() => setDateBucket(null)} />
          )}
          {selectedEventLabel && (
            <FilterChip label={selectedEventLabel} onClear={() => setSelectedEvent(null)} />
          )}
          <button
            type="button"
            onClick={clearCross}
            className="ml-auto font-semibold underline-offset-2 hover:underline"
          >
            Limpiar
          </button>
        </div>
      )}

      {summaryQ.isError && (
        <div
          role="alert"
          className="border-destructive/30 bg-destructive/5 text-destructive mb-4 rounded-md border px-3 py-2 text-sm"
        >
          {extractErrorMessage(summaryQ.error, 'No se pudo cargar el análisis de ralentí.')}
        </div>
      )}

      {/* KPI cards */}
      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
        <KpiCard
          label="Duración total"
          value={kpi ? `${fmtCompact(kpi.minutos)} min` : '—'}
          exact={kpi ? `${fmtInt(kpi.minutos)} min` : undefined}
          hint={kpi ? `${ralentiHorasLabel(kpi.minutos)} en ralentí` : 'en ralentí'}
          icon={<Hourglass size={32} />}
          index={0}
        />
        <KpiCard
          label="Eventos"
          value={kpi ? fmtCompact(kpi.eventos) : '—'}
          exact={kpi ? fmtInt(kpi.eventos) : undefined}
          hint="episodios de ralentí"
          trend={series.map((p) => p.eventos)}
          index={1}
        />
        <KpiCard
          label="Duración prom."
          value={kpi?.promedio != null ? `${fmt(kpi.promedio, 1)} min` : '—'}
          hint="por episodio"
          icon={<Timer size={32} />}
          index={2}
        />
        <KpiCard
          label="Vehículos"
          value={kpi ? fmtInt(kpi.vehiculos) : '—'}
          hint="con ralentí en el periodo"
          icon={<CarFront size={32} />}
          index={3}
        />
      </div>

      {isEmptyRange && !hasCross ? (
        <div className="bg-card text-muted-foreground mb-6 flex flex-col items-center gap-2 rounded-lg border px-6 py-12 text-center text-sm">
          <Clock className="text-muted-foreground/50 h-8 w-8" aria-hidden />
          <p className="text-foreground font-semibold">Sin episodios de ralentí en el rango</p>
          <p className="max-w-md">
            La flota tiene el Análisis Ralentí activo, pero no se registraron episodios entre las
            fechas seleccionadas. Amplía el rango o revisa los filtros de vehículo y motor.
          </p>
        </div>
      ) : (
        <>
          {/* Duración por rango: % del tiempo (barras) + promedio por episodio (línea) */}
          <div className="mb-6 grid grid-cols-1 gap-4 xl:grid-cols-3">
            <div className="xl:col-span-1">
              <ComposedChartCard
                title="Duración (min) por rango"
                subtitle="Porcentaje del tiempo total en ralentí por rango de duración · clic para filtrar"
                data={durationChartData}
                xKey="label"
                height={300}
                isLoading={summaryQ.isLoading}
                isError={summaryQ.isError}
                bars={[
                  {
                    key: 'pct_minutos',
                    name: '% del tiempo',
                    color: CHART_COLORS.blue,
                    format: (v) => `${fmt(v, 0)} %`,
                  },
                ]}
                lines={[
                  {
                    key: 'duracion_promedio_min',
                    name: 'Duración prom. (min)',
                    color: CHART_TONES.yellowDark,
                    axis: 'right',
                    format: (v) => `${fmt(v, 1)} min`,
                  },
                ]}
                selectedBucket={selectedDurationLabel}
                onBucketClick={toggleDurationByLabel}
              />
            </div>
            <DonutChartCard
              title="Eventos por rango de duración"
              subtitle="Cuántos episodios cae en cada rango · clic para filtrar"
              data={durationDonutData}
              colors={durationDonutColors}
              height={300}
              isLoading={summaryQ.isLoading}
              format={(v) =>
                totalEventos > 0
                  ? `${fmtInt(v)} · ${fmt((v / totalEventos) * 100, 1)} %`
                  : fmtInt(v)
              }
              selectedLabel={selectedDurationLabel}
              onSegmentClick={toggleDurationByLabel}
            />
            <DonutChartCard
              title="Eventos por rango de RPM"
              subtitle="RPM promedio del episodio · clic para filtrar"
              data={rpmDonutData}
              colors={rpmDonutColors}
              height={300}
              isLoading={summaryQ.isLoading}
              format={(v) =>
                totalEventos > 0
                  ? `${fmtInt(v)} · ${fmt((v / totalEventos) * 100, 1)} %`
                  : fmtInt(v)
              }
              selectedLabel={selectedRpmLabel}
              onSegmentClick={toggleRpmByLabel}
            />
          </div>

          {/* Tabla por placa */}
          <div className="bg-card mb-6 rounded-lg border p-4">
            <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="font-heading text-sm font-bold tracking-tight">Ralentí por placa</h3>
                <p className="text-muted-foreground text-xs">
                  Episodios por rango de duración y el rango que acumula más tiempo · clic en una
                  fila para acotar mapa, tendencia y detalle
                </p>
              </div>
              <div className="relative">
                <Search
                  className="text-muted-foreground pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2"
                  aria-hidden
                />
                <Input
                  value={placaSearch}
                  onChange={(event) => setPlacaSearch(event.target.value)}
                  placeholder="Buscar placa"
                  className="h-8 w-44 pl-8 text-xs"
                  aria-label="Buscar placa"
                />
              </div>
            </div>
            {porPlacaQ.isLoading ? (
              <div className="space-y-2">
                {Array.from({ length: 6 }).map((_, i) => (
                  <Skeleton key={i} className="h-8 w-full" />
                ))}
              </div>
            ) : porPlacaQ.isError ? (
              <p className="text-destructive text-sm">
                No se pudo cargar la tabla por placa. Intenta de nuevo.
              </p>
            ) : (
              <>
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <SortableHead
                          label="Placa"
                          column="placa"
                          sort={placaSort}
                          onSort={handlePlacaSort}
                        />
                        {RALENTI_DURATION_BUCKETS.map((b) => (
                          <SortableHead
                            key={b.key}
                            label={b.label}
                            column={`eventos_${b.key}` as PlacaSortKey}
                            sort={placaSort}
                            onSort={handlePlacaSort}
                            align="right"
                          />
                        ))}
                        <SortableHead
                          label="Total eventos"
                          column="total_eventos"
                          sort={placaSort}
                          onSort={handlePlacaSort}
                          align="right"
                        />
                        <SortableHead
                          label="Minutos"
                          column="total_minutos"
                          sort={placaSort}
                          onSort={handlePlacaSort}
                          align="right"
                        />
                        <SortableHead
                          label="Rango con más tiempo"
                          column="rango_dominante"
                          sort={placaSort}
                          onSort={handlePlacaSort}
                        />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {sortedPlacas.length === 0 && (
                        <TableRow>
                          <TableCell colSpan={8} className="text-muted-foreground text-center">
                            {normalizedPlacaSearch
                              ? 'Ninguna placa coincide con la búsqueda.'
                              : 'Sin placas con ralentí para los filtros seleccionados.'}
                          </TableCell>
                        </TableRow>
                      )}
                      {visiblePlacas.map((row) => {
                        const isSelected = selectedVehicle?.id === row.vehicle_id;
                        const barPct =
                          maxPlacaEventos > 0 ? (row.total_eventos / maxPlacaEventos) * 100 : 0;
                        const dominant = ralentiDominantBucketLabel(row);
                        return (
                          <TableRow
                            key={row.vehicle_id}
                            tabIndex={0}
                            aria-selected={isSelected}
                            onClick={() => selectPlaca(row)}
                            onKeyDown={(event) => {
                              if (event.key === 'Enter' || event.key === ' ') {
                                event.preventDefault();
                                selectPlaca(row);
                              }
                            }}
                            className={cn(
                              'cursor-pointer',
                              isSelected
                                ? 'bg-accent-blue/10 hover:bg-accent-blue/15'
                                : selectedVehicle
                                  ? 'text-muted-foreground opacity-60 hover:opacity-90'
                                  : 'hover:bg-muted/50',
                            )}
                          >
                            <TableCell className="font-mono font-semibold">
                              {row.placa ?? row.vehicle_id}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {fmtInt(row.eventos_lt1)}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {fmtInt(row.eventos_1_5)}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {fmtInt(row.eventos_5_10)}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {fmtInt(row.eventos_gt10)}
                            </TableCell>
                            <TableCell className="text-right">
                              <div className="flex items-center justify-end gap-2">
                                <span
                                  className="bg-accent-blue/20 inline-block h-2 rounded-full"
                                  style={{ width: `${Math.max(2, barPct) * 0.8}px` }}
                                  aria-hidden
                                />
                                <span className="font-semibold tabular-nums">
                                  {fmtInt(row.total_eventos)}
                                </span>
                              </div>
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {fmt(row.total_minutos, 1)}
                            </TableCell>
                            <TableCell>
                              {row.rango_dominante && dominant ? (
                                <Badge variant={DURATION_BADGE_VARIANT[row.rango_dominante]}>
                                  {dominant}
                                </Badge>
                              ) : (
                                '—'
                              )}
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
                {sortedPlacas.length > PLACA_ROWS_COLLAPSED && (
                  <div className="mt-3 flex justify-center">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => setShowAllPlacas((visible) => !visible)}
                      aria-expanded={showAllPlacas}
                    >
                      {showAllPlacas
                        ? 'Mostrar menos'
                        : `Mostrar todas (${fmtInt(sortedPlacas.length)} placas)`}
                    </Button>
                  </div>
                )}
              </>
            )}
          </div>

          {/* Mapa de calor */}
          <div className="mb-6">
            <RalentiHeatMap
              title="Mapa de calor de ralentí"
              subtitle={
                selectedEvent
                  ? selectedEvent.latitud == null
                    ? 'El episodio seleccionado no tiene posición registrada'
                    : `Posición del episodio seleccionado · ${fmtMinutos(selectedEvent.duracion_min)}`
                  : `${fmtInt(heatQ.data?.length ?? 0)} zonas con episodios${
                      selectedVehicle ? ` · ${selectedVehicle.label}` : ''
                    }`
              }
              cells={heatCells}
              weight={heatWeight}
              onWeightChange={setHeatWeight}
              isLoading={heatQ.isLoading}
              isError={heatQ.isError}
            />
          </div>

          {/* Tendencia */}
          <div className="mb-6">
            <ComposedChartCard
              title="Tendencia del ralentí"
              subtitle={`Minutos y episodios por ${
                granularity === 'daily' ? 'día' : 'mes'
              } · clic en un punto para acotar el resto de la pestaña`}
              data={series}
              xKey="bucket"
              isLoading={seriesQ.isLoading}
              isError={seriesQ.isError}
              showValueLabels={seriesLabelsVisible}
              onBucketClick={toggleDateBucket}
              selectedBucket={highlightedBucket}
              bars={[
                {
                  key: 'minutos',
                  name: 'Minutos en ralentí',
                  color: CHART_TONES.blueMuted,
                  format: (v) => `${fmt(v, 0)} min`,
                },
              ]}
              lines={[
                {
                  key: 'eventos',
                  name: 'Episodios',
                  color: CHART_TONES.redMuted,
                  axis: 'right',
                  format: (v) => fmtInt(v),
                },
              ]}
            />
          </div>
        </>
      )}

      {/* Detalle de episodios */}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-heading text-sm font-bold tracking-tight">Detalle de episodios</h2>
          {selectedDurationLabel && <Badge variant="info">Duración: {selectedDurationLabel}</Badge>}
          {selectedRpmLabel && <Badge variant="info">RPM: {selectedRpmLabel}</Badge>}
          {selectedVehicle && <Badge variant="info">Placa: {selectedVehicle.label}</Badge>}
          {dateBucketLabel && <Badge variant="info">{dateBucketLabel}</Badge>}
          {selectedEventLabel && <Badge variant="info">{selectedEventLabel}</Badge>}
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
              {isExporting ? 'Exportando…' : 'Descargar Excel'}
            </Button>
          )}
        </div>
      </div>
      {showDetail && eventsQ.isError && (
        <p className="text-destructive text-sm">Error al cargar datos. Intenta de nuevo.</p>
      )}

      {showDetail && eventsQ.isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </div>
      ) : showDetail ? (
        <>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Placa</TableHead>
                  <SortableHead
                    label={EVENT_SORT_LABELS.inicio}
                    column="inicio"
                    sort={eventSort}
                    onSort={handleEventSort}
                  />
                  <SortableHead
                    label={EVENT_SORT_LABELS.duracion_segundos}
                    column="duracion_segundos"
                    sort={eventSort}
                    onSort={handleEventSort}
                    align="right"
                  />
                  <SortableHead
                    label={EVENT_SORT_LABELS.rpm_promedio}
                    column="rpm_promedio"
                    sort={eventSort}
                    onSort={handleEventSort}
                    align="right"
                  />
                  <TableHead className="text-right">RPM máx.</TableHead>
                  <TableHead>Rango</TableHead>
                  <TableHead>Ubicación</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {eventsQ.data?.items.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={7} className="text-muted-foreground text-center">
                      Sin episodios para los filtros seleccionados.
                    </TableCell>
                  </TableRow>
                )}
                {eventsQ.data?.items.map((row: RalentiEvent) => {
                  const isSelected = selectedEvent?.event_sk === row.event_sk;
                  return (
                    <TableRow
                      key={row.event_sk}
                      tabIndex={0}
                      aria-selected={isSelected}
                      onClick={() => selectEvent(row)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault();
                          selectEvent(row);
                        }
                      }}
                      className={cn(
                        'cursor-pointer',
                        isSelected
                          ? 'bg-accent-blue/10 hover:bg-accent-blue/15'
                          : selectedEvent
                            ? 'text-muted-foreground opacity-60 hover:opacity-90'
                            : 'hover:bg-muted/50',
                      )}
                    >
                      <TableCell className="font-mono font-semibold">{row.placa ?? '—'}</TableCell>
                      <TableCell className="whitespace-nowrap">
                        {new Date(row.inicio).toLocaleString('es-CO')}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-right tabular-nums">
                        {fmtMinutos(row.duracion_min)}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-right tabular-nums">
                        {fmtRpm(row.rpm_promedio)}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-right tabular-nums">
                        {fmtRpm(row.rpm_maximo)}
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap items-center gap-1">
                          <Badge
                            variant={
                              DURATION_BADGE_VARIANT[row.duration_bucket as RalentiDurationKey] ??
                              'outline'
                            }
                          >
                            {durationLabel(row.duration_bucket)}
                          </Badge>
                          <Badge variant="outline">{rpmLabel(row.rpm_bucket)}</Badge>
                        </div>
                      </TableCell>
                      <TableCell className="text-muted-foreground whitespace-nowrap font-mono text-xs">
                        {fmtCoords(row.latitud, row.longitud)}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>

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
