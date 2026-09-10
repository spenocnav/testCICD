'use client';

import * as React from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { ChartCard } from '@/components/charts/chart-card';
import {
  AXIS_COLOR,
  CHART_ANIMATION_DURATION,
  CHART_ANIMATION_EASING,
  GRID_COLOR,
  SERIES_PALETTE,
} from '@/components/charts/chart-theme';
import { cn } from '@/lib/utils';

interface BarChartCardProps {
  title: string;
  subtitle?: string;
  data: readonly unknown[];
  /** Categoría (eje Y en horizontal). */
  categoryKey: string;
  /** Valor numérico de la barra. */
  valueKey: string;
  valueName: string;
  format?: (v: number) => string;
  isLoading?: boolean;
  isError?: boolean;
  action?: React.ReactNode;
  height?: number;
  /** Color único para todas las barras; si se omite usa la paleta rotando. */
  color?: string;
  /** Gradiente horizontal para las barras. */
  gradient?: { from: string; to: string; id?: string };
  /** Si se pasa, las barras son clicables y reciben el dato de la fila. */
  onBarClick?: (item: Record<string, unknown>) => void;
  /** Id (row.id ?? categoría) seleccionado: las demás barras se atenúan. */
  selectedId?: string | null;
  /** Alineación de las categorías del eje Y; útil para placas en rankings. */
  categoryLabelAlign?: 'start' | 'end';
  /** Ancho reservado para las categorías del eje Y. */
  categoryAxisWidth?: number;
  /** Trunca categorías extensas a una sola línea; el texto completo queda en title. */
  categoryLabelMaxLength?: number;
  /** Renderiza la etiqueta de categoría con tipografía mono (ideal para placas). */
  isMonoCategory?: boolean;
  /** Si es false (default si hay números directos), no dibuja el eje X ni la cuadrícula. */
  showXAxis?: boolean;
  /** Si es false (default), no dibuja el tooltip flotante para evitar redundancia. */
  showTooltip?: boolean;
  /** Si es true (default), dibuja el valor numérico al final de cada barra. */
  showLabels?: boolean;
}

interface BarChartClickState {
  activePayload?: Array<{ payload?: Record<string, unknown> }>;
}

interface CategoryTickProps {
  x?: number;
  y?: number;
  payload?: { value?: string | number };
}

function LeftAlignedCategoryTick({
  x = 0,
  y = 0,
  payload,
  axisWidth,
  isMono,
}: CategoryTickProps & { axisWidth: number; isMono?: boolean }) {
  return (
    <text
      x={x - axisWidth + 4}
      y={y + 4}
      fill={AXIS_COLOR}
      className={cn(
        'fill-muted-foreground text-[11px] font-medium',
        isMono && 'fill-foreground font-mono font-bold',
      )}
      textAnchor="start"
    >
      {String(payload?.value ?? '')}
    </text>
  );
}

function TruncatedCategoryTick({
  x = 0,
  y = 0,
  payload,
  maxLength,
  isMono,
}: CategoryTickProps & { maxLength: number; isMono?: boolean }) {
  const label = String(payload?.value ?? '');
  const visibleLabel =
    label.length > maxLength ? `${label.slice(0, Math.max(1, maxLength - 1))}…` : label;

  return (
    <text
      x={x - 8}
      y={y + 4}
      fill={AXIS_COLOR}
      className={cn(
        'fill-muted-foreground text-[11px] font-medium',
        isMono && 'fill-foreground font-mono font-bold',
      )}
      textAnchor="end"
    >
      <title>{label}</title>
      {visibleLabel}
    </text>
  );
}

