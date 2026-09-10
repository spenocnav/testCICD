'use client';

import * as React from 'react';

import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { SyncRun } from '@/lib/types';
import { cn } from '@/lib/utils';

const KIND_LABEL: Record<SyncRun['kind'], string> = {
  cloudfleet: 'CloudFleet',
  master: 'Fuente maestra',
  novedades: 'Estado de novedades',
  reportes: 'Reportes',
};

const TRIGGER_LABEL: Record<SyncRun['trigger'], string> = {
  manual: 'Manual',
  worker: 'Worker',
  cli: 'CLI',
};

const dateTimeFmt = new Intl.DateTimeFormat('es-CO', {
  dateStyle: 'full',
  timeStyle: 'medium',
});

const numberFmt = new Intl.NumberFormat('es-CO');

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} s`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s % 60);
  return `${m}m ${rem}s`;
}

/**
 * Etiquetas de las claves que manda el ETL de reportes.
 *
 * `vehicle_extraction_state` guarda una fila por (vehículo, dataset), así que
 * los contadores de datasets NO son vehículos: 16 vehículos × 8 datasets = 128
 * filas. Nombrarlos explícitamente evita que el panel se lea como "128
 * vehículos con problemas".
 */
const RESULT_LABEL: Record<string, string> = {
  vehiculos_activos: 'Vehículos en alcance',
  datasets_ok: 'Datasets al día',
  datasets_error: 'Datasets con error',
  datasets_pending: 'Datasets sin extraer',
  datasets_apagados: 'Datasets apagados',
};

/** Claves informativas: no son un problema aunque el número sea alto. */
const NEUTRAL_KEYS = new Set(['vehiculos_activos', 'datasets_apagados', 'datasets_ok']);

/** Etiqueta legible para las claves que manda cada pipeline. */
function humanizeKey(key: string): string {
  return RESULT_LABEL[key] ?? key.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-0.5">
      <dt className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
        {label}
      </dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

/**
 * Detalle completo de una corrida.
 *
 * La tabla muestra solo lo básico (estado, tipo, cuándo, duración); todo lo
 * demás —conteos por tabla, pasos fallidos, error completo, actor— vive acá
 * para que el historial se lea de un vistazo.
 */
export function SyncRunDetailDialog({
  run,
  onOpenChange,
}: {
  run: SyncRun | null;
  onOpenChange: (open: boolean) => void;
}) {
  if (!run) return null;

  const result = run.result ?? {};
  // Los conteos numéricos son el resumen útil; el resto del JSON (p. ej.
  // `pasos_fallidos`, que es una lista) se muestra aparte.
  const counts = Object.entries(result).filter(([, v]) => typeof v === 'number') as [
    string,
    number,
  ][];
  const failedSteps = Array.isArray(result['pasos_fallidos'])
    ? (result['pasos_fallidos'] as unknown[]).map(String)
    : [];

  return (
    <Dialog open={run !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {KIND_LABEL[run.kind]}
            <Badge
              variant={
                run.status === 'success'
                  ? 'success'
                  : run.status === 'partial'
                    ? 'warning'
                    : 'destructive'
              }
            >
              {run.status === 'success' ? 'OK' : run.status === 'partial' ? 'Parcial' : 'Error'}
            </Badge>
          </DialogTitle>
          <DialogDescription>
            {TRIGGER_LABEL[run.trigger]} · {run.mode}
          </DialogDescription>
        </DialogHeader>

        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Inicio">{dateTimeFmt.format(new Date(run.started_at))}</Field>
          <Field label="Fin">{dateTimeFmt.format(new Date(run.finished_at))}</Field>
          <Field label="Duración">{formatDuration(run.duration_ms)}</Field>
          <Field label="Disparada por">
            {run.actor_email ?? <span className="text-muted-foreground">automática</span>}
          </Field>
        </dl>

        {counts.length > 0 && (
          <div className="space-y-2">
            <h3 className="text-sm font-semibold">Resultado</h3>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {counts.map(([key, value]) => (
                <div
                  key={key}
                  className={cn(
                    'rounded-lg border px-3 py-2',
                    // Solo se resalta lo que la corrida produjo. Un contador
                    // informativo alto (datasets apagados a propósito) no es un
                    // hallazgo y no debe leerse como alarma.
                    value > 0 && !NEUTRAL_KEYS.has(key)
                      ? 'bg-accent-blue/5 border-accent-blue/30'
                      : 'bg-muted/30',
                    key === 'datasets_error' &&
                      value > 0 &&
                      'border-destructive/40 bg-destructive/5',
                  )}
                >
                  <p className="text-lg font-bold tabular-nums">{numberFmt.format(value)}</p>
                  <p className="text-muted-foreground text-xs">{humanizeKey(key)}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {failedSteps.length > 0 && (
          <div className="space-y-2">
            <h3 className="text-sm font-semibold text-[#7A5300]">Pasos sin actualizar</h3>
            <ul className="list-inside list-disc text-sm text-[#7A5300]">
              {failedSteps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ul>
          </div>
        )}

        {run.error && (
          <div className="space-y-2">
            <h3 className="text-destructive text-sm font-semibold">Error</h3>
            {/* El mensaje del pipeline puede ser largo y con saltos de línea:
                se preserva tal cual y se le da scroll propio. */}
            <pre className="bg-destructive/5 text-destructive max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-lg border p-3 text-xs">
              {run.error}
            </pre>
          </div>
        )}

        {counts.length === 0 && failedSteps.length === 0 && !run.error && (
          <p className="text-muted-foreground text-sm">La corrida no reportó detalle.</p>
        )}
      </DialogContent>
    </Dialog>
  );
}
