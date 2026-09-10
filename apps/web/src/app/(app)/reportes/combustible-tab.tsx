'use client';

import * as React from 'react';
import { toast } from 'sonner';

import {
  Calendar,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Download,
  Loader2,
  Search,
} from 'lucide-react';
import { BarChartCard } from '@/components/charts/bar-chart-card';
import { CHART_COLORS, CHART_TONES } from '@/components/charts/chart-theme';
import { ComposedChartCard } from '@/components/charts/composed-chart-card';
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
import { cn } from '@/lib/utils';
import {
  applyCross,
  type CombustibleParams,
  type CrossBucket,
  type CrossFilter,
  type ReportesFilters,
  fetchCombustibleDaily,
  normalizeRankingMetric,
  rankingMetricOptions,
  useCombustibleDaily,
  useCombustiblePorGrupo,
  useCombustibleSummary,
  useCombustibleTimeseries,
  useVehicleRanking,
} from '@/lib/reportes';
import type { FuelKind, Granularity, RankingMetric } from '@/lib/types';
import { buildXlsx, type XlsxCell, type XlsxSheet } from '@/lib/xlsx';

import { fmt, fmtCompact, fmtInt, fmtPct, KpiCard } from './shared';

interface CombustibleTabProps {
  filters: ReportesFilters;
  granularity: Granularity;
  fuelKind: FuelKind;
  hasGas: boolean;
  hasLiquid: boolean;
  onFuelKindChange: (fuelKind: FuelKind) => void;
  /** Selección de cross-filter (clic en gráficos). */
  cross: CrossFilter;
  onCross: React.Dispatch<React.SetStateAction<CrossFilter>>;
  /** Clic en una barra del comparativo por grupo → aplicar ese grupo al filtro global. */
  onSelectGroup?: (groupId: string) => void;
}

