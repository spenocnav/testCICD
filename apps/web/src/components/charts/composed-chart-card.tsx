'use client';

import * as React from 'react';
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  LabelList,
  Legend,
  Line,
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
  EXTREME_COLORS,
  GRID_COLOR,
  tooltipStyle,
} from '@/components/charts/chart-theme';

export interface ComposedSeries {
  key: string;
  name: string;
  color: string;
  /** Eje al que se ancla la serie (por defecto izquierdo). */
  axis?: 'left' | 'right';
  /** Formateo del valor en tooltip. */
  format?: (v: number) => string;
  /** Id de apilado: barras con el mismo id se apilan en vez de agruparse. */
  stackId?: string;
  /** Campo alternativo para etiquetar el total de una barra apilada. */
  labelKey?: string;
  /** Opacidad base de la serie. */
  opacity?: number;
}

interface LineDotProps {
  cx?: number;
  cy?: number;
  index?: number;
}

interface AxisTickProps {
  x?: number;
  y?: number;
  payload?: { value?: string | number };
}

interface SeriesExtremes {
  minIndex: number;
  maxIndex: number;
}

interface ComposedChartCardProps {
  title: string;
  subtitle?: string;
  data: readonly unknown[];
  xKey: string;
  /** Series dibujadas como barras (volúmenes: gal, kms, horas). */
  bars?: ComposedSeries[];
  /** Series dibujadas como líneas (tasas: km/gal, %, velocidad). */
  lines?: ComposedSeries[];
  isLoading?: boolean;
  isError?: boolean;
  action?: React.ReactNode;
  height?: number;
  /** Clic en un bucket del eje X (fecha). Recibe la etiqueta y la fila de datos. */
  onBucketClick?: (label: string, payload?: Record<string, unknown>) => void;
  /** Bucket seleccionado: se resalta con una línea vertical. */
  selectedBucket?: string | null;
  /** Muestra valores permanentes sobre barras y líneas. Por defecto, sí. */
  showValueLabels?: boolean;
  /** Líneas que aparecen desactivadas hasta habilitarlas desde la leyenda. */
  defaultHiddenSeries?: readonly string[];
  /** Marca únicamente los puntos mínimo y máximo de cada línea visible. */
  highlightLineExtremes?: boolean;
}

function InteractiveLegend({
  series,
  toggleableKeys,
  hidden,
  onToggle,
}: {
  series: ComposedSeries[];
  toggleableKeys: ReadonlySet<string>;
  hidden: ReadonlySet<string>;
  onToggle: (key: string) => void;
}) {
  return (
    <div className="flex flex-wrap justify-center gap-4 pt-2 text-xs">
      {series.map((item) => {
        const toggleable = toggleableKeys.has(item.key);
        const visible = !hidden.has(item.key);
        const content = (
          <>
            <span
              className="h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: item.color, opacity: item.opacity ?? 1 }}
            />
            <span className={visible ? '' : 'line-through'}>{item.name}</span>
          </>
        );

        return toggleable ? (
          <button
            key={item.key}
            type="button"
            aria-pressed={visible}
            onClick={() => onToggle(item.key)}
            className="inline-flex items-center gap-1.5 transition-opacity hover:opacity-75"
            style={{ opacity: visible ? 1 : 0.4 }}
          >
            {content}
          </button>
        ) : (
          <span key={item.key} className="inline-flex items-center gap-1.5">
            {content}
          </span>
        );
      })}
    </div>
  );
}

/**
 * Barras + líneas sobre un eje temporal. Pensado para combinar un volumen
 * (barras, ej. galones consumidos) con una tasa (línea, ej. km/gal) usando
 * dos ejes Y, de modo que se lea "cuánto gastó" y "qué tan eficiente" a la vez.
 */
