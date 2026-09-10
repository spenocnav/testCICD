'use client';

import * as React from 'react';
import { Info, Layers, SlidersHorizontal } from 'lucide-react';

import type { CalificacionConfiguracion } from '@/lib/types';

function fmtFecha(iso: string | null): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString('es-CO', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/**
 * Con qué calibración se calculó el puntaje que se está viendo.
 *
 * El caso que hay que comunicar es `mixto`: el alcance mezcla flotas con
 * calibraciones distintas, así que el backend calculó con los valores por
 * defecto y el puntaje en pantalla no corresponde a la calibración de NINGUNA
 * de esas flotas. Callarlo deja al usuario comparando notas que no existen.
 *
 * `defecto` no se avisa: es el estado normal y un banner permanente se vuelve
 * ruido que nadie lee.
 */
export function CalificacionConfiguracionAviso({
  configuracion,
}: {
  configuracion?: CalificacionConfiguracion | null;
}) {
  if (!configuracion) return null;

  if (configuracion.origen === 'mixto') {
    return (
      <div className="border-accent-yellow/50 bg-accent-yellow/10 mb-3 flex items-start gap-3 rounded-lg border p-3">
        <Layers className="text-accent-yellow mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        <p className="text-xs leading-snug">
          <span className="font-semibold">
            Estás viendo varias flotas con calibraciones distintas.
          </span>{' '}
          <span className="text-muted-foreground">
            El puntaje se calculó con los valores por defecto, así que no corresponde a la
            calibración de ninguna de ellas. Selecciona una sola flota en el filtro superior para
            ver su calificación con sus propios parámetros.
          </span>
        </p>
      </div>
    );
  }

  if (configuracion.origen === 'flota') {
    const fecha = fmtFecha(configuracion.actualizado_en);
    return (
      <div className="border-accent-blue/40 bg-accent-blue/5 mb-3 flex items-start gap-3 rounded-lg border p-3">
        <SlidersHorizontal className="text-accent-blue mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        <p className="text-xs leading-snug">
          <span className="font-semibold">Esta flota tiene calibración propia.</span>{' '}
          <span className="text-muted-foreground">
            Los pesos, metas y umbrales de la nota fueron ajustados para su operación
            {fecha ? ` el ${fecha}` : ''}
            {configuracion.actualizado_por ? ` por ${configuracion.actualizado_por}` : ''}.
          </span>
        </p>
      </div>
    );
  }

  return null;
}

/** Aviso corto para cuando no se puede calibrar por tener varias flotas activas. */
export function CalificacionCalibracionBloqueada() {
  return (
    <span className="text-muted-foreground inline-flex items-center gap-1.5 text-xs">
      <Info className="h-3.5 w-3.5" aria-hidden />
      Selecciona una sola flota para calibrar
    </span>
  );
}
