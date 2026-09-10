'use client';

import * as React from 'react';
import {
  CartesianGrid,
  LabelList,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
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
  CHART_COLORS,
  GRID_COLOR,
  tooltipStyle,
} from '@/components/charts/chart-theme';

/** Estado parcial que recharts entrega en el onClick del chart. */
export interface ChartClickState {
  activeLabel?: string | number;
  activePayload?: { payload: Record<string, unknown> }[];
}

interface AxisTickProps {
  x?: number;
  y?: number;
  payload?: { value?: string | number };
}

interface LineDotProps {
  cx?: number;
  cy?: number;
  index?: number;
}

export interface LineSeries {
  key: string;
  name: string;
  color: string;
  /** Formateo del valor en tooltip/eje. */
  format?: (v: number) => string;
  /** Posición de sus etiquetas para evitar que dos series se tapen. */
  labelPosition?: 'top' | 'bottom';
}

interface LineChartCardProps {
  title: string;
  subtitle?: string;
  data: readonly unknown[];
  xKey: string;
  series: LineSeries[];
  isLoading?: boolean;
  action?: React.ReactNode;
  height?: number;
  /** Clic en un bucket del eje X (fecha). Recibe la etiqueta y la fila de datos. */
  onBucketClick?: (label: string, payload?: Record<string, unknown>) => void;
  /** Bucket seleccionado: se resalta con una línea vertical. */
  selectedBucket?: string | null;
  /** Dominio fijo opcional del eje Y. */
  yDomain?: [number, number];
  /** Línea horizontal de referencia opcional. */
  referenceY?: { value: number; label?: string; color?: string };
}

function ToggleLegend({
  series,
  hidden,
  onToggle,
}: {
  series: LineSeries[];
  hidden: ReadonlySet<string>;
  onToggle: (key: string) => void;
}) {
  return (
    <div className="flex justify-center gap-4 pt-2 text-xs">
      {series.map((item) => {
        const isVisible = !hidden.has(item.key);
        return (
          <button
            key={item.key}
            type="button"
            aria-pressed={isVisible}
            onClick={() => onToggle(item.key)}
            className="inline-flex items-center gap-1.5 transition-opacity [transition-timing-function:cubic-bezier(0.4,0,0.2,1)] hover:opacity-75"
            style={{ opacity: isVisible ? 1 : 0.4 }}
          >
            <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: item.color }} />
            <span className={isVisible ? '' : 'line-through'}>{item.name}</span>
          </button>
        );
      })}
    </div>
  );
}

