'use client';

import * as React from 'react';
import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';

import { ChartCard } from '@/components/charts/chart-card';
import {
  CHART_ANIMATION_DURATION,
  CHART_ANIMATION_EASING,
  SERIES_PALETTE,
  tooltipStyle,
} from '@/components/charts/chart-theme';

interface DonutChartCardProps {
  title: string;
  subtitle?: string;
  data: Array<{ label: string; value: number }>;
  /** Colores explícitos por segmento (en orden); si falta, usa la paleta. */
  colors?: string[];
  format?: (v: number) => string;
  isLoading?: boolean;
  action?: React.ReactNode;
  height?: number;
  /** Clic en un segmento; recibe su `label`. */
  onSegmentClick?: (label: string) => void;
  /** Segmento seleccionado: los demás se atenúan. */
  selectedLabel?: string | null;
}

export function DonutChartCard({
  title,
  subtitle,
  data,
  colors,
  format,
  isLoading,
  action,
  height,
  onSegmentClick,
  selectedLabel,
}: DonutChartCardProps) {
  const hasSelection = selectedLabel != null && selectedLabel !== '';
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
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            nameKey="label"
            innerRadius="55%"
            outerRadius="80%"
            paddingAngle={2}
            animationDuration={CHART_ANIMATION_DURATION}
            animationEasing={CHART_ANIMATION_EASING as never}
            cursor={onSegmentClick ? 'pointer' : undefined}
            onClick={
              onSegmentClick
                ? (entry: { name?: string; label?: string; payload?: { label?: string } }) => {
                    const lbl = entry?.payload?.label ?? entry?.name ?? entry?.label;
                    if (lbl) onSegmentClick(String(lbl));
                  }
                : undefined
            }
          >
            {data.map((d, i) => {
              const dim = hasSelection && d.label !== selectedLabel;
              return (
                <Cell
                  key={i}
                  fill={colors?.[i] ?? SERIES_PALETTE[i % SERIES_PALETTE.length]}
                  fillOpacity={dim ? 0.3 : 1}
                />
              );
            })}
          </Pie>
          <Tooltip
            contentStyle={tooltipStyle}
            formatter={(value: number, name: string) => [format ? format(value) : value, name]}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
        </PieChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}
