'use client';

import * as React from 'react';
import {
  AlertTriangle,
  CarFront,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Clock,
  Download,
  Loader2,
  Search,
  Wrench,
} from 'lucide-react';
import { toast } from 'sonner';

import { BarChartCard } from '@/components/charts/bar-chart-card';
import {
  AXIS_COLOR,
  CHART_ANIMATION_DURATION,
  CHART_ANIMATION_EASING,
  CHART_COLORS,
  CHART_TONES,
  GRID_COLOR,
  tooltipStyle,
} from '@/components/charts/chart-theme';
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
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
import { useGroupFilterAvailable } from '@/components/vehicles/group-filter';
import {
  GroupComparisonCard,
  type GroupComparisonRow,
} from '@/components/vehicles/group-comparison-card';
import { extractErrorMessage } from '@/lib/api-client';
import {
  FALLAS_REPORT_SCOPE,
  type CrossBucket,
  type FaultFilters,
  type ReportesFilters,
  fetchFaultEvents,
  useFallasPorGrupo,
  useFaultBySeverity,
  useFaultEvents,
  useFaultPareto,
  useFaultRanking,
  useFaultSummary,
  useFaultTimeseries,
} from '@/lib/reportes';
import type { FaultDimension, Granularity } from '@/lib/types';
import { cn } from '@/lib/utils';
import { buildXlsx, type XlsxCell, type XlsxSheet } from '@/lib/xlsx';

import { fmtCompact, fmtInt, KpiCard } from './shared';

const DIMENSIONS: { value: FaultDimension; label: string }[] = [
  { value: 'diagnostico', label: 'Diagnóstico' },
  { value: 'controlador', label: 'Controlador' },
  { value: 'modo_falla', label: 'Modo de falla' },
];

/** Color de marca por nivel de atención. */
function severityColor(sev: string): string {
  const s = sev.toLowerCase();
  if (s.includes('urgente') || s.includes('nivel 1')) return CHART_COLORS.red;
  if (s.includes('prioritaria') || s.includes('nivel 2')) return CHART_TONES.yellowDark;
  if (s.includes('pronta') || s.includes('nivel 3')) return CHART_COLORS.blue;
  return CHART_TONES.grayMuted;
}

function severityBadge(sev: string | null): React.ReactNode {
  if (!sev) return '—';
  const s = sev.toLowerCase();
  if (s.includes('urgente') || s.includes('nivel 1'))
    return <Badge variant="destructive">{sev}</Badge>;
  if (s.includes('prioritaria') || s.includes('nivel 2'))
    return <Badge variant="warning">{sev}</Badge>;
  if (s.includes('pronta') || s.includes('nivel 3')) return <Badge variant="info">{sev}</Badge>;
  return <Badge variant="outline">{sev}</Badge>;
}

function formatDateTime(isoString: string | null | undefined): string {
  if (!isoString) return '—';
  try {
    const d = new Date(isoString);
    if (Number.isNaN(d.getTime())) return isoString;
    return new Intl.DateTimeFormat('es-CO', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(d);
  } catch {
    return isoString;
  }
}

/** Paginador enriquecido con salto directo a página y selector de filas */
function HistoryPagination({
  total,
  limit,
  offset,
  onOffsetChange,
  onLimitChange,
}: {
  total: number;
  limit: number;
  offset: number;
  onOffsetChange: (newOffset: number) => void;
  onLimitChange?: (newLimit: number) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(total / limit));
  const currentPage = Math.floor(offset / limit) + 1;
  const [inputPage, setInputPage] = React.useState(String(currentPage));

  React.useEffect(() => {
    setInputPage(String(currentPage));
  }, [currentPage]);

  const goToPage = (p: number) => {
    const validPage = Math.max(1, Math.min(p, totalPages));
    onOffsetChange((validPage - 1) * limit);
  };

  const handleInputSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const p = parseInt(inputPage, 10);
    if (!isNaN(p)) {
      goToPage(p);
    } else {
      setInputPage(String(currentPage));
    }
  };

  const fromRecord = total === 0 ? 0 : offset + 1;
  const toRecord = Math.min(offset + limit, total);

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 pt-2 text-xs">
      <div className="text-muted-foreground flex items-center gap-3">
        <span>
          Mostrando <span className="font-semibold text-foreground font-mono">{fromRecord}-{toRecord}</span> de{' '}
          <span className="font-semibold text-foreground font-mono">{total.toLocaleString('es-CO')}</span> eventos
        </span>

        {onLimitChange && (
          <div className="flex items-center gap-1.5 border-l border-border/60 pl-3">
            <span className="text-muted-foreground text-[11px]">Filas:</span>
            <select
              value={limit}
              onChange={(e) => {
                onLimitChange(Number(e.target.value));
                onOffsetChange(0);
              }}
              className="h-7 rounded border border-border/60 bg-background px-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary cursor-pointer font-mono"
            >
              <option value={10}>10</option>
              <option value={25}>25</option>
              <option value={50}>50</option>
            </select>
          </div>
        )}
      </div>

      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={currentPage <= 1}
          onClick={() => goToPage(currentPage - 1)}
          className="h-8 px-2.5 text-xs cursor-pointer"
        >
          <ChevronLeft className="h-3.5 w-3.5 mr-1" />
          Anterior
        </Button>

        <form onSubmit={handleInputSubmit} className="flex items-center gap-1.5">
          <span className="text-muted-foreground">Pág.</span>
          <Input
            type="number"
            min={1}
            max={totalPages}
            value={inputPage}
            onChange={(e) => setInputPage(e.target.value)}
            onBlur={() => {
              const p = parseInt(inputPage, 10);
              if (!isNaN(p)) goToPage(p);
              else setInputPage(String(currentPage));
            }}
            className="h-8 w-14 px-1.5 text-center font-mono text-xs"
          />
          <span className="text-muted-foreground">de {totalPages}</span>
        </form>

        <Button
          variant="outline"
          size="sm"
          disabled={currentPage >= totalPages}
          onClick={() => goToPage(currentPage + 1)}
          className="h-8 px-2.5 text-xs cursor-pointer"
        >
          Siguiente
          <ChevronRight className="h-3.5 w-3.5 ml-1" />
        </Button>
      </div>
    </div>
  );
}

