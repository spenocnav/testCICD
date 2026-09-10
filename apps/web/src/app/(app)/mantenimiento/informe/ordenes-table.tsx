'use client';

import { ChevronDown, ChevronRight } from 'lucide-react';
import * as React from 'react';

import { cn } from '@/lib/utils';
import type { OrdenDetalle } from '@/lib/mantenimiento';

function fmtDateTime(iso: string | null): string {
  if (!iso) return '—';
  return iso.replace('T', ' ').slice(0, 16);
}

/** Estado abierto/cerrado a partir del status crudo de Cloudfleet. */
function isClosed(status: string | null): boolean {
  const s = (status ?? '').toLowerCase();
  return s.includes('close') || s.includes('terminad');
}

function StatusBadge({ status }: { status: string | null }) {
  const closed = isClosed(status);
  return (
    <span
      className={cn(
        'rounded-pill px-2 py-0.5 text-xs font-medium',
        closed ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700',
      )}
    >
      {status ?? '—'}
    </span>
  );
}

interface Props {
  rows: OrdenDetalle[];
  isLoading: boolean;
}

/**
 * Lista de OTs del periodo, más reciente primero. Cada fila expande (clic)
 * el motivo, la falla detectada y (si aplica) garantía/afecta disponibilidad.
 */
export function OrdenesTable({ rows, isLoading }: Props) {
  const [open, setOpen] = React.useState<Set<number>>(new Set());
  const toggle = (n: number) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(n)) next.delete(n);
      else next.add(n);
      return next;
    });

  return (
    <div className="bg-card rounded-lg border">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div>
          <h3 className="font-heading text-sm font-bold tracking-tight">Órdenes de trabajo</h3>
          <p className="text-muted-foreground flex items-center gap-1.5 text-xs">
            OTs iniciadas/creadas en el periodo, más recientes primero. Clic para ver el detalle.
            <span className="ml-1 inline-flex items-center gap-1">
              <span className="size-1.5 rounded-full bg-amber-500" />
              afecta disponibilidad
            </span>
          </p>
        </div>
        <span className="text-muted-foreground text-xs tabular-nums">
          {isLoading ? '…' : `${rows.length} OTs`}
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[860px] text-sm whitespace-nowrap">
          <thead>
            <tr className="text-muted-foreground border-b text-left text-xs">
              <th className="px-4 py-2 font-medium">OT</th>
              <th className="px-4 py-2 font-medium">Placa</th>
              <th className="px-4 py-2 font-medium">Tipo</th>
              <th className="px-4 py-2 font-medium">Estado</th>
              <th className="px-4 py-2 font-medium">Inicio</th>
              <th className="px-4 py-2 font-medium">Taller</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={6} className="text-muted-foreground px-4 py-8 text-center">
                  Cargando…
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={6} className="text-muted-foreground px-4 py-8 text-center">
                  Sin OTs en la selección.
                </td>
              </tr>
            ) : (
              rows.map((ot) => {
                const expanded = open.has(ot.number);
                return (
                  <React.Fragment key={ot.number}>
                    <tr
                      className="hover:bg-muted/40 cursor-pointer border-b last:border-0"
                      onClick={() => toggle(ot.number)}
                    >
                      <td className="px-4 py-2 font-mono font-medium">
                        <span className="flex items-center gap-1">
                          {expanded ? (
                            <ChevronDown size={14} className="shrink-0" />
                          ) : (
                            <ChevronRight size={14} className="shrink-0" />
                          )}
                          #{ot.number}
                          {ot.affectsAvailability && (
                            <span
                              className="size-1.5 shrink-0 rounded-full bg-amber-500"
                              title="Afecta disponibilidad"
                            />
                          )}
                        </span>
                      </td>
                      <td className="px-4 py-2 font-mono">{ot.plate}</td>
                      <td className="px-4 py-2">
                        <span className="max-w-[180px] truncate" title={ot.type ?? undefined}>
                          {ot.type ?? 'Sin tipo'}
                        </span>
                      </td>
                      <td className="px-4 py-2">
                        <StatusBadge status={ot.status} />
                      </td>
                      <td className="px-4 py-2 whitespace-nowrap">
                        {fmtDateTime(ot.startDate)}
                      </td>
                      <td className="px-4 py-2">
                        <span className="max-w-[200px] truncate" title={ot.vendor ?? undefined}>
                          {ot.vendor ?? '—'}
                        </span>
                      </td>
                    </tr>
                    {expanded && (
                      <tr className="bg-muted/20 border-b last:border-0">
                        <td />
                        <td colSpan={5} className="space-y-2 px-4 py-3 whitespace-normal">
                          {ot.reason && (
                            <p className="text-sm">
                              <span className="text-muted-foreground">Motivo: </span>
                              {ot.reason.trim()}
                            </p>
                          )}
                          {ot.detectedIssue && (
                            <p className="text-sm">
                              <span className="text-muted-foreground">Falla detectada: </span>
                              {ot.detectedIssue.trim()}
                            </p>
                          )}
                          {(ot.affectsAvailability || ot.warranty) && (
                            <div className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs">
                              {ot.affectsAvailability && (
                                <span className="text-amber-700">Afecta disponibilidad</span>
                              )}
                              {ot.warranty && <span className="text-emerald-700">Garantía</span>}
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
