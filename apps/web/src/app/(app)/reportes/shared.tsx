'use client';

import * as React from 'react';

import { CHART_COLORS } from '@/components/charts/chart-theme';

/** Formato numérico con separadores es-CO. */
export function fmt(v: number | null | undefined, decimals = 1): string {
  if (v == null) return '—';
  return v.toLocaleString('es-CO', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

/** Los pct vienen como fracción 0..1; se muestran como porcentaje. */
export function fmtPct(v: number | null | undefined): string {
  if (v == null) return '—';
  return `${(v * 100).toFixed(1)} %`;
}

/** Entero con separadores de miles. */
export function fmtInt(v: number | null | undefined): string {
  if (v == null) return '—';
  return Math.round(v).toLocaleString('es-CO');
}

/** Magnitudes grandes en notación compacta es-CO: 235 k, 1,2 M. */
export function fmtCompact(v: number | null | undefined): string {
  if (v == null) return '—';
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return `${fmt(v / 1_000_000, 1)} M`;
  if (abs >= 1_000) return `${fmt(v / 1_000, abs >= 100_000 ? 0 : 1)} k`;
  return fmt(v, Number.isInteger(v) ? 0 : 1);
}

interface SparklineProps {
  data: (number | null | undefined)[];
  width?: number;
  height?: number;
  color?: string;
}

function buildSmoothPath(pts: { x: number; y: number }[]): string {
  const first = pts[0];
  if (!first) return '';
  if (pts.length === 1) return `M ${first.x.toFixed(1)} ${first.y.toFixed(1)}`;
  const second = pts[1];
  if (pts.length === 2 && second) {
    return `M ${first.x.toFixed(1)} ${first.y.toFixed(1)} L ${second.x.toFixed(1)} ${second.y.toFixed(1)}`;
  }

  let d = `M ${first.x.toFixed(1)} ${first.y.toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)] ?? first;
    const p1 = pts[i] ?? first;
    const p2 = pts[i + 1] ?? first;
    const p3 = pts[Math.min(pts.length - 1, i + 2)] ?? p2;

    const tension = 0.2;
    const cp1x = p1.x + (p2.x - p0.x) * tension;
    const cp1y = p1.y + (p2.y - p0.y) * tension;
    const cp2x = p2.x - (p3.x - p1.x) * tension;
    const cp2y = p2.y - (p3.y - p1.y) * tension;

    d += ` C ${cp1x.toFixed(1)} ${cp1y.toFixed(1)}, ${cp2x.toFixed(1)} ${cp2y.toFixed(1)}, ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
  }
  return d;
}

function Sparkline({ data, width = 80, height = 36, color = CHART_COLORS.blue }: SparklineProps) {
  const pathD = React.useMemo(() => {
    const valid = data.filter((v): v is number => v != null);
    if (valid.length < 2) return null;
    const min = Math.min(...valid);
    const max = Math.max(...valid);
    const range = max - min || 1;
    const pad = 3;
    const innerHeight = height - pad * 2;
    const stepX = (width - pad * 2) / (valid.length - 1);

    const pts = valid.map((v, i) => ({
      x: pad + i * stepX,
      y: pad + innerHeight - ((v - min) / range) * innerHeight,
    }));

    return buildSmoothPath(pts);
  }, [data, width, height]);

  if (!pathD) return null;

  return (
    <svg width={width} height={height} className="shrink-0" aria-hidden>
      <path
        d={pathD}
        fill="none"
        stroke={color}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function parseFormattedString(str: string) {
  const match = str.trim().match(/^([^\d-]*)(-?[\d.,]+)(.*)$/);
  if (!match) return null;

  const prefix = match[1] ?? '';
  const numStr = match[2] ?? '';
  const suffix = match[3] ?? '';

  const hasComma = numStr.includes(',');
  const decimals = hasComma ? (numStr.split(',')[1]?.length ?? 0) : 0;

  const cleaned = numStr.replace(/\./g, '').replace(',', '.');
  const target = parseFloat(cleaned);
  if (isNaN(target)) return null;

  return { prefix, target, suffix, decimals };
}

export function AnimatedKpiValue({ value }: { value: string }) {
  const [displayValue, setDisplayValue] = React.useState(value);
  const prevTargetRef = React.useRef<number | null>(null);

  React.useEffect(() => {
    const parsed = parseFormattedString(value);
    if (!parsed) {
      setDisplayValue(value);
      prevTargetRef.current = null;
      return;
    }

    const { prefix, target, suffix, decimals } = parsed;
    const startVal = prevTargetRef.current ?? 0;
    prevTargetRef.current = target;

    if (startVal === target) {
      setDisplayValue(value);
      return;
    }

    const duration = 650;
    const startTime = performance.now();
    let frameId: number;

    const tick = (now: number) => {
      const elapsed = now - startTime;
      const progress = Math.min(elapsed / duration, 1);
      const ease = 1 - Math.pow(1 - progress, 3);
      const current = startVal + (target - startVal) * ease;

      const formattedNum = current.toLocaleString('es-CO', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      });

      setDisplayValue(`${prefix}${formattedNum}${suffix}`);

      if (progress < 1) {
        frameId = requestAnimationFrame(tick);
      } else {
        setDisplayValue(value);
      }
    };

    frameId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frameId);
  }, [value]);

  return <>{displayValue}</>;
}

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

export interface KpiDelta {
  /** Porcentaje de cambio (+3.8 para +3.8%, -2.1 para -2.1%). */
  percent: number | null | undefined;
  /** Dirección semántica: 'higher-is-better' (verde al subir), 'lower-is-better' (verde al bajar), 'neutral' (azul/gris). */
  polarity?: 'higher-is-better' | 'lower-is-better' | 'neutral';
  /** Título principal de la comparativa. */
  label?: string;
  /** Periodo anterior de referencia para tooltip. */
  previousPeriod?: string;
  /** Valor real actual formateado completo para mostrar en hover (ej. "70.142"). */
  currentVal?: string;
  /** Valor real anterior formateado completo para mostrar en hover (ej. "23.500"). */
  previousVal?: string;
}

interface KpiCardProps {
  label: string;
  value: string;
  hint?: string;
  trend?: (number | null | undefined)[];
  delta?: KpiDelta | null;
  icon?: React.ReactNode;
  /** Valor exacto sin compactar; se muestra al pasar el cursor. */
  exact?: string;
  /** Fuerza el modo hover aunque la tarjeta no tenga serie o comparativa. */
  interactive?: boolean;
  /** Si la tarjeta está fijada volteada (hover/click global). */
  isFlipped?: boolean;
  /** Callback al hacer clic para togglear el volteo global. */
  onClick?: () => void;
  /** Índice de la tarjeta para efecto dominó escalonado (0, 1, 2, 3...). */
  index?: number;
  /** Variante compacta para secciones densas con múltiples KPIs. */
  compact?: boolean;
  /** Clases CSS adicionales para el contenedor. */
  className?: string;
}

export function KpiCard({
  label,
  value,
  hint,
  trend,
  delta,
  icon,
  exact,
  interactive = false,
  isFlipped = false,
  onClick,
  index = 0,
  compact = false,
  className,
}: KpiCardProps) {
  const fullValue = exact ?? value;
  const hasInteractiveHover = Boolean(
    interactive || hint || icon || delta || trend || (exact && exact !== value),
  );

  const renderDelta = () => {
    if (!delta || delta.percent == null || isNaN(delta.percent)) return null;
    const { percent, polarity = 'higher-is-better', label: deltaLabel, previousPeriod } = delta;
    const isPositive = percent > 0;
    const isNegative = percent < 0;

    let colorStyle = 'bg-muted text-muted-foreground border-border';
    if (polarity === 'higher-is-better') {
      if (isPositive)
        colorStyle =
          'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20';
      if (isNegative)
        colorStyle = 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20';
    } else if (polarity === 'lower-is-better') {
      if (isNegative)
        colorStyle =
          'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20';
      if (isPositive)
        colorStyle = 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20';
    } else {
      colorStyle = 'bg-accent-blue/10 text-accent-blue border-accent-blue/20';
    }

    const badge = (
      <span
        className={cn(
          'inline-flex shrink-0 cursor-help items-center gap-0.5 rounded-full border font-bold transition-all duration-200 hover:scale-105',
          compact ? 'py-0.2 px-1.5 text-[10px]' : 'px-2 py-0.5 text-[11px]',
          colorStyle,
        )}
      >
        {isPositive ? '▲ +' : isNegative ? '▼ ' : ''}
        {Math.abs(percent).toLocaleString('es-CO', {
          minimumFractionDigits: 1,
          maximumFractionDigits: 1,
        })}{' '}
        %
      </span>
    );

    return (
      <TooltipProvider delayDuration={50}>
        <Tooltip>
          <TooltipTrigger asChild>{badge}</TooltipTrigger>
          <TooltipContent
            side="top"
            align="center"
            className="bg-popover text-popover-foreground z-50 rounded-lg border px-2.5 py-1 text-xs shadow-md"
          >
            <p className="text-foreground font-medium">
              {previousPeriod ? `vs ${previousPeriod}` : (deltaLabel ?? 'Variación')}
            </p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  };

  if (hasInteractiveHover) {
    const delayMs = index * 120;

    return (
      <div
        onClick={onClick}
        role={onClick ? 'button' : undefined}
        tabIndex={onClick ? 0 : undefined}
        onKeyDown={
          onClick
            ? (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onClick();
                }
              }
            : undefined
        }
        className={cn(
          'bg-card shadow-xs hover:border-primary/40 group relative w-full cursor-pointer select-none overflow-hidden rounded-xl border outline-none transition-all duration-700 ease-in-out hover:shadow-md',
          compact ? 'h-[72px] p-2' : 'h-[94px] p-3',
          className,
        )}
      >
        {/* Estado Normal (Reposo): Nombre arriba + Cifra completa centrada */}
        <div
          style={{ transitionDelay: `${delayMs}ms` }}
          className={cn(
            'absolute inset-0 flex flex-col items-center justify-center text-center transition-all duration-700 ease-in-out',
            compact ? 'p-2' : 'p-3',
            isFlipped
              ? 'pointer-events-none scale-95 opacity-0'
              : 'scale-100 opacity-100 group-hover:pointer-events-none group-hover:scale-95 group-hover:opacity-0',
          )}
        >
          <p
            className={cn(
              'text-muted-foreground whitespace-nowrap font-bold uppercase tracking-wider',
              compact ? 'text-[10px]' : 'text-[11px]',
            )}
          >
            {label}
          </p>
          <p
            className={cn(
              'font-heading text-foreground font-extrabold tabular-nums tracking-tight',
              compact ? 'mt-0.5 text-lg' : 'mt-1 text-2xl',
            )}
          >
            <AnimatedKpiValue value={fullValue} />
          </p>
        </div>

        {/* Estado Revelado (Hover o Flipped): Variación centrada verticalmente (izq) y Minigráfica + Valores (der) */}
        <div
          style={{ transitionDelay: `${delayMs}ms` }}
          className={cn(
            'bg-card/95 absolute inset-0 flex items-center justify-between text-center backdrop-blur-sm transition-all duration-700 ease-in-out',
            compact ? 'gap-1.5 px-2.5 py-1.5' : 'gap-2 px-3.5 py-2',
            isFlipped
              ? 'pointer-events-auto scale-100 opacity-100'
              : 'pointer-events-none scale-95 opacity-0 group-hover:pointer-events-auto group-hover:scale-100 group-hover:opacity-100',
          )}
        >
          {/* Bloque Izquierdo: Variación centrada verticalmente */}
          <div
            className={cn(
              'flex shrink-0 items-center justify-center',
              compact && '[&_svg]:h-4 [&_svg]:w-4',
            )}
          >
            {icon && <div className="text-muted-foreground/50">{icon}</div>}
            {delta && renderDelta()}
          </div>

          {/* Bloque Derecho: Minigráfica arriba, Valores comparativos centrados debajo */}
          <div className="flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5">
            {trend ? (
              <div className={cn('w-full shrink-0', compact ? 'max-w-[76px]' : 'max-w-[96px]')}>
                <Sparkline data={trend} height={compact ? 28 : 36} width={compact ? 70 : 80} />
              </div>
            ) : null}

            {delta?.currentVal && delta?.previousVal && (
              <p
                className={cn(
                  'text-muted-foreground whitespace-nowrap font-semibold tabular-nums tracking-tight',
                  compact ? 'text-[10px]' : 'text-[11px]',
                )}
              >
                <span className="text-foreground font-bold">{delta.currentVal}</span>
                <span className="text-muted-foreground/50 mx-1">vs</span>
                <span>{delta.previousVal}</span>
              </p>
            )}
            {hint && !trend && !delta && (
              <p
                className={cn(
                  'text-muted-foreground line-clamp-2',
                  compact ? 'text-[10px] leading-tight' : 'text-[11px] font-medium',
                )}
              >
                {hint}
              </p>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      className={cn(
        'bg-card flex items-center justify-between gap-1.5 rounded-lg border',
        compact ? 'px-2.5 py-2' : 'px-3.5 py-3',
        className,
      )}
    >
      <div className="min-w-0">
        <p
          className={cn(
            'text-muted-foreground whitespace-nowrap font-medium uppercase tracking-wide',
            compact ? 'text-[10px]' : 'text-xs',
          )}
        >
          {label}
        </p>
        <p
          className={cn(
            'font-heading truncate font-extrabold tabular-nums tracking-tight',
            compact ? 'mt-0.5 text-lg' : 'mt-1 text-xl',
          )}
          title={exact}
        >
          <AnimatedKpiValue value={value} />
        </p>
        {hint && (
          <p
            className={cn(
              'text-muted-foreground',
              compact ? 'mt-0.5 text-[10px]' : 'mt-0.5 text-xs',
            )}
          >
            {hint}
          </p>
        )}
      </div>
      {delta ? (
        renderDelta()
      ) : trend ? (
        <Sparkline data={trend} height={compact ? 28 : 36} width={compact ? 70 : 80} />
      ) : icon ? (
        <div
          className={cn('text-muted-foreground/30 shrink-0', compact && '[&_svg]:h-4 [&_svg]:w-4')}
        >
          {icon}
        </div>
      ) : null}
    </div>
  );
}
