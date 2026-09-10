'use client';

import * as React from 'react';

import { CHART_TONES } from '@/components/charts/chart-theme';

interface Zone {
  /** Límite superior de la zona (en la misma escala que `value`). */
  upto: number;
  color: string;
  label: string;
}

interface GaugeCardProps {
  title: string;
  subtitle?: string;
  /** Valor 0..max (null = sin dato). */
  value: number | null;
  max?: number;
  unit?: string;
  /** Texto auxiliar bajo el valor (ej. nº de lecturas). */
  hint?: string;
  /** Zonas de color ordenadas por `upto` ascendente. */
  zones?: Zone[];
}

const DEFAULT_ZONES: Zone[] = [
  { upto: 40, color: CHART_TONES.limeDark, label: 'Normal (0–40%)' },
  { upto: 50, color: CHART_TONES.yellowDark, label: 'Precaución (40–50%)' },
  { upto: 100, color: CHART_TONES.redMuted, label: 'Alta demanda (50–100%)' },
];

// Semicírculo: 180° (izquierda) = 0, 0° (derecha) = max.
const CX = 100;
const CY = 100;
const R = 80;

function polar(pct: number): [number, number] {
  const angle = Math.PI * (1 - pct); // pct 0..1 -> π..0
  return [CX + R * Math.cos(angle), CY - R * Math.sin(angle)];
}

function arcPath(fromPct: number, toPct: number): string {
  const [x1, y1] = polar(fromPct);
  const [x2, y2] = polar(toPct);
  // El gauge es un semicírculo (180°). Cualquier segmento abarca como máximo
  // ese arco, así que nunca es un "large-arc" (>180°): siempre 0.
  return `M ${x1} ${y1} A ${R} ${R} 0 0 1 ${x2} ${y2}`;
}

export function GaugeCard({
  title,
  subtitle,
  value,
  max = 100,
  unit = '%',
  hint,
  zones = DEFAULT_ZONES,
}: GaugeCardProps) {
  const hasValue = value != null;
  const clamped = hasValue ? Math.max(0, Math.min(value, max)) : 0;
  const pct = clamped / max;
  const [nx, ny] = polar(pct);

  // Color del valor según la zona en que cae.
  const activeZone = zones.find((z) => clamped <= z.upto) ?? zones[zones.length - 1];

  return (
    <div className="bg-card flex h-full flex-col rounded-lg border p-4">
      <div className="mb-2">
        <h3 className="font-heading text-sm font-bold tracking-tight">{title}</h3>
        {subtitle && <p className="text-muted-foreground text-xs">{subtitle}</p>}
      </div>

      <div className="flex min-h-0 flex-1 flex-col items-center justify-center pt-2">
        <svg viewBox="0 0 200 116" className="w-full max-w-[250px]">
          {/* Pista base */}
          <path d={arcPath(0, 1)} fill="none" stroke="var(--muted)" strokeWidth={14} />
          {/* Zonas de color */}
          {zones.map((z, i) => {
            const from = (i === 0 ? 0 : zones[i - 1]!.upto) / max;
            const to = z.upto / max;
            return (
              <path
                key={z.label}
                d={arcPath(from, to)}
                fill="none"
                stroke={z.color}
                strokeWidth={14}
                strokeLinecap="butt"
              />
            );
          })}
          {/* Aguja */}
          {hasValue && (
            <>
              <line
                x1={CX}
                y1={CY}
                x2={nx}
                y2={ny}
                stroke="var(--foreground)"
                strokeWidth={3}
                strokeLinecap="round"
              />
              <circle cx={CX} cy={CY} r={5} fill="var(--foreground)" />
            </>
          )}
        </svg>

        <div className="-mt-4 text-center">
          <p
            className="font-heading text-3xl font-extrabold tracking-tight"
            style={{ color: hasValue ? activeZone?.color : undefined }}
          >
            {hasValue ? `${clamped.toFixed(1)}${unit}` : '—'}
          </p>
          {hasValue && activeZone && (
            <p className="text-muted-foreground text-xs">{activeZone.label}</p>
          )}
          {hint && <p className="text-muted-foreground mt-0.5 text-xs">{hint}</p>}
        </div>
      </div>
    </div>
  );
}
