'use client';

import * as React from 'react';

import { CHART_COLORS, CHART_TONES } from '@/components/charts/chart-theme';
import { ComposedChartCard } from '@/components/charts/composed-chart-card';
import { GaugeCard } from '@/components/charts/gauge-card';
import { StackedBarChartCard } from '@/components/charts/stacked-bar-chart-card';
import {
  type CrossBucket,
  type ReportesFilters,
  useFactorCarga,
  useOperativosTimeseries,
  usePedalSummary,
} from '@/lib/reportes';

import type { Granularity } from '@/lib/types';
import { fmt, fmtInt } from './shared';

const pctFmt = (v: number) => `${fmt(v, 1)} %`;

// Zonas del gauge de factor de carga (carga del motor, %).
const FACTOR_ZONES = [
  { upto: 30, color: CHART_TONES.yellowDark, label: 'Subutilizado (0–30%)' },
  { upto: 70, color: CHART_TONES.limeDark, label: 'Óptimo (30–70%)' },
  { upto: 100, color: CHART_TONES.redMuted, label: 'Alta carga (70–100%)' },
];

// Recharts pinta la primera serie en la base de la barra. Este orden interno
// produce visualmente, de arriba hacia abajo: Exceso, Consumos, Potencia,
// Balanceado, Económico y Bajo.
const RPM_BANDS = [
  { base: 'pct_rango_bajo', name: 'R. Bajo', color: CHART_COLORS.blue },
  { base: 'pct_rango_economico', name: 'R. Económico', color: CHART_TONES.limeDark },
  { base: 'pct_rango_balanceado', name: 'R. Balanceado', color: CHART_TONES.creamDark },
  { base: 'pct_rango_potencia', name: 'R. Potencia', color: CHART_TONES.yellowDark },
  { base: 'pct_rango_potencia_ineficiente', name: 'R. Consumos', color: CHART_TONES.grayMuted },
  { base: 'pct_exceso_rpm', name: 'Exceso RPM', color: CHART_TONES.redMuted },
] as const;

const RANGE_SERIES_SIN_DESCENSO = RPM_BANDS.map((b) => ({
  key: `${b.base}_sin_descenso`,
  name: b.name,
  color: b.color,
}));
const RANGE_SERIES_DESCENSO = RPM_BANDS.map((b) => ({
  key: `${b.base}_descenso`,
  name: b.name,
  color: b.color,
}));