export function ComposedChartCard({
  title,
  subtitle,
  data,
  xKey,
  bars = [],
  lines = [],
  isLoading,
  isError,
  action,
  height,
  onBucketClick,
  selectedBucket,
  showValueLabels = true,
  defaultHiddenSeries = [],
  highlightLineExtremes = false,
}: ComposedChartCardProps) {
  const [hiddenSeries, setHiddenSeries] = React.useState<Set<string>>(
    () => new Set(defaultHiddenSeries),
  );
  const all = React.useMemo(() => [...bars, ...lines], [bars, lines]);
  const lineKeys = React.useMemo(() => new Set(lines.map((line) => line.key)), [lines]);
  const hasRight = all.some((s) => s.axis === 'right');
  const hasSelectedBucket = selectedBucket != null && selectedBucket !== '';

  const fmtByName = React.useMemo(() => {
    const map: Record<string, (v: number) => string> = {};
    for (const s of all) if (s.format) map[s.name] = s.format;
    return map;
  }, [all]);
  const extremesByKey = React.useMemo(() => {
    const result: Record<string, SeriesExtremes | undefined> = {};

    for (const item of lines) {
      let minValue = Number.POSITIVE_INFINITY;
      let maxValue = Number.NEGATIVE_INFINITY;
      let minIndex = -1;
      let maxIndex = -1;

      data.forEach((row, index) => {
        if (typeof row !== 'object' || row === null) return;
        const rawValue = (row as Record<string, unknown>)[item.key];
        if (rawValue == null) return;
        const value = Number(rawValue);
        if (!Number.isFinite(value)) return;
        if (value < minValue) {
          minValue = value;
          minIndex = index;
        }
        if (value > maxValue) {
          maxValue = value;
          maxIndex = index;
        }
      });

      result[item.key] = minIndex >= 0 && maxIndex >= 0 ? { minIndex, maxIndex } : undefined;
    }

    return result;
  }, [data, lines]);

  function toggleSeries(key: string) {
    setHiddenSeries((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
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

  function renderExtremeDot(item: ComposedSeries, props: LineDotProps) {
    const { cx = 0, cy = 0, index = -1 } = props;
    const extremes = extremesByKey[item.key];
    const isMin = extremes?.minIndex === index;
    const isMax = extremes?.maxIndex === index;

    if (!isMin && !isMax) {
      return <circle key={`${item.key}-dot-${index}`} cx={cx} cy={cy} r={0} fill="none" />;
    }

    return (
      <circle
        key={`${item.key}-dot-${index}`}
        cx={cx}
        cy={cy}
        r={5}
        fill={isMin && !isMax ? EXTREME_COLORS.min : EXTREME_COLORS.max}
        stroke="#fff"
        strokeWidth={2}
        pointerEvents="none"
      />
    );
  }

  function renderSelectedDot(item: ComposedSeries, props: LineDotProps) {
    const { cx = 0, cy = 0, index = -1 } = props;
    const row = data[index];
    const rowLabel =
      typeof row === 'object' && row !== null
        ? String((row as Record<string, unknown>)[xKey] ?? '')
        : '';
    const selected = rowLabel === selectedBucket;

    return (
      <circle
        key={`${item.key}-dot-${index}`}
        cx={cx}
        cy={cy}
        r={selected ? 5 : 3}
        fill={item.color}
        fillOpacity={selected ? 1 : 0.2}
        stroke={selected ? '#fff' : 'none'}
        strokeWidth={selected ? 2 : 0}
      />
    );
  }

  return (
    <ChartCard
      title={title}
      subtitle={subtitle}
      isLoading={isLoading}
      isError={isError}
      isEmpty={!isLoading && !isError && data.length === 0}
      action={action}
      height={height}
    >
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart
          data={data as object[]}
          margin={{ top: 32, right: 48, left: 36, bottom: 28 }}
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
            <ReferenceLine
              x={selectedBucket}
              yAxisId="left"
              stroke={CHART_COLORS.blue}
              strokeDasharray="4 2"
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
            yAxisId="left"
            tick={{ fontSize: 11, fill: AXIS_COLOR }}
            tickLine={false}
            axisLine={false}
            width={48}
          />
          {hasRight && (
            <YAxis
              yAxisId="right"
              orientation="right"
              tick={{ fontSize: 11, fill: AXIS_COLOR }}
              tickLine={false}
              axisLine={false}
              width={44}
            />
          )}
          <Tooltip
            contentStyle={tooltipStyle}
            cursor={{ fill: 'rgba(0,0,0,0.04)' }}
            formatter={(value: number, name: string) =>
              fmtByName[name] ? fmtByName[name](value) : value
            }
          />
          {all.length > 1 && (
            <Legend
              content={
                <InteractiveLegend
                  series={all}
                  toggleableKeys={lineKeys}
                  hidden={hiddenSeries}
                  onToggle={toggleSeries}
                />
              }
            />
          )}
          {bars.map((s) => (
            <Bar
              key={s.key}
              yAxisId={s.axis ?? 'left'}
              dataKey={s.key}
              name={s.name}
              fill={s.color}
              fillOpacity={s.opacity ?? 1}
              stackId={s.stackId}
              radius={s.stackId ? [0, 0, 0, 0] : [6, 6, 0, 0]}
              maxBarSize={48}
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
                    fillOpacity={dimmed ? (s.opacity ?? 1) * 0.2 : (s.opacity ?? 1)}
                  />
                );
              })}
              {/* Apilado: la suma por barra ya se lee en el tooltip; etiquetar cada
                  segmento satura la barra. */}
              {showValueLabels && (!s.stackId || s.labelKey) && (
                <LabelList
                  dataKey={s.labelKey ?? s.key}
                  position="top"
                  formatter={(v: number) => (s.format ? s.format(v) : String(v))}
                  style={{ fontSize: 10, fontWeight: 600 }}
                />
              )}
            </Bar>
          ))}
          {lines.map((s) => {
            const lineLabelPos = bars.length > 0 ? 'bottom' : 'top';
            return (
              <Line
                key={s.key}
                yAxisId={s.axis ?? 'left'}
                type="monotone"
                dataKey={s.key}
                name={s.name}
                hide={hiddenSeries.has(s.key)}
                stroke={s.color}
                strokeOpacity={hasSelectedBucket ? 0.2 : 1}
                strokeWidth={2}
                animationDuration={CHART_ANIMATION_DURATION}
                animationEasing={CHART_ANIMATION_EASING as never}
                dot={
                  hasSelectedBucket
                    ? (props: LineDotProps) => renderSelectedDot(s, props)
                    : highlightLineExtremes
                      ? (props: LineDotProps) => renderExtremeDot(s, props)
                      : { r: 3, strokeWidth: 0, fill: s.color }
                }
                connectNulls
                activeDot={{ r: 5 }}
              >
                {showValueLabels && !hasSelectedBucket && (
                  <LabelList
                    dataKey={s.key}
                    position={lineLabelPos}
                    formatter={(v: number) => (s.format ? s.format(v) : String(v))}
                    style={{ fontSize: 10, fontWeight: 600 }}
                  />
                )}
              </Line>
            );
          })}
        </ComposedChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}
