'use client';

import * as React from 'react';

import { ComposedChartCard, type ComposedSeries } from '@/components/charts/composed-chart-card';
import { DonutChartCard } from '@/components/charts/donut-chart-card';
import { LineChartCard } from '@/components/charts/line-chart-card';
import { CHART_COLORS, CHART_TONES } from '@/components/charts/chart-theme';
import { KpiCard, fmt, fmtInt } from '@/app/(app)/reportes/shared';
import { RankingList, fmtHours, fmtPct100 } from '@/app/(app)/mantenimiento/shared';
import {
  useConfiabilidad,
  useConfiabilidadTimeseries,
  useHistoricoProgramaciones,
  useOrdenes,
  useOrdenesList,
  useOrdenesTimeseries,
  usePreventivo,
  usePreventivoTimeseries,
  useProximasProgramaciones,
  useRankings,
  type MttoFilters,
  type OrdenesTypeTimePoint,
  type RankingGroup,
} from '@/lib/mantenimiento';
import { OrdenesTable } from './ordenes-table';
import { ProgramacionesTable } from './proximas-table';
import { TiemposTallerDashboard } from './tiempos-taller-table';

/** Colores semánticos estables para que un tipo conserve su identidad visual. */
const ORDENES_TYPE_COLORS: Record<string, string> = {
  programado: '#2f855a',
  'no programado': '#c88700',
  garantía: CHART_TONES.grayMuted,
  garantia: CHART_TONES.grayMuted,
  'daño operativo': '#d97706',
  'dano operativo': '#d97706',
  varado: CHART_TONES.redMuted,
  otros: CHART_TONES.creamDark,
};

function ordenTypeColor(type: string): string {
  return ORDENES_TYPE_COLORS[type.trim().toLocaleLowerCase('es')] ?? CHART_COLORS.blue;
}

/**
 * Prepara filas para barras apiladas por tipo de OT: toma los 5 tipos con más
 * volumen en el periodo (de `byType`) y agrupa el resto en "Otros" para que
 * el gráfico no reviente de series (recharts + SERIES_PALETTE alcanzan para 6).
 */
function buildOrdenesStack(ts: OrdenesTypeTimePoint[] | undefined): {
  rows: Record<string, number | string>[];
  bars: ComposedSeries[];
  lines: ComposedSeries[];
} {
  const typeTotals = new Map<string, number>();
  for (const point of ts ?? []) {
    for (const [type, count] of Object.entries(point.counts)) {
      typeTotals.set(type, (typeTotals.get(type) ?? 0) + count);
    }
  }
  const topTypes = [...typeTotals.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5)
    .map(([type]) => type);
  const hasOtros = typeTotals.size > topTypes.length;

  const rows = (ts ?? []).map((point) => {
    const row: Record<string, number | string> = { label: point.label, month: point.month };
    let otros = 0;
    for (const t of topTypes) row[t] = 0;
    for (const [type, count] of Object.entries(point.counts)) {
      if (topTypes.includes(type)) row[type] = (row[type] as number) + count;
      else otros += count;
    }
    if (hasOtros) row.Otros = otros;
    row.total = Object.values(point.counts).reduce((sum, count) => sum + count, 0);
    return row;
  });

  const names = hasOtros ? [...topTypes, 'Otros'] : topTypes;
  const bars: ComposedSeries[] = names.map((name) => ({
    key: name,
    name,
    color: ordenTypeColor(name),
    stackId: 'ot',
  }));
  return {
    rows,
    bars,
    lines: [
      {
        key: 'total',
        name: 'Total OTs',
        color: CHART_COLORS.gray,
        format: fmtInt,
      },
    ],
  };
}

/** Grid de los 5 rankings de una dimensión (placa o flota). */
function RankingSection({ group, dim }: { group?: RankingGroup; dim: string }) {
  return (
    <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      <RankingList
        title={`Peor puntualidad (${dim})`}
        rows={group?.peorPuntualidad ?? []}
        format={fmtPct100}
        emptyText="Sin planes en la selección."
      />
      <RankingList
        title={`Menor cumplimiento (${dim})`}
        rows={group?.menorCumplimiento ?? []}
        format={fmtPct100}
        emptyText="Sin planes en la selección."
      />
      <RankingList
        title={`Más fallas (${dim})`}
        rows={group?.masFallas ?? []}
        format={fmtInt}
        emptyText="Sin fallas registradas."
      />
      <RankingList
        title={`MTTR más extenso (${dim})`}
        rows={group?.mttrMasLargo ?? []}
        format={fmtHours}
        emptyText="Sin MTTR calculable."
      />
      <RankingList
        title={`MTTR más corto (${dim})`}
        rows={group?.mttrMasCorto ?? []}
        format={fmtHours}
        emptyText="Sin MTTR calculable."
      />
    </section>
  );
}

