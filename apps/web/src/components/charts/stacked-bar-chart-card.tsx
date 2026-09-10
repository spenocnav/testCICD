'use client';

import * as React from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { ChartCard } from '@/components/charts/chart-card';
import type { ChartClickState } from '@/components/charts/line-chart-card';
import {
  AXIS_COLOR,
  CHART_ANIMATION_DURATION,
  CHART_ANIMATION_EASING,
  CHART_COLORS,
  GRID_COLOR,
  tooltipStyle,
} from '@/components/charts/chart-theme';

export interface StackedSeries {
  key: string;
  name: string;
  color: string;
}

interface StackedBarChartCardProps {
  title: string;
  subtitle?: string;
  data: readonly unknown[];
  xKey: string;
  series: StackedSeries[];
  /** Normaliza cada barra a 100% (composición relativa). */
  expand?: boolean;
  format?: (v: number) => string;
  isLoading?: boolean;
  action?: React.ReactNode;
  height?: number;
  /** Clic en un bucket del eje X (fecha). Recibe la etiqueta y la fila de datos. */
  onBucketClick?: (label: string, payload?: Record<string, unknown>) => void;
  /** Bucket seleccionado: se resalta con una línea vertical. */
  selectedBucket?: string | null;
}

/** Barras verticales apiladas; con `expand` cada barra suma 100% (composición). */
export function StackedBarChartCard({
  title,
  subtitle,
  data,
  xKey,
  series,
  expand,
  format,
  isLoading,
  action,
  height,
  onBucketClick,
  selectedBucket,
}: StackedBarChartCardProps) {
  return (
    <ChartCard
      title={title}
      subtitle={subtitle}
      isLoading={isLoading}
      isEmpty={!isLoading && data.length === 0}
      action={action}
      height={height}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data as object[]}
          stackOffset={expand ? 'expand' : 'none'}
          margin={{ top: 8, right: 20, left: 12, bottom: 12 }}
          onClick={
            onBucketClick
              ? (state: ChartClickState) => {
                  if (state?.activeLabel != null)
                    onBucketClick(String(state.activeLabel), state.activePayload?.[0]?.payload);
                }
              : undefined
          }
          style={onBucketClick ? { cursor: 'pointer' } : undefined}
        >
          <CartesianGrid stroke={GRID_COLOR} strokeDasharray="3 3" vertical={false} />
          {selectedBucket != null && selectedBucket !== '' && (
            <ReferenceLine x={selectedBucket} stroke={CHART_COLORS.blue} strokeDasharray="4 2" />
          )}
          <XAxis
            dataKey={xKey}
            tick={{ fontSize: 11, fill: AXIS_COLOR }}
            tickLine={false}
            axisLine={{ stroke: GRID_COLOR }}
            minTickGap={8}
          />
          <YAxis
            tick={{ fontSize: 11, fill: AXIS_COLOR }}
            tickLine={false}
            axisLine={false}
            width={48}
            tickFormatter={expand ? (v: number) => `${Math.round(v * 100)}%` : undefined}
          />
          <Tooltip
            contentStyle={tooltipStyle}
            cursor={{ fill: 'rgba(0,0,0,0.04)' }}
            itemSorter={(item) => {
              const index = series.findIndex((entry) => entry.key === String(item.dataKey ?? ''));
              return index < 0 ? 0 : -index;
            }}
            formatter={(value: number, name: string) => [format ? format(value) : value, name]}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          {series.map((s, i) => (
            <Bar
              key={s.key}
              dataKey={s.key}
              name={s.name}
              stackId="a"
              fill={s.color}
              radius={i === series.length - 1 ? [4, 4, 0, 0] : undefined}
              animationDuration={CHART_ANIMATION_DURATION}
              animationEasing={CHART_ANIMATION_EASING as never}
            >
              {data.map((row, index) => {
                const rowLabel =
                  typeof row === 'object' && row !== null
                    ? String((row as Record<string, unknown>)[xKey] ?? '')
                    : '';
                const dimmed =
                  selectedBucket != null && selectedBucket !== '' && rowLabel !== selectedBucket;

                return (
                  <Cell
                    key={`${s.key}-${rowLabel || index}`}
                    fill={s.color}
                    fillOpacity={dimmed ? 0.2 : 1}
                  />
                );
              })}
            </Bar>
          ))}
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}