/** Barras horizontales (ideal para rankings por vehículo). */
export function BarChartCard({
  title,
  subtitle,
  data,
  categoryKey,
  valueKey,
  valueName,
  format,
  isLoading,
  isError,
  action,
  height,
  color,
  gradient,
  onBarClick,
  selectedId,
  categoryLabelAlign = 'end',
  categoryAxisWidth = 96,
  categoryLabelMaxLength,
  isMonoCategory,
  showXAxis = true,
  showTooltip = true,
  showLabels = true,
}: BarChartCardProps) {
  const hasSelection = selectedId != null && selectedId !== '';
  // `useId` se llama SIEMPRE, no dentro del `??`. Con `gradient?.id ?? ...` la
  // evaluación se cortaba cuando venía un id propio y el hook no se ejecutaba:
  // el número de hooks del componente cambiaba entre renders según ese prop, que
  // es exactamente lo que rompe el orden de hooks de React.
  const autoGradId = React.useId().replace(/:/g, '');
  const gradId = gradient?.id ?? `barchart-grad-${autoGradId}`;

  return (
    <ChartCard
      title={title}
      subtitle={subtitle}
      isLoading={isLoading}
      isError={isError}
      isEmpty={!isLoading && !isError && data.length === 0}
      action={action}
      height={height ?? Math.max(220, data.length * 32 + 40)}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data as object[]}
          layout="vertical"
          margin={{ top: 8, right: showLabels ? 70 : 16, left: 4, bottom: showXAxis ? 4 : 0 }}
          onClick={
            onBarClick
              ? (state: BarChartClickState) => {
                  const item = state?.activePayload?.[0]?.payload;
                  if (item) onBarClick(item);
                }
              : undefined
          }
          style={onBarClick ? { cursor: 'pointer' } : undefined}
        >
          {gradient && (
            <defs>
              <linearGradient id={gradId} x1="0" y1="0" x2="1" y2="0">
                <stop offset="0%" stopColor={gradient.from} />
                <stop offset="100%" stopColor={gradient.to} />
              </linearGradient>
            </defs>
          )}
          {showXAxis && (
            <CartesianGrid stroke={GRID_COLOR} strokeDasharray="3 3" horizontal={false} />
          )}
          <XAxis
            type="number"
            hide={!showXAxis}
            tick={{ fontSize: 11, fill: AXIS_COLOR }}
            tickLine={false}
            axisLine={{ stroke: GRID_COLOR }}
          />
          <YAxis
            type="category"
            dataKey={categoryKey}
            tick={
              categoryLabelAlign === 'start' ? (
                <LeftAlignedCategoryTick axisWidth={categoryAxisWidth} isMono={isMonoCategory} />
              ) : categoryLabelMaxLength ? (
                <TruncatedCategoryTick maxLength={categoryLabelMaxLength} isMono={isMonoCategory} />
              ) : (
                { fontSize: 11, fill: AXIS_COLOR }
              )
            }
            tickLine={false}
            axisLine={false}
            width={categoryAxisWidth}
          />
          {showTooltip && (
            <Tooltip
              cursor={{ fill: 'rgba(0,0,0,0.04)' }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const rawVal = Number(payload[0]?.value ?? 0);
                const formattedVal = format ? format(rawVal) : rawVal.toLocaleString('es-CO');
                return (
                  <div className="border-border bg-card shadow-xs text-foreground rounded-md border px-2.5 py-1 text-xs font-semibold">
                    {formattedVal} {valueName ? valueName.toLowerCase() : ''}
                  </div>
                );
              }}
            />
          )}
          <Bar
            dataKey={valueKey}
            name={valueName}
            radius={[0, 6, 6, 0]}
            minPointSize={onBarClick ? 12 : 3}
            animationDuration={CHART_ANIMATION_DURATION}
            animationEasing={CHART_ANIMATION_EASING as never}
            cursor={onBarClick ? 'pointer' : undefined}
          >
            {showLabels && (
              <LabelList
                dataKey={valueKey}
                position="right"
                formatter={(v: number) => (format ? format(v) : String(v))}
                style={{ fontSize: 11, fontWeight: 700, fill: 'var(--foreground)' }}
              />
            )}
            {data.map((row, i) => {
              const r = row as Record<string, unknown>;
              const rowId = String(r.id ?? r[categoryKey] ?? '');
              const isSelected = hasSelection && rowId === selectedId;
              const dim = hasSelection && !isSelected;
              const fill = gradient
                ? `url(#${gradId})`
                : (color ?? SERIES_PALETTE[i % SERIES_PALETTE.length]);

              return (
                <Cell
                  key={i}
                  fill={fill}
                  fillOpacity={dim ? 0.25 : 1}
                  stroke={isSelected ? 'var(--foreground)' : undefined}
                  strokeWidth={isSelected ? 1.5 : 0}
                />
              );
            })}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}