/** Cards de cabecera comunes a los tabs de programación/confiabilidad. */
export function ProgramacionKpis({ filters }: { filters: MttoFilters }) {
  const prev = usePreventivo(filters);
  const conf = useConfiabilidad(filters);
  const p = prev.data;
  const c = conf.data;

  return (
    <section className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
      <KpiCard
        label="Ejecución Preventivo"
        value={fmtPct100(p?.executionPct)}
        hint={`${fmtInt(p?.dueCount)} planes en el periodo`}
      />
      <KpiCard label="Puntualidad" value={fmtPct100(p?.onTimePct)} hint="Ejecutados a tiempo" />
      <KpiCard
        label="Fallas / Correctivos"
        value={fmtInt(c?.failureCount)}
        hint={`${fmtInt(c?.vehiclesWithFailures)} vehículos con falla`}
      />
      <KpiCard
        label="MTTR"
        value={fmtHours(c?.mttrHoursAvg)}
        hint={`Mediana ${fmtHours(c?.mttrHoursMedian)}`}
      />
      <KpiCard
        label="MTBF"
        value={fmtHours(c?.mtbfHoursAvg)}
        hint={`Mediana ${fmtHours(c?.mtbfHoursMedian)}`}
      />
    </section>
  );
}

/** Rankings por placa y (si el scope abarca >=2 flotas) por flota. */
export function ProgramacionRankings({ filters }: { filters: MttoFilters }) {
  const rk = useRankings(filters);

  return (
    <>
      <div className="space-y-2">
        <h2 className="font-heading text-sm font-bold tracking-tight">Ranking por vehículo</h2>
        <RankingSection group={rk.data?.byPlaca} dim="placa" />
      </div>

      {(rk.data?.fleetCount ?? 0) >= 2 && (
        <div className="space-y-2">
          <h2 className="font-heading text-sm font-bold tracking-tight">Ranking por flota</h2>
          <RankingSection group={rk.data?.byFleet} dim="flota" />
        </div>
      )}
    </>
  );
}

export function PreventivoTab({
  filters,
  activeFilters,
  selectedMonth,
  onMonthChange,
}: {
  filters: MttoFilters;
  activeFilters: MttoFilters;
  selectedMonth: string | null;
  onMonthChange: (month: string | null) => void;
}) {
  const prev = usePreventivo(activeFilters);
  const prevTs = usePreventivoTimeseries(filters);
  const selectedLabel = prevTs.data?.find((point) => point.month === selectedMonth)?.label;

  return (
    <div className="space-y-3">
      <ComposedChartCard
        title="Cumplimiento preventivo"
        subtitle="Ejecución y puntualidad por mes"
        data={prevTs.data ?? []}
        xKey="label"
        isLoading={prevTs.isLoading}
        selectedBucket={selectedLabel ?? null}
        onBucketClick={(_label, payload) => {
          const month = typeof payload?.month === 'string' ? payload.month : null;
          if (month) onMonthChange(selectedMonth === month ? null : month);
        }}
        bars={[
          { key: 'ejecucion', name: 'Ejecución %', color: CHART_COLORS.blue, format: fmtPct100 },
        ]}
        lines={[
          {
            key: 'puntualidad',
            name: 'Puntualidad %',
            color: CHART_COLORS.lime,
            format: fmtPct100,
          },
        ]}
      />
      <DonutChartCard
        title="Planes por estado"
        subtitle="Distribución del periodo"
        isLoading={prev.isLoading}
        data={Object.entries(prev.data?.byStatus ?? {}).map(([label, value]) => ({ label, value }))}
        format={fmtInt}
      />
    </div>
  );
}

export function ProximasTab({ filters }: { filters: MttoFilters }) {
  const proximas = useProximasProgramaciones(filters.placas);
  return <ProgramacionesTable rows={proximas.data ?? []} isLoading={proximas.isLoading} />;
}

export function HistoricoTab({ filters }: { filters: MttoFilters }) {
  const historico = useHistoricoProgramaciones(filters);
  return (
    <ProgramacionesTable
      rows={historico.data ?? []}
      isLoading={historico.isLoading}
      variant="historico"
    />
  );
}