export function OperativosTab({
  filters,
  granularity,
}: {
  filters: ReportesFilters;
  granularity: Granularity;
}) {
  const [periodFilter, setPeriodFilter] = React.useState<CrossBucket | null>(null);

  React.useEffect(() => setPeriodFilter(null), [filters, granularity]);

  const scopedFilters: ReportesFilters = periodFilter
    ? {
        ...filters,
        date_from: periodFilter.dateFrom,
        date_to: periodFilter.dateTo,
      }
    : filters;

  // Las series permanecen completas para que el periodo seleccionado se vea
  // resaltado y el resto atenuado; los indicadores sí responden al periodo.
  const seriesQ = useOperativosTimeseries(filters, granularity);
  const pedalQ = usePedalSummary(scopedFilters);
  const factorQ = useFactorCarga(scopedFilters, granularity);
  const factorSeriesQ = useFactorCarga(filters, granularity);

  const series = seriesQ.data ?? [];
  const pedal = pedalQ.data;
  const factor = factorQ.data;
  const factorSeries = factorSeriesQ.data;

  const periodLabel = granularity === 'daily' ? 'día' : 'mes';

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

  const selectedBucket = periodFilter?.label ?? null;

  // Puntos porcentuales para las barras de tiempo; los excesos son conteos de eventos.
  const pctData = series.map((m) => ({
    label: m.label,
    periodo: m.periodo,
    exceso:
      m.eventos_rpm_sin_descenso ??
      Math.max((m.eventos_rpm ?? 0) - (m.eventos_rpm_descenso ?? 0), 0),
    descenso: m.eventos_rpm_descenso ?? 0,
    exceso_total: m.eventos_rpm ?? 0,
    bajo: (m.pct_rango_bajo ?? 0) * 100,
    ralenti: (m.pct_ralenti ?? 0) * 100,
  }));

  return (
    <>
      {/* Gauges: posición del pedal + factor de carga */}
      <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <GaugeCard
          title="Posición del pedal"
          subtitle="Presión promedio sobre el acelerador"
          value={pedal?.promedio ?? null}
          hint={pedal ? `${pedal.n_lecturas} lecturas en el periodo` : undefined}
        />
        <GaugeCard
          title="Factor de carga"
          subtitle="Carga promedio del motor"
          value={factor?.promedio ?? null}
          zones={FACTOR_ZONES}
          hint={factor ? `${factor.n_lecturas} registros en el periodo` : undefined}
        />
      </div>

      {periodFilter && (
        <div
          role="status"
          className="border-accent-blue/30 bg-accent-blue/5 text-accent-blue mb-4 flex items-center gap-2 rounded-md border px-3 py-2 text-xs"
        >
          <span className="font-semibold">Filtro de gráficos:</span>
          <span>Periodo: {periodFilter.label}</span>
          <button
            type="button"
            onClick={() => setPeriodFilter(null)}
            className="ml-auto font-semibold underline-offset-2 hover:underline"
          >
            Limpiar
          </button>
        </div>
      )}

      {/* Exceso de RPM (1/3) + distribución de bandas (2/3). */}
      <div className="mb-6 grid grid-cols-1 items-stretch gap-4 lg:grid-cols-3">
        <div className="lg:col-span-1">
          <ComposedChartCard
            title="Exceso de RPM"
            subtitle={`Cantidad de eventos por ${periodLabel} · descenso = factor de carga <5%`}
            data={pctData}
            xKey="label"
            isLoading={seriesQ.isLoading}
            selectedBucket={selectedBucket}
            onBucketClick={pickPeriod}
            bars={[
              {
                key: 'exceso',
                name: 'Exceso RPM fuera de descenso',
                color: CHART_TONES.redMuted,
                format: fmtInt,
                stackId: 'rpm',
              },
              {
                key: 'descenso',
                name: 'Exceso RPM en descenso (<5% carga)',
                color: CHART_TONES.pinkLight,
                format: fmtInt,
                stackId: 'rpm',
                labelKey: 'exceso_total',
              },
            ]}
            height={320}
          />
        </div>

        <div className="lg:col-span-2">
          <StackedBarChartCard
            title="Distribución de bandas de RPM"
            subtitle={`Participación de cada banda por ${periodLabel} (100%, sin descenso)`}
            data={series}
            xKey="label"
            series={RANGE_SERIES_SIN_DESCENSO}
            expand
            format={(v) => `${(v * 100).toFixed(1)} %`}
            isLoading={seriesQ.isLoading}
            selectedBucket={selectedBucket}
            onBucketClick={pickPeriod}
            height={320}
          />
        </div>
      </div>

      {/* Ralentí vs Rango bajo (1/3) + distribución en descenso (2/3). */}
      <div className="mb-6 grid grid-cols-1 items-stretch gap-4 lg:grid-cols-3">
        <div className="lg:col-span-1">
          <ComposedChartCard
            title="Ralentí vs Rango bajo"
            subtitle={`% de tiempo por ${periodLabel} — comparativo`}
            data={pctData}
            xKey="label"
            isLoading={seriesQ.isLoading}
            selectedBucket={selectedBucket}
            onBucketClick={pickPeriod}
            bars={[
              { key: 'bajo', name: '% R. Bajo', color: CHART_COLORS.blue, format: pctFmt },
              {
                key: 'ralenti',
                name: '% T. Ralentí',
                color: CHART_TONES.grayMuted,
                format: pctFmt,
              },
            ]}
            height={320}
          />
        </div>

        <div className="lg:col-span-2">
          <StackedBarChartCard
            title="Distribución de bandas de RPM en descenso"
            subtitle={`Participación de cada banda durante descensos por ${periodLabel} (100%)`}
            data={series}
            xKey="label"
            series={RANGE_SERIES_DESCENSO}
            expand
            format={(v) => `${(v * 100).toFixed(1)} %`}
            isLoading={seriesQ.isLoading}
            selectedBucket={selectedBucket}
            onBucketClick={pickPeriod}
            height={320}
          />
        </div>
      </div>

      {/* Tendencia del factor de carga */}
      <div className="mb-6">
        <ComposedChartCard
          title="Factor de carga"
          subtitle={`Carga promedio del motor por ${periodLabel}`}
          data={factorSeries?.monthly ?? []}
          xKey="label"
          isLoading={factorSeriesQ.isLoading}
          selectedBucket={selectedBucket}
          onBucketClick={pickPeriod}
          bars={[
            {
              key: 'factor_de_carga',
              name: 'Factor de carga',
              color: CHART_COLORS.blue,
              format: pctFmt,
            },
          ]}
          height={280}
        />
      </div>
    </>
  );
}
