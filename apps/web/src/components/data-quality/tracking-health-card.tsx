'use client';

import { Tags } from 'lucide-react';
import * as React from 'react';

import { Skeleton } from '@/components/ui/skeleton';
import { useTrackingHealth } from '@/lib/data-quality';
import type { TrackingHealth } from '@/lib/types';
import { cn } from '@/lib/utils';

/** Tonos del semáforo. El backend decide el estado; acá solo se pinta. */
const TONES: Record<
  TrackingHealth['status'],
  { dot: string; ring: string; panel: string; text: string; label: string }
> = {
  ok: {
    dot: 'bg-emerald-500',
    ring: 'bg-emerald-500/30',
    panel: 'border-emerald-500/30 bg-emerald-500/5',
    text: 'text-emerald-700',
    label: 'En línea',
  },
  degraded: {
    dot: 'bg-amber-500',
    ring: 'bg-amber-500/30',
    panel: 'border-amber-500/30 bg-amber-500/5',
    text: 'text-amber-700',
    label: 'Degradado',
  },
  down: {
    dot: 'bg-destructive',
    ring: 'bg-destructive/30',
    panel: 'border-destructive/30 bg-destructive/5',
    text: 'text-destructive',
    label: 'Detenido',
  },
  unknown: {
    dot: 'bg-slate-400',
    ring: 'bg-slate-400/30',
    panel: 'border-slate-300 bg-slate-50',
    text: 'text-slate-600',
    label: 'Sin datos',
  },
};

function relativeAge(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return 'sin reporte';
  if (seconds < 90) return `hace ${Math.round(seconds)} s`;
  if (seconds < 5400) return `hace ${Math.round(seconds / 60)} min`;
  return `hace ${Math.round(seconds / 3600)} h`;
}

export function TrackingHealthCard({ enabled = true }: { enabled?: boolean }) {
  const health = useTrackingHealth(enabled);

  if (health.isLoading) {
    return <Skeleton className="h-[168px] rounded-lg" />;
  }
  // Un error de red al consultar el semáforo no es lo mismo que "el pipeline
  // está caído": no se inventa un color, se dice que no se pudo consultar.
  if (health.isError || !health.data) {
    return (
      <article className="rounded-lg border bg-white p-5">
        <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
          Etiquetas de taller
        </p>
        <p className="text-muted-foreground mt-3 text-sm">
          No se pudo consultar el estado del pipeline.
        </p>
      </article>
    );
  }

  const data = health.data;
  const tone = TONES[data.status] ?? TONES.unknown;

  return (
    <article className={cn('rounded-lg border p-5', tone.panel)}>
      <div className="flex items-start justify-between gap-3">
        <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
          Etiquetas de taller
        </p>
        <Tags className="text-muted-foreground h-4 w-4 shrink-0" aria-hidden />
      </div>

      <div className="mt-2 flex items-center gap-2">
        <span className="relative flex h-3 w-3 shrink-0" aria-hidden>
          {data.status === 'ok' && (
            <span
              className={cn(
                'absolute inline-flex h-full w-full animate-ping rounded-full',
                tone.ring,
              )}
            />
          )}
          <span className={cn('relative inline-flex h-3 w-3 rounded-full', tone.dot)} />
        </span>
        <span className={cn('text-lg font-extrabold', tone.text)}>{tone.label}</span>
        {/* Texto además del color: el estado no puede depender solo del tono. */}
        <span className="sr-only">{data.headline}</span>
      </div>

      <p className="text-muted-foreground mt-1 text-xs">{data.headline}</p>

      <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted-foreground">Ingestor</dt>
        <dd className="text-right font-semibold">{relativeAge(data.ingest_age_seconds)}</dd>
        <dt className="text-muted-foreground">Worker</dt>
        <dd className="text-right font-semibold">{relativeAge(data.worker_age_seconds)}</dd>
        <dt className="text-muted-foreground">Seguimientos</dt>
        <dd className="text-right font-semibold">{data.events_total.toLocaleString('es-CO')}</dd>
        <dt className="text-muted-foreground">Con etiqueta</dt>
        <dd
          className={cn(
            'text-right font-semibold',
            data.events_total > 0 && data.events_with_label === 0 && 'text-amber-700',
          )}
        >
          {data.events_with_label.toLocaleString('es-CO')}
        </dd>
      </dl>

      {data.notes.length > 0 && (
        <ul className="text-muted-foreground mt-3 space-y-1 border-t pt-2 text-[11px] leading-snug">
          {data.notes.slice(0, 3).map((note) => (
            <li key={note} className="flex gap-1.5">
              <span aria-hidden>·</span>
              <span>{note}</span>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}