export function FallasTab({
  filters,
  granularity,
  onSelectGroup,
}: {
  filters: ReportesFilters;
  granularity: Granularity;
  /** Clic en una barra del comparativo por grupo → aplicar ese grupo al filtro global. */
  onSelectGroup?: (groupId: string) => void;
}) {
  const [severity, setSeverity] = React.useState('');
  const [dimension, setDimension] = React.useState<FaultDimension>('diagnostico');
  const [paretoFilter, setParetoFilter] = React.useState<{
    dimension: FaultDimension;
    value: string;
  } | null>(null);
  const [vehicleFilter, setVehicleFilter] = React.useState<{
    id: string;
    label: string;
  } | null>(null);
  const [periodFilter, setPeriodFilter] = React.useState<CrossBucket | null>(null);
  const [onlyUrgent, setOnlyUrgent] = React.useState(false);
  const [pageSize, setPageSize] = React.useState(10);
  const [offset, setOffset] = React.useState(0);
  const [searchQuery, setSearchQuery] = React.useState('');
  const [isHistoryExpanded, setIsHistoryExpanded] = React.useState(false);
  const [isExporting, setIsExporting] = React.useState(false);

  React.useEffect(() => setOffset(0), [
    filters,
    severity,
    paretoFilter,
    vehicleFilter?.id,
    periodFilter,
    onlyUrgent,
    pageSize,
  ]);
  React.useEffect(
    () => setPeriodFilter(null),
    [granularity, filters.date_from, filters.date_to],
  );

  const dateScopedFilters: ReportesFilters = periodFilter
    ? {
        ...filters,
        date_from: periodFilter.dateFrom,
        date_to: periodFilter.dateTo,
      }
    : filters;
  const scopedVehicleIds = vehicleFilter ? [vehicleFilter.id] : filters.vehicle_id;
  const categoryFields = paretoFilter
    ? {
        fault_dimension: paretoFilter.dimension,
        fault_value: paretoFilter.value,
      }
    : {};
  // Este tab muestra el histórico DE FALLAS DEL VEHÍCULO, así que las fallas del
  // dispositivo telemático son ruido y se excluyen. El flag es explícito de esta
  // pantalla y NO un default del backend: los endpoints `/reportes/fallas/*` los
  // comparten dos pantallas con propósitos distintos y Navifault trabaja sobre
  // todo lo que llega. Ponerlo por defecto le borraba a Navifault el 15,4 % de su
  // ventana de 24 h en silencio. Va en los seis objetos de filtros porque cada
  // uno alimenta una consulta distinta y todas deben ver el mismo universo.
  const telematicsScope = FALLAS_REPORT_SCOPE;
  const faultFilters: FaultFilters = {
    ...dateScopedFilters,
    vehicle_id: scopedVehicleIds,
    severity: severity || undefined,
    ...categoryFields,
    ...telematicsScope,
  };
  const severityFilters: FaultFilters = {
    ...dateScopedFilters,
    vehicle_id: scopedVehicleIds,
    ...categoryFields,
    ...telematicsScope,
  };
  const paretoFilters: FaultFilters = {
    ...dateScopedFilters,
    vehicle_id: scopedVehicleIds,
    severity: severity || undefined,
    ...telematicsScope,
  };
  const seriesFilters: FaultFilters = {
    ...filters,
    vehicle_id: scopedVehicleIds,
    severity: severity || undefined,
    ...categoryFields,
    ...telematicsScope,
  };
  const rankingFilters: FaultFilters = {
    ...dateScopedFilters,
    severity: severity || undefined,
    ...categoryFields,
    ...telematicsScope,
  };
  // Comparativo por grupo interno: mismo universo que el resto del tab (con el
  // scope de telemáticas); el endpoint no acepta severidad ni categoría.
  const groupFilters: FaultFilters = {
    ...dateScopedFilters,
    vehicle_id: scopedVehicleIds,
    ...telematicsScope,
  };

  const summaryQ = useFaultSummary(faultFilters);
  const bySeverityQ = useFaultBySeverity(severityFilters);
  const paretoQ = useFaultPareto(paretoFilters, dimension);
  const seriesQ = useFaultTimeseries(seriesFilters, granularity);
  const rankingQ = useFaultRanking(rankingFilters);
  const eventsQ = useFaultEvents(faultFilters, onlyUrgent, pageSize, offset, isHistoryExpanded);
  // Solo se consulta cuando el comparativo aplica (una flota y con grupos);
  // queda fuera de la precarga por pestaña a propósito.
  const groupsAvailable = useGroupFilterAvailable();
  const porGrupoQ = useFallasPorGrupo(groupFilters, groupsAvailable);
  const groupRows: GroupComparisonRow[] = React.useMemo(
    () =>
      (porGrupoQ.data ?? []).map((b) => ({
        groupId: b.group_id,
        values: {
          n_fallas: b.n_fallas,
          n_eventos: b.n_eventos,
          n_urgentes: b.n_urgentes,
          n_vehiculos: b.n_vehiculos,
        },
      })),
    [porGrupoQ.data],
  );

  const s = summaryQ.data;
  const bySeverity = bySeverityQ.data ?? [];

  const handleExport = async () => {
    setIsExporting(true);
    try {
      const items = await fetchFaultEvents(faultFilters, onlyUrgent);

      if (items.length === 0) {
        toast.info('No hay eventos para exportar con los filtros actuales.');
        return;
      }

      // Hoja 1: Resumen Ejecutivo
      const toCells = (row: unknown[], style?: number): XlsxCell[] =>
        row.map((value) => ({ value: value as XlsxCell['value'], style }));

      const summaryRows: XlsxCell[][] = [
        [{ value: 'Reporte de Fallas y Alertas Técnicas', style: 1 }],
        [{ value: 'Filtros aplicados', style: 2 }],
        toCells(['Desde', faultFilters.date_from ?? 'Todos']),
        toCells(['Hasta', faultFilters.date_to ?? 'Todos']),
        toCells(['Severidad', faultFilters.severity ?? 'Todas']),
        toCells(['Solo urgentes', onlyUrgent ? 'SÍ' : 'NO']),
        toCells([
          'Vehículos filtrados',
          faultFilters.vehicle_id?.length ? faultFilters.vehicle_id.join(', ') : 'Todos',
        ]),
        [{ value: 'Indicadores generales', style: 2 }],
        toCells(['Total fallas / eventos', s?.n_fallas ?? items.length]),
        toCells(['Vehículos afectados', s?.n_vehiculos ?? 0]),
        toCells(['Diagnósticos distintos', s?.n_diagnosticos ?? 0]),
        toCells(['Fallas urgentes (Luz roja)', s?.n_urgentes ?? 0]),
      ];

      // Hoja 2: Detalle Completo de Eventos
      const headerRow: XlsxCell[] = [
        { value: 'Fecha y Hora', style: 3 },
        { value: 'Vehículo / Móvil', style: 3 },
        { value: 'SPN', style: 3 },
        { value: 'FMI', style: 3 },
        { value: 'Severidad', style: 3 },
        { value: 'Diagnóstico', style: 3 },
        { value: 'Modo de Falla', style: 3 },
        { value: 'Controlador (ECU)', style: 3 },
        { value: 'Ocurrencias', style: 3 },
        { value: 'Luz Roja (Parada)', style: 3 },
        { value: 'Luz Ámbar (Prioritaria)', style: 3 },
        { value: 'Lámpara MIL (Avería)', style: 3 },
      ];

      const dataRows: XlsxCell[][] = items.map((row) => [
        { value: formatDateTime(row.fecha_de_falla || row.fecha) },
        { value: row.movil ?? '—' },
        { value: row.codigo_diagnostico != null ? row.codigo_diagnostico : '—' },
        { value: row.codigo_modo_de_falla != null ? row.codigo_modo_de_falla : '—' },
        { value: row.tipo_de_atencion ?? 'Sin clasificar' },
        { value: row.diagnostico ?? '—' },
        { value: row.modo_de_falla ?? '—' },
        { value: row.nombre_de_controlador ?? '—' },
        { value: row.recuento_de_fallos ?? 1 },
        { value: row.luz_de_parada_roja ? 'SÍ' : 'NO' },
        { value: row.luz_de_parada_amber ? 'SÍ' : 'NO' },
        { value: row.lampara_de_averia ? 'SÍ' : 'NO' },
      ]);

      const workbook = buildXlsx([
        { name: 'Resumen', rows: summaryRows },
        { name: 'Detalle de Eventos', rows: [headerRow, ...dataRows] },
      ] satisfies XlsxSheet[]);

      const url = URL.createObjectURL(workbook);
      const link = document.createElement('a');
      link.href = url;
      const fromStr = faultFilters.date_from || 'inicio';
      const toStr = faultFilters.date_to || 'fin';
      link.download = `reporte_fallas_${fromStr}_${toStr}.xlsx`;
      link.click();
      URL.revokeObjectURL(url);
      toast.success(`${items.length.toLocaleString('es-CO')} eventos exportados a Excel.`);
    } catch (err) {
      toast.error(extractErrorMessage(err, 'Error al exportar los datos de fallas'));
    } finally {
      setIsExporting(false);
    }
  };

  const displayedEvents = React.useMemo(() => {
    if (!eventsQ.data?.items) return [];
    const q = searchQuery.trim().toLowerCase();
    if (!q) return eventsQ.data.items;

    return eventsQ.data.items.filter((item) => {
      const placa = (item.movil || '').toLowerCase();
      const diag = (item.diagnostico || '').toLowerCase();
      const code = item.codigo_diagnostico != null ? String(item.codigo_diagnostico) : '';
      const ctrl = (item.nombre_de_controlador || '').toLowerCase();
      return placa.includes(q) || diag.includes(q) || code.includes(q) || ctrl.includes(q);
    });
  }, [eventsQ.data?.items, searchQuery]);

  const severityData = bySeverity.map((b) => ({ label: b.severity, value: b.n_fallas }));
  const severityColors = bySeverity.map((b) => severityColor(b.severity));
  const paretoData = (paretoQ.data ?? []).map((p) => ({
    id: p.label,
    label: p.label,
    value: p.n_fallas,
  }));
  const rankingData = (rankingQ.data ?? []).map((r) => ({
    id: r.vehicle_id,
    label: r.vehicle_label ?? r.placa ?? r.vehicle_id ?? '—',
    value: r.value ?? 0,
  }));
  const series = seriesQ.data ?? [];

  const pctUrgentes =
    s && s.n_fallas > 0 ? `${((s.n_urgentes / s.n_fallas) * 100).toFixed(1)}% urgentes` : '0% urgentes';

  const activeTrend = React.useMemo(() => {
    if (!series || series.length === 0) return null;
    const values = series.map((p) => p.n_fallas || 0);
    const total = values.reduce((a, b) => a + b, 0);
    const avg = Math.round(total / values.length);
    const max = Math.max(...values);

    let targetIdx = values.length - 1;
    let isFiltered = false;
    if (periodFilter) {
      const foundIdx = series.findIndex((p) => p.label === periodFilter.label);
      if (foundIdx !== -1) {
        targetIdx = foundIdx;
        isFiltered = true;
      }
    }

    const currentVal = values[targetIdx] ?? 0;
    const prevVal = targetIdx > 0 ? (values[targetIdx - 1] ?? currentVal) : currentVal;
    const prevLabel = targetIdx > 0 ? series[targetIdx - 1]?.label : null;
    const changePct = targetIdx > 0 && prevVal > 0 ? ((currentVal - prevVal) / prevVal) * 100 : 0;
    const direction = changePct > 0 ? 'up' : changePct < 0 ? 'down' : 'flat';
    const compareLabel = prevLabel ? `vs ${prevLabel}` : 'vs periodo anterior';

    return {
      avg,
      max,
      currentVal,
      changePct,
      direction,
      compareLabel,
      isFiltered,
      selectedLabel: series[targetIdx]?.label,
    };
  }, [series, periodFilter]);

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
      {/* Indicador sutil condicional si hay filtros cruzados activos */}
      {(vehicleFilter || paretoFilter || periodFilter || severity) && (
        <div className="mb-4 flex items-center justify-between gap-2 rounded-lg bg-muted/40 border border-border/40 px-3 py-1.5 text-xs">
          <span className="text-muted-foreground font-medium">
            Filtros activos:{' '}
            {[
              severity && `Severidad (${severity})`,
              vehicleFilter && `Vehículo (${vehicleFilter.label})`,
              paretoFilter && `Categoría (${paretoFilter.value})`,
              periodFilter && `Periodo (${periodFilter.label})`,
            ]
              .filter(Boolean)
              .join(' · ')}
          </span>
          <button
            type="button"
            onClick={() => {
              setSeverity('');
              setVehicleFilter(null);
              setParetoFilter(null);
              setPeriodFilter(null);
            }}
            className="text-accent-blue hover:text-accent-blue/80 font-semibold underline underline-offset-2 cursor-pointer transition-colors"
          >
            Limpiar todos
          </button>
        </div>
      )}

      {/* KPI Cards con métricas macro y contexto */}
      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
        <KpiCard
          label="Fallas Registradas"
          value={s ? fmtCompact(s.n_fallas) : '—'}
          exact={s ? fmtInt(s.n_fallas) : undefined}
          hint="en el periodo filtrado"
          trend={series.map((p) => p.n_fallas)}
        />
        <KpiCard
          label="Fallas Urgentes"
          value={s ? fmtCompact(s.n_urgentes) : '—'}
          exact={s ? fmtInt(s.n_urgentes) : undefined}
          hint={pctUrgentes}
          icon={<AlertTriangle size={32} className="text-destructive" />}
        />
        <KpiCard
          label="Vehículos Afectados"
          value={s ? String(s.n_vehiculos) : '—'}
          hint="unidades con eventos"
          icon={<CarFront size={32} className="text-accent-blue" />}
        />
        <KpiCard
          label="Diagnósticos Únicos"
          value={s ? String(s.n_diagnosticos) : '—'}
          hint="códigos DTC distintos"
          icon={<Wrench size={32} className="text-accent-yellow" />}
        />
      </div>

      {/* Comparativo por grupo interno del cliente (solo con una flota en
          alcance y con grupos; el componente se oculta solo). */}
      <div className="mb-6">
        <GroupComparisonCard
          title="Fallas por grupo"
          subtitle="Fallas únicas del periodo; clic en una barra filtra la pestaña."
          rows={groupRows}
          derive={(t) => t.n_fallas ?? null}
          format={(value) => fmtInt(value)}
          detail={(t) =>
            `${fmtInt(t.n_eventos ?? 0)} eventos · ${fmtInt(t.n_urgentes ?? 0)} urgentes · ${fmtInt(
              t.n_vehiculos ?? 0,
            )} placas`
          }
          onSelectGroup={onSelectGroup}
          isLoading={porGrupoQ.isLoading}
        />
      </div>

      {/* Fila 1 de Gráficas: Severidad (Donut con métrica + desglose) + Pareto Analítico */}
      <div className="mb-6 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <div className="bg-card rounded-lg border p-4 flex flex-col justify-between">
          <div className="mb-2 flex items-start justify-between gap-3">
            <div>
              <h3 className="font-heading text-sm font-bold tracking-tight">Fallas por severidad</h3>
              <p className="text-muted-foreground text-xs">Distribución por nivel de atención</p>
            </div>
            {severity && (
              <button
                type="button"
                onClick={() => setSeverity('')}
                className="text-accent-blue hover:text-accent-blue/80 text-xs font-semibold underline underline-offset-2 cursor-pointer transition-colors"
              >
                Limpiar filtro
              </button>
            )}
          </div>

          {bySeverityQ.isLoading ? (
            <Skeleton className="h-[280px] w-full rounded-md" />
          ) : bySeverity.length === 0 ? (
            <div className="text-muted-foreground flex h-[280px] items-center justify-center text-sm">
              Sin datos para los filtros seleccionados.
            </div>
          ) : (
            <div className="flex-1 grid grid-cols-1 sm:grid-cols-12 items-center gap-4 py-2">
              {/* Gráfico Donut con métrica central ampliada (sin tooltip flotante) */}
              <div className="sm:col-span-5 relative flex items-center justify-center h-[260px]">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={severityData}
                      dataKey="value"
                      nameKey="label"
                      innerRadius={68}
                      outerRadius={98}
                      paddingAngle={3}
                      animationDuration={CHART_ANIMATION_DURATION}
                      animationEasing={CHART_ANIMATION_EASING as never}
                      cursor="pointer"
                      onClick={(entry: { name?: string; label?: string; payload?: { label?: string } }) => {
                        const lbl = entry?.payload?.label ?? entry?.name ?? entry?.label;
                        if (lbl) setSeverity((current) => (current === lbl ? '' : String(lbl)));
                      }}
                    >
                      {severityData.map((d, i) => {
                        const isDimmed = severity && d.label !== severity;
                        return (
                          <Cell
                            key={i}
                            fill={severityColors[i]}
                            fillOpacity={isDimmed ? 0.25 : 1}
                            stroke="transparent"
                          />
                        );
                      })}
                    </Pie>
                  </PieChart>
                </ResponsiveContainer>
                {/* Centro del Donut con total de eventos claramente nombrado */}
                <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center">
                  <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">
                    {severity ? 'Eventos filtrados' : 'Total eventos'}
                  </span>
                  <span className="font-heading text-2xl font-extrabold text-foreground tracking-tight">
                    {fmtInt(
                      severity
                        ? (bySeverity.find((b) => b.severity === severity)?.n_fallas ?? 0)
                        : bySeverity.reduce((acc, curr) => acc + (curr.n_fallas || 0), 0),
                    )}
                  </span>
                  <span className="text-[10px] text-muted-foreground font-medium">
                    eventos
                  </span>
                </div>
              </div>

              {/* Desglose interactivo centrado y con espaciado equilibrado */}
              <div className="sm:col-span-7 flex flex-col justify-center gap-2 py-1">
                {bySeverity.map((item) => {
                  const color = severityColor(item.severity);
                  const isSelected = severity === item.severity;
                  const isDimmed = severity && !isSelected;
                  const totalSum = bySeverity.reduce((acc, curr) => acc + (curr.n_fallas || 0), 0) || 1;
                  const share = (item.n_fallas / totalSum) * 100;

                  let hint = 'Monitoreo / Lámpara de avería';
                  if (item.severity.toLowerCase().includes('urgente') || item.severity.toLowerCase().includes('nivel 1')) {
                    hint = 'Parada de seguridad / Luz roja';
                  } else if (item.severity.toLowerCase().includes('prioritaria') || item.severity.toLowerCase().includes('nivel 2')) {
                    hint = 'Atención en taller / Luz ámbar';
                  }

                  return (
                    <button
                      key={item.severity}
                      type="button"
                      onClick={() => setSeverity((current) => (current === item.severity ? '' : item.severity))}
                      className={cn(
                        'group w-full rounded-xl border p-2.5 text-left transition-all duration-150 cursor-pointer',
                        isSelected
                          ? 'border-primary/60 bg-primary/5 shadow-xs ring-1 ring-primary/20'
                          : isDimmed
                            ? 'border-border/30 opacity-35 hover:opacity-75'
                            : 'border-border/60 hover:border-border hover:bg-muted/40 hover:shadow-2xs',
                      )}
                    >
                      <div className="flex items-center justify-between text-xs mb-1">
                        <div className="flex items-center gap-2.5">
                          <span className="h-3 w-3 rounded-full shrink-0 shadow-2xs" style={{ backgroundColor: color }} />
                          <div>
                            <span className="font-bold text-foreground block text-xs">
                              {item.severity}
                            </span>
                            <span className="text-[10px] text-muted-foreground font-medium">
                              {hint}
                            </span>
                          </div>
                        </div>
                        <div className="text-right">
                          <span className="font-bold text-foreground font-mono text-xs block">
                            {fmtInt(item.n_fallas)}
                          </span>
                          <span className="text-muted-foreground text-[11px] font-semibold">
                            ({share.toFixed(1)}%)
                          </span>
                        </div>
                      </div>
                      <div className="h-2 w-full rounded-full bg-muted/80 overflow-hidden mt-1">
                        <div
                          className="h-full rounded-full transition-all duration-500"
                          style={{ width: `${Math.max(share, 1.5)}%`, backgroundColor: color }}
                        />
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>
        <BarChartCard
          title="Pareto de fallas"
          subtitle="Top 10 por concentración"
          data={paretoData}
          categoryKey="label"
          valueKey="value"
          valueName="eventos"
          categoryAxisWidth={275}
          categoryLabelMaxLength={46}
          color={CHART_TONES.blueMuted}
          showXAxis={true}
          showLabels={false}
          showTooltip={true}
          isLoading={paretoQ.isLoading}
          format={(v) => fmtInt(v)}
          selectedId={
            paretoFilter?.dimension === dimension ? paretoFilter.value : null
          }
          onBarClick={(item) => {
            const value = item.label;
            if (typeof value === 'string' && value) {
              setParetoFilter((current) =>
                current?.dimension === dimension && current.value === value
                  ? null
                  : { dimension, value },
              );
            }
          }}
          action={
            <div className="shrink-0 inline-flex items-center rounded-lg bg-muted/60 p-0.5 border border-border/50 gap-0.5">
              {DIMENSIONS.map((d) => (
                <button
                  key={d.value}
                  type="button"
                  onClick={() => {
                    setDimension(d.value);
                    setParetoFilter(null);
                  }}
                  className={cn(
                    'whitespace-nowrap rounded-md px-2.5 py-1 text-xs font-medium transition-all duration-150',
                    dimension === d.value
                      ? 'bg-background text-foreground font-semibold shadow-xs'
                      : 'text-muted-foreground hover:text-foreground',
                  )}
                >
                  {d.label}
                </button>
              ))}
            </div>
          }
        />
      </div>

      {/* Fila 2 de Gráficas: Tendencia Histórica + Ranking de Vehículos */}
      <div className="mb-6 grid grid-cols-1 gap-4 xl:grid-cols-2">
        {/* Tarjeta de Tendencia de Fallas */}
        <div className="bg-card rounded-lg border p-4 flex flex-col">
          <div className="mb-2 flex items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 flex-wrap">
                <h3 className="font-heading text-sm font-bold tracking-tight">Tendencia de fallas</h3>
                {activeTrend && (
                  <Badge
                    variant="outline"
                    className={cn(
                      'text-[10px] font-semibold px-2 py-0.5',
                      activeTrend.direction === 'up'
                        ? 'border-destructive/40 text-destructive bg-destructive/5'
                        : activeTrend.direction === 'down'
                          ? 'border-emerald-500/40 text-emerald-600 dark:text-emerald-400 bg-emerald-500/5'
                          : 'text-muted-foreground',
                    )}
                  >
                    {activeTrend.direction === 'up' ? '▲' : '▼'}{' '}
                    {activeTrend.changePct > 0 ? `+${activeTrend.changePct.toFixed(1)}%` : `${activeTrend.changePct.toFixed(1)}%`}{' '}
                    {activeTrend.compareLabel}
                  </Badge>
                )}
              </div>
              <p className="text-muted-foreground text-xs">
                Histórico por {granularity === 'daily' ? 'día' : 'mes'} · clic en un punto para filtrar
              </p>
            </div>

            {periodFilter && (
              <button
                type="button"
                onClick={() => setPeriodFilter(null)}
                className="text-accent-blue hover:text-accent-blue/80 text-xs font-semibold underline underline-offset-2 cursor-pointer transition-colors"
              >
                Limpiar periodo
              </button>
            )}
          </div>

          {/* Gráfico de Área con Gradiente y Línea de Referencia ocupando el 100% de la altura */}
          <div className="flex-1 w-full min-h-[300px]">
            {seriesQ.isLoading ? (
              <Skeleton className="h-full w-full rounded-md" />
            ) : series.length === 0 ? (
              <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
                Sin datos de tendencia para los filtros seleccionados.
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart
                  data={series as unknown as Record<string, unknown>[]}
                  margin={{ top: 16, right: 28, left: 0, bottom: 4 }}
                  onClick={(state: { activeLabel?: string | number; activePayload?: Array<{ payload?: Record<string, unknown> }> }) => {
                    if (state?.activeLabel != null) {
                      pickPeriod(String(state.activeLabel), state.activePayload?.[0]?.payload);
                    }
                  }}
                  style={{ cursor: 'pointer' }}
                >
                  <defs>
                    <linearGradient id="fallasTrendGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={CHART_COLORS.red} stopOpacity={0.22} />
                      <stop offset="95%" stopColor={CHART_COLORS.red} stopOpacity={0.0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke={GRID_COLOR} strokeDasharray="3 3" vertical={false} />
                  <XAxis
                    dataKey="label"
                    tick={{ fontSize: 11, fill: AXIS_COLOR }}
                    tickLine={false}
                    axisLine={{ stroke: GRID_COLOR }}
                    padding={{ left: 16, right: 16 }}
                  />
                  <YAxis
                    tick={{ fontSize: 11, fill: AXIS_COLOR }}
                    tickLine={false}
                    axisLine={false}
                    width={45}
                    tickFormatter={(v: number) => fmtCompact(v)}
                  />
                  <Tooltip
                    contentStyle={tooltipStyle}
                    formatter={(value: number) => [fmtInt(value), 'Fallas']}
                  />
                  {activeTrend && (
                    <ReferenceLine
                      y={activeTrend.avg}
                      stroke={AXIS_COLOR}
                      strokeDasharray="4 4"
                      strokeWidth={1}
                      label={{
                        value: `Prom: ${fmtCompact(activeTrend.avg)}`,
                        fill: AXIS_COLOR,
                        fontSize: 10,
                        position: 'right',
                      }}
                    />
                  )}
                  <Area
                    type="monotone"
                    dataKey="n_fallas"
                    name="Fallas"
                    stroke={CHART_COLORS.red}
                    strokeWidth={2.5}
                    fill="url(#fallasTrendGrad)"
                    animationDuration={CHART_ANIMATION_DURATION}
                    animationEasing={CHART_ANIMATION_EASING as never}
                    dot={(props: { cx?: number; cy?: number; index?: number }) => {
                      const { cx = 0, cy = 0, index = -1 } = props;
                      const point = series[index];
                      const isSelected = point && point.label === periodFilter?.label;
                      return (
                        <circle
                          key={`trend-dot-${index}`}
                          cx={cx}
                          cy={cy}
                          r={isSelected ? 6 : 4}
                          fill={CHART_COLORS.red}
                          stroke="#fff"
                          strokeWidth={isSelected ? 3 : 2}
                        />
                      );
                    }}
                    activeDot={{ r: 6, fill: CHART_COLORS.red, stroke: '#fff', strokeWidth: 2 }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
        <BarChartCard
          title="Ranking por vehículo"
          subtitle="Mayor volumen de eventos"
          data={rankingData}
          categoryKey="label"
          valueKey="value"
          valueName="eventos"
          categoryLabelAlign="start"
          categoryAxisWidth={90}
          isMonoCategory={true}
          color={CHART_TONES.grayMuted}
          showXAxis={true}
          showLabels={false}
          showTooltip={true}
          isLoading={rankingQ.isLoading}
          format={(v) => fmtInt(v)}
          selectedId={vehicleFilter?.id ?? null}
          onBarClick={(item) => {
            const id = item.id;
            const label = item.label;
            if (typeof id === 'string' && id) {
              setVehicleFilter((current) =>
                current?.id === id
                  ? null
                  : { id, label: typeof label === 'string' ? label : id },
              );
            }
          }}
        />
      </div>

      {/* Sección del Historial de Fallas (Contraída por Defecto para Carga Rápida) */}
      <div className="rounded-lg border border-border bg-card overflow-hidden transition-all duration-200">
        <button
          type="button"
          onClick={() => setIsHistoryExpanded((prev) => !prev)}
          className="w-full flex items-center justify-between p-4 hover:bg-muted/40 transition-colors text-left cursor-pointer"
        >
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-muted text-foreground">
              <Clock className="h-4 w-4" />
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="font-heading text-sm font-bold text-foreground">Historial Detallado de Eventos</h2>
              {isHistoryExpanded ? (
                eventsQ.isFetching ? (
                  <Badge
                    variant="outline"
                    className="text-xs font-medium text-primary border-primary/30 bg-primary/5 inline-flex items-center gap-1.5 py-0.5"
                  >
                    <Loader2 className="h-3 w-3 animate-spin" />
                    Actualizando eventos...
                  </Badge>
                ) : (
                  <Badge variant="outline" className="text-xs font-mono font-bold">
                    {(eventsQ.data?.total ?? 0).toLocaleString('es-CO')} registros
                  </Badge>
                )
              ) : (
                <span className="text-xs text-muted-foreground font-medium flex items-center gap-1">
                  · <span className="underline underline-offset-2">clic para desplegar y consultar</span>
                </span>
              )}
              {dateScopedFilters.date_from && (
                <span className="text-xs text-muted-foreground font-medium">
                  · {dateScopedFilters.date_from} al {dateScopedFilters.date_to || dateScopedFilters.date_from}
                </span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-3">
            {isHistoryExpanded && (
              <label
                className="text-muted-foreground flex items-center gap-2 text-xs font-medium cursor-pointer"
                onClick={(e) => e.stopPropagation()}
              >
                <input
                  type="checkbox"
                  checked={onlyUrgent}
                  onChange={(e) => setOnlyUrgent(e.target.checked)}
                  className="rounded border-input text-primary focus:ring-primary/30"
                />
                Solo urgentes
              </label>
            )}
            <span className="text-muted-foreground flex h-7 w-7 items-center justify-center rounded-md hover:bg-muted">
              {isHistoryExpanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </span>
          </div>
        </button>

        {isHistoryExpanded && (
          <div className="border-t border-border p-4 pt-2 space-y-3">
            {/* Barra de Búsqueda Rápida Local y Botón de Exportar */}
            <div className="flex items-center justify-between gap-3 pt-1 flex-wrap">
              <div className="flex items-center gap-2 flex-1 max-w-md">
                <div className="relative flex-1">
                  <Search className="text-muted-foreground pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2" />
                  <Input
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Filtrar por placa, diagnóstico, código o ECU..."
                    className="h-8 pl-8 text-xs bg-background"
                  />
                </div>

                {searchQuery && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setSearchQuery('')}
                    className="h-8 px-2 text-xs text-muted-foreground hover:text-foreground cursor-pointer"
                  >
                    Limpiar
                  </Button>
                )}
              </div>

              <Button
                variant="outline"
                size="sm"
                onClick={handleExport}
                disabled={isExporting || (eventsQ.data?.total ?? 0) === 0}
                className="h-8 px-3 text-xs gap-1.5 font-medium border-border/80 hover:bg-muted cursor-pointer"
              >
                {isExporting ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
                    <span>Exportando...</span>
                  </>
                ) : (
                  <>
                    <Download className="h-3.5 w-3.5 text-primary" />
                    <span>Exportar Excel</span>
                  </>
                )}
              </Button>
            </div>

            {eventsQ.isError && (
              <p className="text-destructive text-sm">Error al cargar datos. Intenta de nuevo.</p>
            )}

            {eventsQ.isLoading ? (
              <div className="rounded-lg border border-border bg-card overflow-hidden">
                <Table>
                  <TableHeader>
                    <TableRow className="bg-muted/40 hover:bg-muted/40">
                      <TableHead className="font-semibold text-xs">Fecha / Hora</TableHead>
                      <TableHead className="font-semibold text-xs">Vehículo</TableHead>
                      <TableHead className="font-semibold text-xs">Severidad</TableHead>
                      <TableHead className="font-semibold text-xs">Diagnóstico</TableHead>
                      <TableHead className="font-semibold text-xs">Controlador (ECU)</TableHead>
                      <TableHead className="text-right font-semibold text-xs">Recuento</TableHead>
                      <TableHead className="text-center font-semibold text-xs">Lámparas</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {Array.from({ length: 6 }).map((_, i) => (
                      <TableRow key={i} className="animate-pulse">
                        <TableCell>
                          <Skeleton className="h-4 w-28 rounded" />
                        </TableCell>
                        <TableCell>
                          <Skeleton className="h-4 w-16 rounded" />
                        </TableCell>
                        <TableCell>
                          <Skeleton className="h-5 w-20 rounded-full" />
                        </TableCell>
                        <TableCell>
                          <Skeleton className="h-4 w-44 rounded" />
                        </TableCell>
                        <TableCell>
                          <Skeleton className="h-4 w-24 rounded" />
                        </TableCell>
                        <TableCell className="text-right">
                          <Skeleton className="h-4 w-8 ml-auto rounded" />
                        </TableCell>
                        <TableCell className="text-center">
                          <Skeleton className="h-3 w-10 mx-auto rounded-full" />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            ) : (
              <>
                <div
                  className={cn(
                    'rounded-lg border border-border bg-card overflow-hidden transition-opacity duration-150',
                    eventsQ.isFetching && 'opacity-60 pointer-events-none',
                  )}
                >
                  <Table>
                    <TableHeader>
                      <TableRow className="bg-muted/40 hover:bg-muted/40">
                        <TableHead className="font-semibold text-xs">Fecha / Hora</TableHead>
                        <TableHead className="font-semibold text-xs">Vehículo</TableHead>
                        <TableHead className="font-semibold text-xs">Severidad</TableHead>
                        <TableHead className="font-semibold text-xs">Diagnóstico</TableHead>
                        <TableHead className="font-semibold text-xs">Controlador (ECU)</TableHead>
                        <TableHead className="text-right font-semibold text-xs">Recuento</TableHead>
                        <TableHead className="text-center font-semibold text-xs">Lámparas</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {displayedEvents.length === 0 && (
                        <TableRow>
                          <TableCell colSpan={7} className="text-muted-foreground text-center py-6">
                            Sin fallas registradas para los filtros seleccionados.
                          </TableCell>
                        </TableRow>
                      )}
                      {displayedEvents.map((row) => {
                        const isUrgentRow = row.luz_de_parada_roja || (row.tipo_de_atencion || '').toLowerCase().includes('urgente');
                        return (
                          <TableRow
                            key={row.row_id}
                            className={cn(
                              'transition-colors',
                              isUrgentRow
                                ? 'bg-destructive/5 hover:bg-destructive/10'
                                : 'hover:bg-muted/50',
                            )}
                          >
                            <TableCell className="whitespace-nowrap font-mono text-xs text-foreground">
                              {formatDateTime(row.fecha_de_falla || row.fecha)}
                            </TableCell>
                            <TableCell className="font-bold text-xs text-foreground font-mono">
                              {row.movil ?? '—'}
                            </TableCell>
                            <TableCell>{severityBadge(row.tipo_de_atencion)}</TableCell>
                            <TableCell className="max-w-xs truncate text-xs text-foreground font-medium" title={row.diagnostico ?? undefined}>
                              {row.diagnostico ?? '—'}
                            </TableCell>
                            <TableCell className="text-muted-foreground max-w-[10rem] truncate text-xs" title={row.nombre_de_controlador ?? undefined}>
                              {row.nombre_de_controlador ?? '—'}
                            </TableCell>
                            <TableCell className="text-right font-mono text-xs">
                              {row.recuento_de_fallos ?? 1}
                            </TableCell>
                            <TableCell className="text-center">
                              <div className="flex items-center justify-center gap-1.5">
                                {row.luz_de_parada_roja && (
                                  <span
                                    className="inline-flex h-2.5 w-2.5 rounded-full bg-destructive ring-2 ring-destructive/25"
                                    title="Luz Roja (Parada obligatoria / Nivel 1)"
                                  />
                                )}
                                {row.luz_de_parada_amber && (
                                  <span
                                    className="inline-flex h-2.5 w-2.5 rounded-full bg-accent-yellow ring-2 ring-accent-yellow/25"
                                    title="Luz Ámbar (Atención prioritaria / Nivel 2)"
                                  />
                                )}
                                {row.lampara_de_averia && (
                                  <span
                                    className="inline-flex h-2.5 w-2.5 rounded-full bg-[#ffb301] ring-2 ring-accent-yellow/25"
                                    title="Lámpara MIL (Avería / Check Engine)"
                                  />
                                )}
                                {!row.luz_de_parada_roja && !row.luz_de_parada_amber && !row.lampara_de_averia && (
                                  <span className="text-muted-foreground/40 text-[11px]">—</span>
                                )}
                              </div>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>

                <HistoryPagination
                  total={eventsQ.data?.total ?? 0}
                  limit={pageSize}
                  offset={offset}
                  onOffsetChange={setOffset}
                  onLimitChange={setPageSize}
                />
              </>
            )}
          </div>
        )}
      </div>
    </>
  );
}