/** Paginador enriquecido con salto directo a página y selector de filas */
function DetailPagination({
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
          Mostrando{' '}
          <span className="text-foreground font-mono font-semibold">
            {fromRecord}-{toRecord}
          </span>{' '}
          de{' '}
          <span className="text-foreground font-mono font-semibold">
            {total.toLocaleString('es-CO')}
          </span>{' '}
          registros
        </span>

        {onLimitChange && (
          <div className="border-border/60 flex items-center gap-1.5 border-l pl-3">
            <span className="text-muted-foreground text-[11px]">Filas:</span>
            <select
              value={limit}
              onChange={(e) => {
                onLimitChange(Number(e.target.value));
                onOffsetChange(0);
              }}
              className="border-border/60 bg-background text-foreground focus:ring-primary h-7 cursor-pointer rounded border px-1.5 font-mono text-xs focus:outline-none focus:ring-1"
            >
              <option value={10}>10</option>
              <option value={20}>20</option>
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
          className="h-8 cursor-pointer px-2.5 text-xs"
        >
          <ChevronLeft className="mr-1 h-3.5 w-3.5" />
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
          className="h-8 cursor-pointer px-2.5 text-xs"
        >
          Siguiente
          <ChevronRight className="ml-1 h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
}

export function CombustibleTab({
  filters,
  granularity,
  fuelKind,
  hasGas,
  hasLiquid,
  onFuelKindChange,
  cross,
  onCross,
  onSelectGroup,
}: CombustibleTabProps) {
  const [rankMetric, setRankMetric] = React.useState<RankingMetric>('comb');
  const [rankLimit, setRankLimit] = React.useState<10 | 0>(10);
  const [rankSortOrder, setRankSortOrder] = React.useState<'asc' | 'desc'>('desc');
  const [offset, setOffset] = React.useState(0);
  const [pageSize, setPageSize] = React.useState(20);
  const [showDetail, setShowDetail] = React.useState(false);
  const [searchQuery, setSearchQuery] = React.useState('');
  const [isExporting, setIsExporting] = React.useState(false);
  const isGas = fuelKind === 'gas';
  const fuelUnit = isGas ? 'm³' : 'gal';
  const detailFuelUnit = isGas ? 'm³' : 'Gal';
  const detailEfficiencyLabel = isGas ? 'Km/m³' : 'Kpg';
  const detailRateLabel = isGas ? 'm³/h' : 'Gph';
  const efficiencyKey = isGas ? 'km_m3' : 'km_gal';
  const rateKey = isGas ? 'm3_hr' : 'gal_hr';
  const idleRateKey = isGas ? 'm3_hr_ralenti' : 'gal_hr_ralenti';
  const fuelFilters = React.useMemo(
    () => ({ ...filters, fuel_kind: fuelKind }),
    [filters, fuelKind],
  );

  // Filtros por gráfico: cada uno aplica el cross EXCEPTO su propia dimensión.
  const consumerFilters = React.useMemo(() => applyCross(fuelFilters, cross), [fuelFilters, cross]);
  const seriesFilters = React.useMemo(
    () => applyCross(fuelFilters, cross, 'bucket'),
    [fuelFilters, cross],
  );
  const rankingFilters = React.useMemo(
    () => applyCross(fuelFilters, cross, 'vehicle'),
    [fuelFilters, cross],
  );

  // Resetear paginación cuando cambian los filtros efectivos de la tabla o el tamaño de página.
  React.useEffect(() => setOffset(0), [consumerFilters, pageSize]);

  const tableParams: CombustibleParams = { ...consumerFilters, limit: pageSize, offset };

  const summaryQ = useCombustibleSummary(consumerFilters);
  const seriesQ = useCombustibleTimeseries(seriesFilters, granularity);

  // Una flota vocacional trabaja por horas ECM, no por kilómetros: su
  // rendimiento es gal/h y no km/gal. Lo decide el backend sobre los vehículos
  // con datos del alcance; aquí sólo se elige qué mostrar.
  const allVocacional = summaryQ.data?.all_vocacional ?? false;
  const rankingMetrics = rankingMetricOptions(isGas, allVocacional);
  const effectiveRankMetric = normalizeRankingMetric(rankMetric, isGas, allVocacional);
  // Hasta conocer el tipo de operación no se pide un ranking de rendimiento:
  // se pediría km/gal para luego descartarlo por gal/h.
  const rankingReady = summaryQ.data != null || effectiveRankMetric === rankMetric;
  const rankingQ = useVehicleRanking(
    rankingFilters,
    effectiveRankMetric,
    rankLimit,
    rankSortOrder,
    rankingReady,
  );
  const dailyQ = useCombustibleDaily(tableParams, showDetail);

  // Comparativo por grupo interno: MISMOS filtros (con fuel_kind) que el
  // summary de la pestaña. Solo se consulta cuando el comparativo aplica
  // (una flota en alcance y con grupos); queda fuera de la precarga por
  // pestaña a propósito.
  const groupsAvailable = useGroupFilterAvailable();
  const porGrupoQ = useCombustiblePorGrupo(consumerFilters, groupsAvailable);
  const groupRows: GroupComparisonRow[] = React.useMemo(
    () =>
      (porGrupoQ.data ?? []).map((b) => ({
        groupId: b.group_id,
        values: {
          kms: b.kms,
          comb: b.comb,
          hrs: b.hrs,
          comb_ralenti: b.comb_ralenti,
          n_vehiculos: b.n_vehiculos,
          n_registros: b.n_registros,
        },
      })),
    [porGrupoQ.data],
  );

  const displayedItems = React.useMemo(() => {
    if (!dailyQ.data?.items) return [];
    if (!searchQuery.trim()) return dailyQ.data.items;
    const q = searchQuery.toLowerCase().trim();
    return dailyQ.data.items.filter(
      (r) =>
        (r.placa && r.placa.toLowerCase().includes(q)) ||
        (r.motor_type && r.motor_type.toLowerCase().includes(q)) ||
        (r.fecha && r.fecha.toLowerCase().includes(q)),
    );
  }, [dailyQ.data?.items, searchQuery]);

  // Resuelve el bucket clicado (eje X) a un rango de fechas según granularidad.
  const resolveBucket = React.useCallback(
    (label: string, payload?: Record<string, unknown>): CrossBucket => {
      if (granularity === 'monthly') {
        const yyyymm = Number(payload?.periodo);
        if (Number.isFinite(yyyymm) && yyyymm > 100000) {
          const y = Math.floor(yyyymm / 100);
          const m = yyyymm % 100;
          const month = String(m).padStart(2, '0');
          const lastDay = new Date(Date.UTC(y, m, 0)).getUTCDate();
          let dateFrom = `${y}-${month}-01`;
          let dateTo = `${y}-${month}-${String(lastDay).padStart(2, '0')}`;

          if (filters.date_from && dateFrom < filters.date_from) {
            dateFrom = filters.date_from;
          }
          if (filters.date_to && dateTo > filters.date_to) {
            dateTo = filters.date_to;
          }

          return { label, dateFrom, dateTo };
        }
      }
      return { label, dateFrom: label, dateTo: label };
    },
    [filters.date_from, filters.date_to, granularity],
  );

  const pickVehicle = React.useCallback(
    (id: string, label: string) =>
      onCross((c) =>
        c.vehicleId === id
          ? { ...c, vehicleId: undefined, vehicleLabel: undefined }
          : { ...c, vehicleId: id, vehicleLabel: label },
      ),
    [onCross],
  );

  const pickBucket = React.useCallback(
    (label: string, payload?: Record<string, unknown>) => {
      const b = resolveBucket(label, payload);
      onCross((c) =>
        c.bucket?.label === b.label ? { ...c, bucket: undefined } : { ...c, bucket: b },
      );
    },
    [onCross, resolveBucket],
  );

  const selectedBucket = cross.bucket?.label ?? null;

  const s = summaryQ.data;
  const series = seriesQ.data ?? [];

  const eficienciaTitle = 'Combustible y rendimiento';

  const rankMeta =
    rankingMetrics.find((m) => m.value === effectiveRankMetric) ?? rankingMetrics[0]!;
  const rankingData = (rankingQ.data ?? []).map((r) => ({
    id: r.vehicle_id ?? '',
    label: r.vehicle_label ?? r.placa ?? r.vehicle_id ?? '—',
    value: rankMeta.pct ? (r.value ?? 0) * 100 : (r.value ?? 0),
  }));

  const vehicleSelected = cross.vehicleId != null;
  const efficiency = isGas ? s?.km_m3 : s?.km_gal;
  const fuelPerHour = isGas ? s?.m3_hr : s?.gal_hr;
  const groupRendimiento = allVocacional
    ? {
        title: 'Consumo por hora por grupo',
        subtitle: `${fuelUnit}/h ponderado por grupo; clic en una barra filtra la pestaña.`,
        derive: (t: Record<string, number>) =>
          (t.hrs ?? 0) > 0 ? (t.comb ?? 0) / (t.hrs ?? 1) : null,
        reference:
          fuelPerHour != null ? { label: `Flota (${fuelUnit}/h)`, value: fuelPerHour } : null,
      }
    : {
        title: 'Rendimiento por grupo',
        subtitle: `km/${fuelUnit} ponderado por grupo; clic en una barra filtra la pestaña.`,
        derive: (t: Record<string, number>) =>
          (t.comb ?? 0) > 0 ? (t.kms ?? 0) / (t.comb ?? 1) : null,
        reference:
          efficiency != null ? { label: `Flota (km/${fuelUnit})`, value: efficiency } : null,
      };
  const idleFuelPerHour = isGas ? s?.m3_hr_ralenti : s?.gal_hr_ralenti;

  const exportExcel = async () => {
    setIsExporting(true);
    try {
      const rows = await fetchCombustibleDaily(consumerFilters);
      const headers = [
        'Fecha',
        'Placa',
        'Motor',
        'Kms ECM',
        `Comb (${detailFuelUnit})`,
        detailEfficiencyLabel,
        detailRateLabel,
        `Comb Ralentí (${detailFuelUnit})`,
        'Horas Ralentí',
        'Velocidad Promedio (Km/h)',
      ];
      const values = rows.map((row) => [
        row.fecha ?? '',
        row.placa ?? '',
        row.motor_type ?? '',
        row.kms_ecm,
        row.comb,
        isGas ? row.km_m3 : row.km_gal,
        isGas ? row.m3_hr : row.gal_hr,
        row.comb_ralenti,
        row.ralenti,
        row.velocidad_promedio,
      ]);
      const toCells = (row: unknown[], style?: number): XlsxCell[] =>
        row.map((value) => ({ value: value as XlsxCell['value'], style }));
      const detailRows: XlsxCell[][] = [
        toCells(headers, 3),
        ...values.map((row) =>
          toCells(row).map((cell, index) => ({
            ...cell,
            style: index >= 3 ? 4 : cell.style,
          })),
        ),
      ];
      const vehicleMap = new Map<
        string,
        {
          motor: string;
          days: number;
          kms: number;
          efficiencyKms: number;
          fuel: number;
        }
      >();
      for (const row of rows) {
        const plate = row.placa ?? '—';
        const current = vehicleMap.get(plate) ?? {
          motor: row.motor_type ?? '',
          days: 0,
          kms: 0,
          efficiencyKms: 0,
          fuel: 0,
        };
        current.days += 1;
        current.kms += row.kms_ecm ?? 0;
        current.efficiencyKms += !isGas || (row.comb ?? 0) > 0 ? (row.kms_ecm ?? 0) : 0;
        current.fuel += row.comb ?? 0;
        vehicleMap.set(plate, current);
      }
      const vehicleRows: XlsxCell[][] = [
        toCells(
          ['Placa', 'Motor', 'Días con datos', 'Kms ECM', `Comb. (${fuelUnit})`, `km/${fuelUnit}`],
          3,
        ),
        ...Array.from(vehicleMap)
          .sort(([, a], [, b]) => {
            const efficiencyA = a.fuel > 0 ? a.efficiencyKms / a.fuel : Number.NEGATIVE_INFINITY;
            const efficiencyB = b.fuel > 0 ? b.efficiencyKms / b.fuel : Number.NEGATIVE_INFINITY;
            return efficiencyB - efficiencyA;
          })
          .map(([plate, vehicle]) =>
            toCells([
              plate,
              vehicle.motor,
              vehicle.days,
              vehicle.kms,
              vehicle.fuel,
              vehicle.fuel > 0 ? vehicle.efficiencyKms / vehicle.fuel : null,
            ]).map((cell, index) => ({
              ...cell,
              style: index === 2 ? 6 : index >= 3 ? 4 : cell.style,
            })),
          ),
      ];
      const exportedVehicleCount = new Set(
        rows.map((row) => row.vehicle_id ?? row.placa).filter(Boolean),
      ).size;
      const exportedMotorCount = new Set(rows.map((row) => row.motor_type).filter(Boolean)).size;
      const summaryRows: XlsxCell[][] = [
        [{ value: 'Combustible y rendimiento', style: 1 }],
        [{ value: 'Filtros aplicados', style: 2 }],
        toCells(['Tipo de combustible', isGas ? 'Gas (m³)' : 'Diésel (gal)']),
        toCells(['Desde', consumerFilters.date_from ?? 'Todos']),
        toCells(['Hasta', consumerFilters.date_to ?? 'Todos']),
        toCells([
          'Vehículos incluidos',
          consumerFilters.vehicle_id?.length || exportedVehicleCount,
        ]).map((cell, index) => ({ ...cell, style: index === 1 ? 6 : cell.style })),
        toCells([
          'Motores incluidos',
          consumerFilters.motor_type?.length || exportedMotorCount,
        ]).map((cell, index) => ({ ...cell, style: index === 1 ? 6 : cell.style })),
        [{ value: 'Indicadores', style: 2 }],
        toCells(['Kms ECM', s?.kms_ecm], 4),
        toCells(['Horas ECM', s?.hrs_ecm], 4),
        toCells([`Combustible (${fuelUnit})`, s?.comb], 4),
        toCells([`Rendimiento (km/${fuelUnit})`, efficiency], 4),
        toCells([`Consumo (${fuelUnit}/h)`, fuelPerHour], 4),
        toCells(['% Ralentí', s?.pct_ralenti == null ? null : s.pct_ralenti], 5),
        toCells(['Vehículos con datos', s?.n_vehiculos]).map((cell, index) => ({
          ...cell,
          style: index === 1 ? 6 : cell.style,
        })),
        toCells(['Registros diarios', s?.n_registros]).map((cell, index) => ({
          ...cell,
          style: index === 1 ? 6 : cell.style,
        })),
      ];
      const sheets: XlsxSheet[] = [
        { name: 'Resumen', rows: summaryRows },
        { name: 'Vehículos', rows: vehicleRows },
        { name: 'Detalle Diario', rows: detailRows },
      ];
      const workbook = buildXlsx(sheets);
      const url = URL.createObjectURL(workbook);
      const link = document.createElement('a');
      link.href = url;
      link.download = `detalle_combustible_${fuelKind}_${filters.date_from ?? 'periodo'}.xlsx`;
      link.click();
      URL.revokeObjectURL(url);
      toast.success(`${rows.length} registros exportados.`);
    } catch (err) {
      toast.error(extractErrorMessage(err, 'No se pudieron exportar los datos'));
    } finally {
      setIsExporting(false);
    }
  };

  const [compareMode, setCompareMode] = React.useState<'step' | 'shift'>('step');
  const [isKpiFlipped, setIsKpiFlipped] = React.useState(false);
  const toggleKpiFlip = () => setIsKpiFlipped((prev) => !prev);

  // MTD dates para granularidad mensual (mismos N días del mes actual vs primeros N días del mes previo)
  const mtdDates = React.useMemo(() => {
    if (!filters.date_to) return null;
    const [yStr, mStr, dStr] = filters.date_to.split('-');
    const year = parseInt(yStr ?? '2026', 10);
    const month = parseInt(mStr ?? '1', 10);
    const day = parseInt(dStr ?? '1', 10);

    const currFrom = `${yStr}-${mStr}-01`;
    const currTo = filters.date_to;

    const prevMonth = month === 1 ? 12 : month - 1;
    const prevYear = month === 1 ? year - 1 : year;
    const prevMStr = String(prevMonth).padStart(2, '0');
    const prevYStr = String(prevYear);

    const lastDayOfPrev = new Date(prevYear, prevMonth, 0).getDate();
    const matchedDay = Math.min(day, lastDayOfPrev);
    const matchedDStr = String(matchedDay).padStart(2, '0');

    const prevFrom = `${prevYStr}-${prevMStr}-01`;
    const prevTo = `${prevYStr}-${prevMStr}-${matchedDStr}`;

    return {
      elapsedDays: day,
      currFrom,
      currTo,
      prevFrom,
      prevTo,
      currLabel: `${currFrom} al ${currTo} (${day} días)`,
      prevLabel: `${prevFrom} al ${prevTo} (${matchedDay} días)`,
    };
  }, [filters.date_to]);

  const currMtdQ = useCombustibleSummary(
    {
      ...filters,
      date_from: mtdDates?.currFrom,
      date_to: mtdDates?.currTo,
    },
    compareMode === 'step' && granularity === 'monthly' && !!mtdDates,
  );

  const prevMtdQ = useCombustibleSummary(
    {
      ...filters,
      date_from: mtdDates?.prevFrom,
      date_to: mtdDates?.prevTo,
    },
    compareMode === 'step' && granularity === 'monthly' && !!mtdDates,
  );

  // Time Shift dates (mismo número de días inmediatamente antes de date_from)
  const shiftDates = React.useMemo(() => {
    if (!filters.date_from || !filters.date_to) return null;
    const dFrom = new Date(`${filters.date_from}T00:00:00Z`);
    const dTo = new Date(`${filters.date_to}T00:00:00Z`);
    if (isNaN(dFrom.getTime()) || isNaN(dTo.getTime())) return null;
    const diffMs = dTo.getTime() - dFrom.getTime();
    const prevTo = new Date(dFrom.getTime() - 24 * 60 * 60 * 1000);
    const prevFrom = new Date(prevTo.getTime() - diffMs);
    return {
      date_from: prevFrom.toISOString().slice(0, 10),
      date_to: prevTo.toISOString().slice(0, 10),
    };
  }, [filters.date_from, filters.date_to]);

  const effectiveCompareMode = granularity === 'daily' ? 'shift' : compareMode;

  const prevSummaryQ = useCombustibleSummary(
    {
      ...filters,
      date_from: shiftDates?.date_from,
      date_to: shiftDates?.date_to,
    },
    effectiveCompareMode === 'shift' && !!shiftDates,
  );

  const prevS = prevSummaryQ.data;
  const prevEfficiency = prevS?.km_gal ?? prevS?.km_m3 ?? null;
  const prevFuelPerHour = isGas ? prevS?.m3_hr : prevS?.gal_hr;

  // Deltas calculados según effectiveCompareMode
  const deltas = React.useMemo(() => {
    const calcDelta = (curr: number | null | undefined, prev: number | null | undefined) => {
      if (curr == null || prev == null || prev === 0) return null;
      return ((curr - prev) / Math.abs(prev)) * 100;
    };

    if (effectiveCompareMode === 'step') {
      if (!currMtdQ.data || !prevMtdQ.data || !mtdDates) return null;
      const currData = currMtdQ.data;
      const prevData = prevMtdQ.data;

      const currEff = currData.km_gal ?? currData.km_m3 ?? null;
      const prevEff = prevData.km_gal ?? prevData.km_m3 ?? null;
      const currRate = isGas ? currData.m3_hr : currData.gal_hr;
      const prevRate = isGas ? prevData.m3_hr : prevData.gal_hr;
      const currVeh = currData.n_vehiculos;
      const prevVeh = prevData.n_vehiculos;
      const currVel = currData.velocidad_promedio;
      const prevVel = prevData.velocidad_promedio;
      const currPctRal = currData.pct_ralenti;
      const prevPctRal = prevData.pct_ralenti;
      const currCombRal = currData.comb_ralenti;
      const prevCombRal = prevData.comb_ralenti;
      const currIdleRate = isGas ? currData.m3_hr_ralenti : currData.gal_hr_ralenti;
      const prevIdleRate = isGas ? prevData.m3_hr_ralenti : prevData.gal_hr_ralenti;

      const compLabel = `Mes actual vs anterior (mismos ${mtdDates.elapsedDays} días)`;
      const currentPeriod = mtdDates.currLabel;
      const previousPeriod = mtdDates.prevLabel;

      return {
        kms: {
          percent: calcDelta(currData.kms_ecm, prevData.kms_ecm),
          polarity: 'neutral' as const,
          label: compLabel,
          currentPeriod,
          previousPeriod,
          currentVal: fmtInt(currData.kms_ecm),
          previousVal: fmtInt(prevData.kms_ecm),
        },
        hrs: {
          percent: calcDelta(currData.hrs_ecm, prevData.hrs_ecm),
          polarity: 'neutral' as const,
          label: compLabel,
          currentPeriod,
          previousPeriod,
          currentVal: fmtInt(currData.hrs_ecm),
          previousVal: fmtInt(prevData.hrs_ecm),
        },
        comb: {
          percent: calcDelta(currData.comb, prevData.comb),
          polarity: 'neutral' as const,
          label: compLabel,
          currentPeriod,
          previousPeriod,
          currentVal: fmtInt(currData.comb),
          previousVal: fmtInt(prevData.comb),
        },
        eff: {
          percent: calcDelta(currEff, prevEff),
          polarity: 'higher-is-better' as const,
          label: compLabel,
          currentPeriod,
          previousPeriod,
          currentVal: fmt(currEff, 2),
          previousVal: fmt(prevEff, 2),
        },
        rate: {
          percent: calcDelta(currRate, prevRate),
          polarity: 'lower-is-better' as const,
          label: compLabel,
          currentPeriod,
          previousPeriod,
          currentVal: fmt(currRate, 2),
          previousVal: fmt(prevRate, 2),
        },
        veh: {
          percent: calcDelta(currVeh, prevVeh),
          polarity: 'neutral' as const,
          label: compLabel,
          currentPeriod,
          previousPeriod,
          currentVal: String(currVeh),
          previousVal: String(prevVeh),
        },
        vel: {
          percent: calcDelta(currVel, prevVel),
          polarity: 'neutral' as const,
          label: compLabel,
          currentPeriod,
          previousPeriod,
          currentVal: fmt(currVel, 1),
          previousVal: fmt(prevVel, 1),
        },
        pctRalenti: {
          percent: calcDelta(currPctRal, prevPctRal),
          polarity: 'lower-is-better' as const,
          label: compLabel,
          currentPeriod,
          previousPeriod,
          currentVal: fmtPct(currPctRal),
          previousVal: fmtPct(prevPctRal),
        },
        combRalenti: {
          percent: calcDelta(currCombRal, prevCombRal),
          polarity: 'neutral' as const,
          label: compLabel,
          currentPeriod,
          previousPeriod,
          currentVal: fmtInt(currCombRal),
          previousVal: fmtInt(prevCombRal),
        },
        idleRate: {
          percent: calcDelta(currIdleRate, prevIdleRate),
          polarity: 'lower-is-better' as const,
          label: compLabel,
          currentPeriod,
          previousPeriod,
          currentVal: fmt(currIdleRate, 2),
          previousVal: fmt(prevIdleRate, 2),
        },
      };
    } else {
      if (!prevS || !shiftDates) return null;
      const compLabel = 'Periodo seleccionado vs anterior (mismos días)';
      const currentPeriod = `${filters.date_from ?? ''} al ${filters.date_to ?? ''}`;
      const previousPeriod = `${shiftDates.date_from} al ${shiftDates.date_to}`;

      const currIdleRate = idleFuelPerHour;
      const prevIdleRate = isGas ? prevS.m3_hr_ralenti : prevS.gal_hr_ralenti;

      return {
        kms: {
          percent: calcDelta(s?.kms_ecm, prevS.kms_ecm),
          polarity: 'neutral' as const,
          label: compLabel,
          currentPeriod,
          previousPeriod,
          currentVal: fmtInt(s?.kms_ecm),
          previousVal: fmtInt(prevS.kms_ecm),
        },
        hrs: {
          percent: calcDelta(s?.hrs_ecm, prevS.hrs_ecm),
          polarity: 'neutral' as const,
          label: compLabel,
          currentPeriod,
          previousPeriod,
          currentVal: fmtInt(s?.hrs_ecm),
          previousVal: fmtInt(prevS.hrs_ecm),
        },
        comb: {
          percent: calcDelta(s?.comb, prevS.comb),
          polarity: 'neutral' as const,
          label: compLabel,
          currentPeriod,
          previousPeriod,
          currentVal: fmtInt(s?.comb),
          previousVal: fmtInt(prevS.comb),
        },
        eff: {
          percent: calcDelta(efficiency, prevEfficiency),
          polarity: 'higher-is-better' as const,
          label: compLabel,
          currentPeriod,
          previousPeriod,
          currentVal: fmt(efficiency, 2),
          previousVal: fmt(prevEfficiency, 2),
        },
        rate: {
          percent: calcDelta(fuelPerHour, prevFuelPerHour),
          polarity: 'lower-is-better' as const,
          label: compLabel,
          currentPeriod,
          previousPeriod,
          currentVal: fmt(fuelPerHour, 2),
          previousVal: fmt(prevFuelPerHour, 2),
        },
        veh: {
          percent: calcDelta(s?.n_vehiculos, prevS.n_vehiculos),
          polarity: 'neutral' as const,
          label: compLabel,
          currentPeriod,
          previousPeriod,
          currentVal: String(s?.n_vehiculos ?? '—'),
          previousVal: String(prevS.n_vehiculos ?? '—'),
        },
        vel: {
          percent: calcDelta(s?.velocidad_promedio, prevS.velocidad_promedio),
          polarity: 'neutral' as const,
          label: compLabel,
          currentPeriod,
          previousPeriod,
          currentVal: fmt(s?.velocidad_promedio, 1),
          previousVal: fmt(prevS.velocidad_promedio, 1),
        },
        pctRalenti: {
          percent: calcDelta(s?.pct_ralenti, prevS.pct_ralenti),
          polarity: 'lower-is-better' as const,
          label: compLabel,
          currentPeriod,
          previousPeriod,
          currentVal: fmtPct(s?.pct_ralenti),
          previousVal: fmtPct(prevS.pct_ralenti),
        },
        combRalenti: {
          percent: calcDelta(s?.comb_ralenti, prevS.comb_ralenti),
          polarity: 'neutral' as const,
          label: compLabel,
          currentPeriod,
          previousPeriod,
          currentVal: fmtInt(s?.comb_ralenti),
          previousVal: fmtInt(prevS.comb_ralenti),
        },
        idleRate: {
          percent: calcDelta(currIdleRate, prevIdleRate),
          polarity: 'lower-is-better' as const,
          label: compLabel,
          currentPeriod,
          previousPeriod,
          currentVal: fmt(currIdleRate, 2),
          previousVal: fmt(prevIdleRate, 2),
        },
      };
    }
  }, [
    effectiveCompareMode,
    currMtdQ.data,
    prevMtdQ.data,
    mtdDates,
    series,
    isGas,
    s,
    prevS,
    shiftDates,
    efficiency,
    prevEfficiency,
    fuelPerHour,
    prevFuelPerHour,
    idleFuelPerHour,
  ]);

  // Tarjetas del encabezado en orden de importancia para quien lee el reporte:
  // primero el rendimiento —la medida de la que trata la pantalla— y detrás
  // sus dos componentes. En una flota comercial eso es km/gal con kilómetros
  // y combustible; en una vocacional es gal/h con horas y combustible.
  const rendimientoCard = {
    label: `km/${fuelUnit}`,
    value: fmt(efficiency, 2),
    exact: fmt(efficiency, 2),
    trend: series.map((p) => (isGas ? p.km_m3 : p.km_gal)),
    delta: deltas?.eff,
  };
  const consumoPorHoraCard = {
    label: `${fuelUnit}/h`,
    value: fmt(fuelPerHour, 2),
    exact: fmt(fuelPerHour, 2),
    trend: series.map((p) => (isGas ? p.m3_hr : p.gal_hr)),
    delta: deltas?.rate,
  };
  const kmsCard = {
    label: 'Kms ECM',
    value: fmtCompact(s?.kms_ecm),
    exact: fmtInt(s?.kms_ecm),
    trend: series.map((p) => p.kms_ecm),
    delta: deltas?.kms,
  };
  const combCard = {
    label: `Comb. (${fuelUnit})`,
    value: fmtCompact(s?.comb),
    exact: fmtInt(s?.comb),
    trend: series.map((p) => p.comb),
    delta: deltas?.comb,
  };
  const hrsCard = {
    label: 'Hrs ECM',
    value: fmtCompact(s?.hrs_ecm),
    exact: fmtInt(s?.hrs_ecm),
    trend: series.map((p) => p.hrs_ecm),
    delta: deltas?.hrs,
  };
  const vehiculosCard = {
    label: 'Vehículos',
    value: s ? String(s.n_vehiculos) : '—',
    exact: s ? String(s.n_vehiculos) : '—',
    trend: undefined,
    delta: deltas?.veh,
  };
  const kpiCards = allVocacional
    ? [consumoPorHoraCard, hrsCard, combCard, kmsCard, rendimientoCard, vehiculosCard]
    : [rendimientoCard, kmsCard, combCard, hrsCard, consumoPorHoraCard, vehiculosCard];

  return (
    <>
      {hasGas && hasLiquid && (
        <div
          className="border-border bg-muted/40 mb-4 inline-flex rounded-lg border p-1"
          role="group"
          aria-label="Tipo de combustible"
        >
          {(
            [
              ['liquid', 'Líquido (gal)'],
              ['gas', 'Gas (m³)'],
            ] as const
          ).map(([kind, label]) => (
            <button
              key={kind}
              type="button"
              onClick={() => {
                if (kind !== fuelKind) {
                  onFuelKindChange(kind);
                  onCross({});
                }
              }}
              aria-pressed={fuelKind === kind}
              className={`rounded-md px-3 py-1.5 text-sm font-semibold transition ${
                fuelKind === kind
                  ? 'bg-card text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      <section className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-heading text-lg font-extrabold">
              Rendimiento, consumo y operación
            </h2>
            <p className="text-muted-foreground text-xs">
              Distancias, horas, combustible, rendimiento y vehículos del periodo filtrado.
            </p>
          </div>
          {granularity === 'monthly' && (
            <div className="bg-muted/40 flex items-center gap-1.5 rounded-lg border p-1">
              <button
                type="button"
                onClick={() => setCompareMode('step')}
                className={`rounded px-2.5 py-1 text-xs font-semibold transition ${
                  compareMode === 'step'
                    ? 'bg-card text-foreground shadow-xs'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
                title="Mes vs anterior: Compara los días transcurridos del mes actual contra los mismos días del mes anterior"
              >
                Mes vs anterior
              </button>
              <button
                type="button"
                onClick={() => setCompareMode('shift')}
                className={`rounded px-2.5 py-1 text-xs font-semibold transition ${
                  compareMode === 'shift'
                    ? 'bg-card text-foreground shadow-xs'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
                title="Periodo vs anterior: Compara el rango total seleccionado contra el periodo anterior de igual duración"
              >
                Periodo vs anterior
              </button>
            </div>
          )}
        </div>

        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          {/*
            Orden por importancia para quien lee el reporte: primero el
            rendimiento, que es la medida de la que trata la pantalla, y detrás
            sus dos componentes —lo recorrido y lo consumido—, que son los que
            lo explican. Las horas, el consumo por hora y el conteo de vehículos
            son contexto y van después.

            En una flota vocacional el rendimiento es el consumo por hora y
            sus componentes son las horas y lo consumido; km/gal pasa a ser
            contexto. El orden lo decide `kpiCards`, y `index` —que solo
            escalona la animación de volteo— sigue ese mismo orden o la
            cascada saldría desordenada.
          */}
          {kpiCards.map((card, i) => (
            <KpiCard
              key={card.label}
              index={i}
              isFlipped={isKpiFlipped}
              onClick={toggleKpiFlip}
              label={card.label}
              value={card.value}
              exact={card.exact}
              trend={card.trend}
              delta={card.delta}
            />
          ))}
        </div>

        {/* Comparativo por grupo interno del cliente (solo con una flota en
            alcance y con grupos; el componente se oculta solo). */}
        <div className="mb-6">
          <GroupComparisonCard
            title={groupRendimiento.title}
            subtitle={groupRendimiento.subtitle}
            rows={groupRows}
            derive={groupRendimiento.derive}
            format={(value) => fmt(value, 2)}
            detail={(t) =>
              `${fmtInt(t.n_vehiculos ?? 0)} placas · ${fmtInt(t.n_registros ?? 0)} registros`
            }
            reference={groupRendimiento.reference}
            onSelectGroup={onSelectGroup}
            isLoading={porGrupoQ.isLoading}
          />
        </div>

        {/* Consumo (barras) + rendimiento (líneas, eje derecho). */}
        <div>
          <ComposedChartCard
            key={`${fuelKind}-${allVocacional ? 'combustible-vocacional' : 'combustible-comercial'}`}
            title={eficienciaTitle}
            subtitle="Clic en un periodo para filtrar por fecha"
            data={series}
            xKey="label"
            isLoading={seriesQ.isLoading}
            isError={seriesQ.isError}
            onBucketClick={pickBucket}
            selectedBucket={selectedBucket}
            showValueLabels={false}
            highlightLineExtremes
            defaultHiddenSeries={[allVocacional ? efficiencyKey : rateKey]}
            bars={[
              {
                key: 'comb',
                name: `Comb. (${fuelUnit})`,
                color: CHART_COLORS.gray,
                opacity: 0.65,
                format: (v) => fmt(v),
              },
            ]}
            lines={[
              {
                key: efficiencyKey,
                name: `km/${fuelUnit}`,
                color: CHART_COLORS.blue,
                axis: 'right',
                format: (v) => fmt(v, 2),
              },
              {
                key: rateKey,
                name: `${fuelUnit}/h`,
                color: CHART_TONES.yellowDark,
                axis: 'right',
                format: (v) => fmt(v, 2),
              },
            ]}
          />
        </div>

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {/* Volúmenes → barras agrupadas. */}
          <ComposedChartCard
            title="Distancia y horas"
            subtitle="Kms y horas de motor (ECM) por periodo"
            data={series}
            xKey="label"
            isLoading={seriesQ.isLoading}
            isError={seriesQ.isError}
            onBucketClick={pickBucket}
            selectedBucket={selectedBucket}
            showValueLabels={false}
            highlightLineExtremes
            bars={[
              {
                key: 'kms_ecm',
                name: 'Kms ECM',
                color: CHART_COLORS.blue,
                format: (v) => fmt(v),
              },
              {
                key: 'hrs_ecm',
                name: 'Horas ECM',
                color: CHART_TONES.yellowDark,
                axis: 'right',
                format: (v) => fmt(v),
              },
            ]}
          />
          <ComposedChartCard
            key={`ecm-gps-${allVocacional ? 'vocacional' : 'comercial'}`}
            title={
              allVocacional ? 'Comparativo de horas ECM vs GPS' : 'Comparativo de Kms ECM vs GPS'
            }
            subtitle={
              allVocacional
                ? 'Horas de operación por fuente; GPS totalizado desde los datos diarios'
                : 'Kilómetros por fuente; GPS totalizado desde los datos diarios'
            }
            data={series}
            xKey="label"
            isLoading={seriesQ.isLoading}
            isError={seriesQ.isError}
            onBucketClick={pickBucket}
            selectedBucket={selectedBucket}
            showValueLabels={false}
            highlightLineExtremes
            bars={[
              {
                key: allVocacional ? 'hrs_ecm' : 'kms_ecm',
                name: allVocacional ? 'Horas ECM' : 'Kms ECM',
                color: CHART_COLORS.blue,
                format: (v) => fmt(v),
              },
              {
                key: allVocacional ? 'hrs_gps' : 'kms_gps',
                name: allVocacional ? 'Horas GPS' : 'Kms GPS',
                color: CHART_TONES.limeDark,
                format: (v) => fmt(v),
              },
            ]}
          />
        </div>

        {/* Ranking por vehículo */}
        <div>
          <BarChartCard
            title="Ranking por vehículo"
            subtitle={
              vehicleSelected
                ? 'Placa filtrada — clic en la misma barra para quitar el filtro'
                : `${rankLimit === 0 ? 'Todos' : 'Top 10'} en el periodo — clic en una barra para filtrar por placa`
            }
            data={rankingData}
            categoryKey="label"
            valueKey="value"
            valueName={rankMeta.label}
            categoryLabelAlign="start"
            color={CHART_COLORS.blue}
            isLoading={rankingQ.isLoading}
            isError={rankingQ.isError}
            format={(v) => (rankMeta.pct ? `${fmt(v, 1)} %` : fmt(v, rankMeta.dec ?? 1))}
            selectedId={cross.vehicleId ?? null}
            onBarClick={(item) => {
              const id = item.id;
              const label = item.label;
              if (typeof id === 'string' && id) {
                pickVehicle(id, typeof label === 'string' ? label : id);
              }
            }}
            action={
              <div className="flex flex-wrap items-center justify-end gap-2">
                <select
                  className="border-input bg-background h-8 rounded-md border px-2 text-xs"
                  value={effectiveRankMetric}
                  onChange={(e) => setRankMetric(e.target.value as RankingMetric)}
                  aria-label="Métrica de ranking"
                >
                  {rankingMetrics.map((m) => (
                    <option key={m.value} value={m.value}>
                      {m.label}
                    </option>
                  ))}
                </select>
                <select
                  className="border-input bg-background h-8 rounded-md border px-2 text-xs"
                  value={rankLimit}
                  onChange={(e) => setRankLimit(Number(e.target.value) as 10 | 0)}
                  aria-label="Cantidad de vehículos"
                >
                  <option value={10}>Top 10</option>
                  <option value={0}>Todos</option>
                </select>
                <select
                  className="border-input bg-background h-8 rounded-md border px-2 text-xs"
                  value={rankSortOrder}
                  onChange={(e) => setRankSortOrder(e.target.value as 'asc' | 'desc')}
                  aria-label="Orden del ranking"
                >
                  <option value="desc">Mayor a menor</option>
                  <option value="asc">Menor a mayor</option>
                </select>
              </div>
            }
          />
        </div>
      </section>

      <section className="space-y-4 border-t pt-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-heading text-lg font-extrabold">Velocidad y ralentí</h2>
            <p className="text-muted-foreground text-xs">
              Velocidad promedio, tiempo y consumo asociados al ralentí del periodo filtrado.
            </p>
          </div>
          {granularity === 'monthly' && (
            <div className="bg-muted/40 flex items-center gap-1.5 rounded-lg border p-1">
              <button
                type="button"
                onClick={() => setCompareMode('step')}
                className={`rounded px-2.5 py-1 text-xs font-semibold transition ${
                  compareMode === 'step'
                    ? 'bg-card text-foreground shadow-xs'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
                title="Mes vs anterior: Compara los días transcurridos del mes actual contra los mismos días del mes anterior"
              >
                Mes vs anterior
              </button>
              <button
                type="button"
                onClick={() => setCompareMode('shift')}
                className={`rounded px-2.5 py-1 text-xs font-semibold transition ${
                  compareMode === 'shift'
                    ? 'bg-card text-foreground shadow-xs'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
                title="Periodo vs anterior: Compara el rango total seleccionado contra el periodo anterior de igual duración"
              >
                Periodo vs anterior
              </button>
            </div>
          )}
        </div>

        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <KpiCard
            index={0}
            isFlipped={isKpiFlipped}
            onClick={toggleKpiFlip}
            label="Vel. prom."
            value={`${fmt(s?.velocidad_promedio, 1)} km/h`}
            exact={fmt(s?.velocidad_promedio, 1)}
            trend={series.map((p) => p.velocidad_promedio)}
            delta={deltas?.vel}
          />
          <KpiCard
            index={1}
            isFlipped={isKpiFlipped}
            onClick={toggleKpiFlip}
            label="% Ralentí"
            value={fmtPct(s?.pct_ralenti)}
            exact={fmtPct(s?.pct_ralenti)}
            trend={series.map((p) => p.pct_ralenti)}
            delta={deltas?.pctRalenti}
          />
          <KpiCard
            index={2}
            isFlipped={isKpiFlipped}
            onClick={toggleKpiFlip}
            label="Consumo ralentí"
            value={fmtCompact(s?.comb_ralenti)}
            exact={fmtInt(s?.comb_ralenti)}
            trend={series.map((p) => p.comb_ralenti)}
            delta={deltas?.combRalenti}
          />
          <KpiCard
            index={3}
            isFlipped={isKpiFlipped}
            onClick={toggleKpiFlip}
            label={`Ralentí ${fuelUnit}/h`}
            value={fmt(idleFuelPerHour, 2)}
            exact={fmt(idleFuelPerHour, 2)}
            trend={series.map((p) => (isGas ? p.m3_hr_ralenti : p.gal_hr_ralenti))}
            delta={deltas?.idleRate}
          />
        </div>

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <ComposedChartCard
            title="Velocidad y ralentí"
            subtitle="% ralentí (eje derecho); la velocidad promedio se activa desde la leyenda"
            data={series}
            xKey="label"
            isLoading={seriesQ.isLoading}
            onBucketClick={pickBucket}
            selectedBucket={selectedBucket}
            showValueLabels={false}
            highlightLineExtremes
            // La velocidad arranca oculta: en flotas vocacionales o urbanas su
            // escala aplana la lectura del ralentí, que es lo que se viene a ver.
            defaultHiddenSeries={['velocidad_promedio']}
            lines={[
              {
                key: 'velocidad_promedio',
                name: 'Vel. (km/h)',
                color: CHART_TONES.grayMuted,
                format: (v) => fmt(v),
              },
              {
                key: 'pct_ralenti',
                name: '% Ralentí',
                color: CHART_TONES.yellowDark,
                axis: 'right',
                format: (v) => fmtPct(v),
              },
            ]}
          />
          <ComposedChartCard
            title="Consumo en ralentí"
            subtitle={`${isGas ? 'Metros cúbicos' : 'Galones'} consumidos y tasa global por periodo`}
            data={series}
            xKey="label"
            isLoading={seriesQ.isLoading}
            onBucketClick={pickBucket}
            selectedBucket={selectedBucket}
            showValueLabels={false}
            highlightLineExtremes
            bars={[
              {
                key: 'comb_ralenti',
                name: `Comb. ralentí (${fuelUnit})`,
                color: CHART_COLORS.yellow,
                format: (v) => fmt(v),
              },
            ]}
            lines={[
              {
                key: idleRateKey,
                name: `Ralentí (${fuelUnit}/h)`,
                color: CHART_COLORS.red,
                axis: 'right',
                format: (v) => fmt(v, 2),
              },
            ]}
          />
        </div>
      </section>

      {/* Sección del Detalle Diario (Acordeón, Búsqueda, Skeletons y Paginación Enriquecida) */}
      <div className="border-border bg-card mt-6 overflow-hidden rounded-lg border transition-all duration-200">
        <button
          type="button"
          onClick={() => setShowDetail((prev) => !prev)}
          className="hover:bg-muted/40 flex w-full cursor-pointer items-center justify-between p-4 text-left transition-colors"
        >
          <div className="flex items-center gap-3">
            <div className="bg-muted text-foreground flex h-9 w-9 items-center justify-center rounded-lg">
              <Calendar className="h-4 w-4" />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-heading text-foreground text-sm font-bold">
                Detalle Diario de Operación y Combustible
              </h2>
              {showDetail ? (
                dailyQ.isFetching ? (
                  <Badge
                    variant="outline"
                    className="text-primary border-primary/30 bg-primary/5 inline-flex items-center gap-1.5 py-0.5 text-xs font-medium"
                  >
                    <Loader2 className="h-3 w-3 animate-spin" />
                    Actualizando registros...
                  </Badge>
                ) : (
                  <Badge variant="outline" className="font-mono text-xs font-bold">
                    {(dailyQ.data?.total ?? 0).toLocaleString('es-CO')} registros
                  </Badge>
                )
              ) : (
                <span className="text-muted-foreground flex items-center gap-1 text-xs font-medium">
                  ·{' '}
                  <span className="underline underline-offset-2">
                    clic para desplegar y consultar
                  </span>
                </span>
              )}
              {cross.bucket && (
                <Badge variant="info" className="text-xs">
                  Filtro: {granularity === 'daily' ? 'día' : 'mes'} {cross.bucket.label}
                </Badge>
              )}
              {cross.vehicleId && (
                <Badge variant="info" className="text-xs">
                  Placa: {cross.vehicleLabel ?? cross.vehicleId}
                </Badge>
              )}
              {consumerFilters.date_from && (
                <span className="text-muted-foreground text-xs font-medium">
                  · {consumerFilters.date_from} al{' '}
                  {consumerFilters.date_to || consumerFilters.date_from}
                </span>
              )}
            </div>
          </div>

          <span className="text-muted-foreground hover:bg-muted flex h-7 w-7 items-center justify-center rounded-md">
            {showDetail ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </span>
        </button>

        {showDetail && (
          <div className="border-border space-y-3 border-t p-4 pt-2">
            {/* Barra de Búsqueda Rápida y Botón de Exportar */}
            <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
              <div className="flex max-w-md flex-1 items-center gap-2">
                <div className="relative flex-1">
                  <Search className="text-muted-foreground pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2" />
                  <Input
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Filtrar por placa, motor o fecha..."
                    className="bg-background h-8 pl-8 text-xs"
                  />
                </div>

                {searchQuery && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setSearchQuery('')}
                    className="text-muted-foreground hover:text-foreground h-8 cursor-pointer px-2 text-xs"
                  >
                    Limpiar
                  </Button>
                )}
              </div>

              <Button
                variant="outline"
                size="sm"
                onClick={exportExcel}
                disabled={isExporting || dailyQ.isLoading || (dailyQ.data?.total ?? 0) === 0}
                className="border-border/80 hover:bg-muted h-8 cursor-pointer gap-1.5 px-3 text-xs font-medium"
              >
                {isExporting ? (
                  <>
                    <Loader2 className="text-primary h-3.5 w-3.5 animate-spin" />
                    <span>Exportando...</span>
                  </>
                ) : (
                  <>
                    <Download className="text-primary h-3.5 w-3.5" />
                    <span>Exportar Excel</span>
                  </>
                )}
              </Button>
            </div>

            {dailyQ.isError && (
              <p className="text-destructive text-sm">Error al cargar datos. Intenta de nuevo.</p>
            )}

            {dailyQ.isLoading ? (
              <div className="border-border bg-card overflow-hidden rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow className="bg-muted/40 hover:bg-muted/40">
                      <TableHead className="text-xs font-semibold">Fecha</TableHead>
                      <TableHead className="text-xs font-semibold">Placa</TableHead>
                      <TableHead className="text-xs font-semibold">Motor</TableHead>
                      <TableHead className="text-right text-xs font-semibold">Kms ECM</TableHead>
                      <TableHead className="text-right text-xs font-semibold">
                        Comb ({detailFuelUnit})
                      </TableHead>
                      <TableHead className="text-right text-xs font-semibold">
                        {detailEfficiencyLabel}
                      </TableHead>
                      <TableHead className="text-right text-xs font-semibold">
                        {detailRateLabel}
                      </TableHead>
                      <TableHead className="text-right text-xs font-semibold">
                        Comb Ralentí ({detailFuelUnit})
                      </TableHead>
                      <TableHead className="text-right text-xs font-semibold">
                        Horas Ralentí
                      </TableHead>
                      <TableHead className="text-right text-xs font-semibold">
                        Velocidad Promedio (Km/h)
                      </TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {Array.from({ length: 8 }).map((_, i) => (
                      <TableRow key={i} className="animate-pulse">
                        <TableCell>
                          <Skeleton className="h-4 w-20 rounded" />
                        </TableCell>
                        <TableCell>
                          <Skeleton className="h-4 w-16 rounded" />
                        </TableCell>
                        <TableCell>
                          <Skeleton className="h-4 w-14 rounded-full" />
                        </TableCell>
                        <TableCell className="text-right">
                          <Skeleton className="ml-auto h-4 w-16 rounded" />
                        </TableCell>
                        <TableCell className="text-right">
                          <Skeleton className="ml-auto h-4 w-14 rounded" />
                        </TableCell>
                        <TableCell className="text-right">
                          <Skeleton className="ml-auto h-4 w-12 rounded" />
                        </TableCell>
                        <TableCell className="text-right">
                          <Skeleton className="ml-auto h-4 w-12 rounded" />
                        </TableCell>
                        <TableCell className="text-right">
                          <Skeleton className="ml-auto h-4 w-12 rounded" />
                        </TableCell>
                        <TableCell className="text-right">
                          <Skeleton className="ml-auto h-4 w-10 rounded" />
                        </TableCell>
                        <TableCell className="text-right">
                          <Skeleton className="ml-auto h-4 w-10 rounded" />
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
                    'border-border bg-card overflow-hidden rounded-lg border transition-opacity duration-150',
                    dailyQ.isFetching && 'pointer-events-none opacity-60',
                  )}
                >
                  <Table>
                    <TableHeader>
                      <TableRow className="bg-muted/40 hover:bg-muted/40">
                        <TableHead className="text-xs font-semibold">Fecha</TableHead>
                        <TableHead className="text-xs font-semibold">Placa</TableHead>
                        <TableHead className="text-xs font-semibold">Motor</TableHead>
                        <TableHead className="text-right text-xs font-semibold">Kms ECM</TableHead>
                        <TableHead className="text-right text-xs font-semibold">
                          Comb ({detailFuelUnit})
                        </TableHead>
                        <TableHead className="text-right text-xs font-semibold">
                          {detailEfficiencyLabel}
                        </TableHead>
                        <TableHead className="text-right text-xs font-semibold">
                          {detailRateLabel}
                        </TableHead>
                        <TableHead className="text-right text-xs font-semibold">
                          Comb Ralentí ({detailFuelUnit})
                        </TableHead>
                        <TableHead className="text-right text-xs font-semibold">
                          Horas Ralentí
                        </TableHead>
                        <TableHead className="text-right text-xs font-semibold">
                          Velocidad Promedio (Km/h)
                        </TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {displayedItems.length === 0 && (
                        <TableRow>
                          <TableCell
                            colSpan={10}
                            className="text-muted-foreground py-6 text-center"
                          >
                            Sin registros para los filtros seleccionados.
                          </TableCell>
                        </TableRow>
                      )}
                      {displayedItems.map((row) => (
                        <TableRow
                          key={row.fact_row_id}
                          className="hover:bg-muted/50 transition-colors"
                        >
                          <TableCell className="text-foreground whitespace-nowrap font-mono text-xs">
                            {row.fecha ?? '—'}
                          </TableCell>
                          <TableCell className="text-foreground font-mono text-xs font-bold">
                            {row.placa ?? '—'}
                          </TableCell>
                          <TableCell>
                            {row.motor_type ? (
                              <Badge variant="outline" className="font-mono text-[11px]">
                                {row.motor_type}
                              </Badge>
                            ) : (
                              <span className="text-muted-foreground/40 text-xs">—</span>
                            )}
                          </TableCell>
                          <TableCell className="text-foreground text-right font-mono text-xs">
                            {fmt(row.kms_ecm)}
                          </TableCell>
                          <TableCell className="text-foreground text-right font-mono text-xs">
                            {fmt(row.comb)}
                          </TableCell>
                          <TableCell className="text-foreground text-right font-mono text-xs font-semibold">
                            {fmt(isGas ? row.km_m3 : row.km_gal, 2)}
                          </TableCell>
                          <TableCell className="text-foreground text-right font-mono text-xs">
                            {fmt(isGas ? row.m3_hr : row.gal_hr, 2)}
                          </TableCell>
                          <TableCell className="text-foreground text-right font-mono text-xs">
                            {fmt(row.comb_ralenti)}
                          </TableCell>
                          <TableCell className="text-foreground text-right font-mono text-xs">
                            {fmt(row.ralenti)}
                          </TableCell>
                          <TableCell className="text-foreground text-right font-mono text-xs">
                            {fmt(row.velocidad_promedio)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>

                <DetailPagination
                  total={dailyQ.data?.total ?? 0}
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