export function LineChartCard({
  title,
  subtitle,
  data,
  xKey,
  series,
  isLoading,
  action,
  height,
  onBucketClick,
  selectedBucket,
  yDomain,
  referenceY,
}: LineChartCardProps) {
  const [hiddenSeries, setHiddenSeries] = React.useState<Set<string>>(() => new Set());
  const [seriesVersions, setSeriesVersions] = React.useState<Record<string, number>>({});
  const fmtByKey = React.useMemo(() => {
    const map: Record<string, (v: number) => string> = {};
    for (const s of series) if (s.format) map[s.name] = s.format;
    return map;
  }, [series]);
  const hasSelectedBucket = selectedBucket != null && selectedBucket !== '';

  function toggleSeries(key: string) {
    const isHidden = hiddenSeries.has(key);
    setHiddenSeries((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    // Al volver a mostrar una línea, se remonta solo esa serie para conservar
    // su animación de entrada sin reanimar las demás.
    if (isHidden) {
      setSeriesVersions((current) => ({
        ...current,
        [key]: (current[key] ?? 0) + 1,
      }));
    }
  }

  function selectBucket(label: string, payload?: Record<string, unknown>) {
    if (!onBucketClick) return;
    const matchingRow = data.find((item) => {
      if (typeof item !== 'object' || item === null) return false;
      return String((item as Record<string, unknown>)[xKey]) === label;
    });
    onBucketClick(label, payload ?? (matchingRow as Record<string, unknown> | undefined));
  }

  function InteractiveTick({ x = 0, y = 0, payload }: AxisTickProps) {
    const label = String(payload?.value ?? '');
    const selected = selectedBucket === label;
    const width = Math.max(48, label.length * 7 + 16);

    return (
      <g
        transform={`translate(${x}, ${y})`}
        role="button"
        tabIndex={0}
        aria-label={`${selected ? 'Quitar selección de' : 'Seleccionar'} ${label}`}
        className="cursor-pointer outline-none"
        onClick={(event: React.MouseEvent<SVGGElement>) => {
          event.stopPropagation();
          selectBucket(label);
        }}
        onKeyDown={(event: React.KeyboardEvent<SVGGElement>) => {
          if (event.key !== 'Enter' && event.key !== ' ') return;
          event.preventDefault();
          event.stopPropagation();
          selectBucket(label);
        }}
      >
        <rect
          x={-width / 2}
          y={4}
          width={width}
          height={22}
          rx={11}
          fill={selected ? CHART_COLORS.blue : 'transparent'}
          className="transition-colors [transition-timing-function:cubic-bezier(0.4,0,0.2,1)]"
        />
        <text
          x={0}
          y={19}
          textAnchor="middle"
          fontSize={11}
          fontWeight={selected ? 700 : 400}
          fill={selected ? '#fff' : AXIS_COLOR}
        >
          {label}
        </text>
      </g>
    );
  }

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
        <LineChart
          data={data as object[]}
          margin={{ top: 32, right: 48, left: 36, bottom: 28 }}
          onClick={
            onBucketClick
              ? (state: ChartClickState) => {
                  if (state?.activeLabel != null)
                    selectBucket(String(state.activeLabel), state.activePayload?.[0]?.payload);
                }
              : undefined
          }
          style={onBucketClick ? { cursor: 'pointer' } : undefined}
        >
          <CartesianGrid stroke={GRID_COLOR} strokeDasharray="3 3" vertical={false} />
          {referenceY && (
            <ReferenceLine
              y={referenceY.value}
              stroke={referenceY.color ?? CHART_COLORS.blue}
              strokeWidth={1.5}
              strokeDasharray="6 4"
              label={referenceY.label}
            />
          )}
          <XAxis
            dataKey={xKey}
            tick={onBucketClick ? <InteractiveTick /> : { fontSize: 11, fill: AXIS_COLOR }}
            tickLine={false}
            axisLine={{ stroke: GRID_COLOR }}
            minTickGap={24}
            padding={{ left: 24, right: 24 }}
          />
          <YAxis
            domain={yDomain}
            tick={{ fontSize: 11, fill: AXIS_COLOR }}
            tickLine={false}
            axisLine={false}
            width={48}
          />
          <Tooltip
            contentStyle={tooltipStyle}
            formatter={(value: number, name: string) =>
              fmtByKey[name] ? fmtByKey[name](value) : value
            }
          />
          {series.length > 1 && (
            <Legend
              content={
                <ToggleLegend series={series} hidden={hiddenSeries} onToggle={toggleSeries} />
              }
            />
          )}
          {series.map((s) => {
            const renderDot = (props: LineDotProps) => {
              const { cx = 0, cy = 0, index = -1 } = props;
              const row = data[index];
              const rowLabel =
                typeof row === 'object' && row !== null
                  ? String((row as Record<string, unknown>)[xKey] ?? '')
                  : '';
              const selected = rowLabel === selectedBucket;

              return (
                <circle
                  key={`${s.key}-dot-${index}`}
                  cx={cx}
                  cy={cy}
                  r={selected ? 5 : 3}
                  fill={s.color}
                  fillOpacity={selected ? 1 : 0.2}
                  stroke={selected ? '#fff' : 'none'}
                  strokeWidth={selected ? 2 : 0}
                />
              );
            };

            return (
              <Line
                key={`${s.key}-${seriesVersions[s.key] ?? 0}`}
                type="monotone"
                dataKey={s.key}
                name={s.name}
                hide={hiddenSeries.has(s.key)}
                stroke={s.color}
                strokeOpacity={hasSelectedBucket ? 0.2 : 1}
                strokeWidth={2}
                animationDuration={CHART_ANIMATION_DURATION}
                animationEasing={CHART_ANIMATION_EASING as never}
                dot={hasSelectedBucket ? renderDot : { r: 3, strokeWidth: 0, fill: s.color }}
                connectNulls
                activeDot={{ r: 5 }}
              >
                {!hasSelectedBucket && (
                  <LabelList
                    dataKey={s.key}
                    position={s.labelPosition ?? 'top'}
                    formatter={(v: number) => (s.format ? s.format(v) : String(v))}
                    style={{ fontSize: 10, fontWeight: 600 }}
                  />
                )}
              </Line>
            );
          })}
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}
