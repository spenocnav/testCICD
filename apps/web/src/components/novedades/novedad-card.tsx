'use client';

import Link from 'next/link';
import { CheckCircle2, ChevronRight, ImageIcon } from 'lucide-react';

import {
  PRIORITY_LABEL,
  formatNovedadDate,
  lifecycle,
} from '@/components/novedades/novedad-status';
import { Badge } from '@/components/ui/badge';
import type { NovedadListItem } from '@/lib/types';
import { cn } from '@/lib/utils';

/**
 * Fila de la lista en pantallas angostas. Toda la tarjeta es el enlace: en un
 * teléfono no hay "Ver" que apuntar con precisión, y ≥ 88 px de alto la hacen
 * fácil de tocar. Un solo badge de estado (`lifecycle`); una novedad resuelta
 * se atenúa y lleva un borde verde a la izquierda para leerse "cerrada" de un
 * vistazo antes de leer nada.
 */
export function NovedadCard({ novedad }: { novedad: NovedadListItem }) {
  const state = lifecycle(novedad);
  const resolved = state.code === 'resolved';

  return (
    <Link
      href={`/novedades/${novedad.id}`}
      data-testid="novedad-card"
      data-state={state.code}
      className={cn(
        'active:bg-muted focus-visible:ring-ring flex min-h-[88px] items-stretch gap-3 rounded-md border bg-white p-4 transition-colors focus-visible:outline-none focus-visible:ring-2',
        resolved && 'bg-muted/40 border-l-4 border-l-[#3F5E00]',
      )}
    >
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex items-center justify-between gap-2">
          <span
            className={cn('font-mono text-sm font-semibold', resolved && 'text-muted-foreground')}
          >
            {novedad.vehicle_code}
          </span>
          <Badge variant={state.variant} className="shrink-0">
            {resolved && <CheckCircle2 className="mr-1 h-3 w-3" aria-hidden />}
            {state.label}
          </Badge>
        </div>

        <p
          className={cn(
            'line-clamp-2 whitespace-pre-line text-sm',
            resolved && 'text-muted-foreground',
          )}
        >
          {novedad.comment || 'Sin comentario'}
        </p>

        <div className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          <span>{formatNovedadDate(novedad.reported_at, 'medium')}</span>
          <span>Prioridad {PRIORITY_LABEL[novedad.priority].toLowerCase()}</span>
          <span className="inline-flex items-center gap-1">
            <ImageIcon className="h-3.5 w-3.5" aria-hidden />
            {novedad.attachment_count}
          </span>
          {state.detail && (
            <span className={cn(resolved && 'font-medium text-[#3F5E00]')}>{state.detail}</span>
          )}
        </div>
      </div>
      <ChevronRight className="text-muted-foreground h-5 w-5 shrink-0 self-center" aria-hidden />
    </Link>
  );
}