export function ConfiabilidadTab({
  filters,
  selectedMonth,
  onMonthChange,
}: {
  filters: MttoFilters;
  selectedMonth: string | null;
  onMonthChange: (month: string | null) => void;
}) {
  const confTs = useConfiabilidadTimeseries(filters);
  const selectedLabel = confTs.data?.find((point) => point.month === selectedMonth)?.label;
  const pickMonth = (_label: string, payload?: Record<string, unknown>) => {
    const month = typeof payload?.month === 'string' ? payload.month : null;
    if (month) onMonthChange(selectedMonth === month ? null : month);
  };

  return (
    <div className="grid gap-3 lg:grid-cols-3">
      <div className="lg:col-span-1">
        <ComposedChartCard
          title="Fallas por mes"
          subtitle="Cantidad de fallas detectadas"
          data={confTs.data ?? []}
          xKey="label"
          isLoading={confTs.isLoading}
          selectedBucket={selectedLabel ?? null}
          onBucketClick={pickMonth}
          bars={[{ key: 'fallas', name: 'Fallas', color: CHART_COLORS.red, format: fmtInt }]}
          height={320}
        />
      </div>
      <div className="lg:col-span-2">
        <LineChartCard
          title="Evolución de MTTR y MTBF"
          subtitle="MTTR solo considera fallas con cierre técnico; MTBF requiere dos fallas del mismo vehículo"
          data={confTs.data ?? []}
          xKey="label"
          isLoading={confTs.isLoading}
          selectedBucket={selectedLabel ?? null}
          onBucketClick={pickMonth}
          series={[
            {
              key: 'mttr',
              name: 'MTTR (h)',
              color: CHART_COLORS.blue,
              format: (v) => fmt(v, 1),
            },
            {
              key: 'mtbf',
              name: 'MTBF (h)',
              color: CHART_COLORS.lime,
              format: (v) => fmt(v, 1),
            },
          ]}
          height={320}
        />
      </div>
    </div>
  );
}

export function OrdenesTab({
  filters,
  activeFilters,
  selectedMonth,
  onMonthChange,
}: {
  filters: MttoFilters;
  activeFilters: MttoFilters;
  selectedMonth: string | null;
  onMonthChange: (month: string | null) => void;
}) {
  const ord = useOrdenes(activeFilters);
  const ordTs = useOrdenesTimeseries(filters);
  const ordList = useOrdenesList(activeFilters);
  const selectedLabel = ordTs.data?.find((point) => point.month === selectedMonth)?.label;
  const ordStack = React.useMemo(() => buildOrdenesStack(ordTs.data), [ordTs.data]);

  return (
    <div className="space-y-3">
      <section className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
        <KpiCard label="OTs del periodo" value={fmtInt(ord.data?.createdOrStarted)} />
        <KpiCard label="Abiertas al cierre" value={fmtInt(ord.data?.openAtEnd)} />
        <KpiCard label="Abiertas vencidas" value={fmtInt(ord.data?.overdueOpen)} />
        <KpiCard label="Ciclo técnico prom." value={fmtHours(ord.data?.avgTechnicalCycleHours)} />
        <KpiCard
          label="Cierre administrativo"
          value={fmtHours(ord.data?.avgFinalClosureLagHours)}
        />
      </section>
      <ComposedChartCard
        title="Órdenes por mes y tipo"
        subtitle="Evolución mensual: total de OTs y distribución por tipo"
        data={ordStack.rows}
        xKey="label"
        isLoading={ordTs.isLoading}
        selectedBucket={selectedLabel ?? null}
        onBucketClick={(_label, payload) => {
          const month = typeof payload?.month === 'string' ? payload.month : null;
          if (month) onMonthChange(selectedMonth === month ? null : month);
        }}
        bars={ordStack.bars}
        lines={ordStack.lines}
      />
      <div className="grid gap-3 md:grid-cols-2">
        <DonutChartCard
          title="Órdenes por estado"
          isLoading={ord.isLoading}
          data={Object.entries(ord.data?.byStatus ?? {}).map(([label, value]) => ({
            label,
            value,
          }))}
          format={fmtInt}
        />
        <DonutChartCard
          title="Órdenes por tipo"
          isLoading={ord.isLoading}
          data={Object.entries(ord.data?.byType ?? {}).map(([label, value]) => ({ label, value }))}
          colors={Object.keys(ord.data?.byType ?? {}).map(ordenTypeColor)}
          format={fmtInt}
        />
      </div>
      <OrdenesTable rows={ordList.data ?? []} isLoading={ordList.isLoading} />
    </div>
  );
}

export function TiemposTallerTab({
  filters,
  activeFilters,
  selectedMonth,
  onMonthChange,
}: {
  filters: MttoFilters;
  activeFilters: MttoFilters;
  selectedMonth: string | null;
  onMonthChange: (month: string | null) => void;
}) {
  return (
    <TiemposTallerDashboard
      chartFilters={filters}
      activeFilters={activeFilters}
      selectedMonth={selectedMonth}
      onMonthChange={onMonthChange}
    />
  );
}
