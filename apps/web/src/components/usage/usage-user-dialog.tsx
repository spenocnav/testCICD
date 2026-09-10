'use client';

import * as React from 'react';

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { sectionLabel, useUsageUserDetail, type UsageRange } from '@/lib/usage';

interface UsageUserDialogProps {
  userId: string | null;
  range: UsageRange;
  onClose: () => void;
}

function formatDateTime(value: string | null): string {
  if (!value) return '—';
  try {
    return new Date(value).toLocaleString('es-CO', { dateStyle: 'short', timeStyle: 'short' });
  } catch {
    return value;
  }
}

/** Desglose de la actividad de UN usuario. Conserva el último id al cerrarse
 *  (misma disciplina que el visor de evidencias): Radix mantiene el diálogo
 *  montado durante la animación y un contenido vaciado parpadea. */
export function UsageUserDialog({ userId, range, onClose }: UsageUserDialogProps) {
  const [lastUserId, setLastUserId] = React.useState<string | null>(null);
  if (userId !== null && userId !== lastUserId) setLastUserId(userId);

  const detail = useUsageUserDetail(userId ?? lastUserId, range);
  const data = detail.data;
  const maxDaily = Math.max(1, ...(data?.daily.map((d) => d.requests) ?? [1]));

  return (
    <Dialog open={userId !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{data?.full_name ?? 'Actividad del usuario'}</DialogTitle>
          <DialogDescription>
            {data?.email ?? data?.user_id ?? ''} · {range.startDate} → {range.endDate}
          </DialogDescription>
        </DialogHeader>

        {detail.isLoading && !data && <Skeleton className="h-64 w-full" />}
        {detail.isError && (
          <p className="text-destructive text-sm">No se pudo cargar el desglose.</p>
        )}

        {data && (
          <div className="space-y-5">
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div className="border-border rounded-lg border p-3">
                <p className="text-muted-foreground text-xs">Peticiones en el rango</p>
                <p className="text-lg font-semibold tabular-nums">
                  {data.total_requests.toLocaleString('es-CO')}
                </p>
              </div>
              <div className="border-border rounded-lg border p-3">
                <p className="text-muted-foreground text-xs">Última actividad</p>
                <p className="text-lg font-semibold">{formatDateTime(data.last_seen)}</p>
              </div>
            </div>

            {data.daily.length > 0 && (
              <div>
                <h3 className="mb-2 text-sm font-semibold">Actividad por día</h3>
                <div className="flex h-20 items-end gap-px">
                  {data.daily.map((d) => (
                    <div
                      key={d.day}
                      className="bg-primary flex-1 rounded-t opacity-70"
                      title={`${d.day}: ${d.requests}`}
                      style={{ height: `${Math.max(4, (d.requests / maxDaily) * 100)}%` }}
                    />
                  ))}
                </div>
              </div>
            )}

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <h3 className="mb-2 text-sm font-semibold">Secciones</h3>
                {data.sections.map((s) => (
                  <p key={s.section} className="flex justify-between py-0.5 text-sm">
                    <span>{sectionLabel(s.section)}</span>
                    <span className="tabular-nums">{s.requests.toLocaleString('es-CO')}</span>
                  </p>
                ))}
                {data.sections.length === 0 && (
                  <p className="text-muted-foreground text-sm">Sin datos.</p>
                )}
              </div>
              <div>
                <h3 className="mb-2 text-sm font-semibold">Flotas consultadas</h3>
                {data.fleets.map((f) => (
                  <p key={f.fleet_id} className="flex justify-between py-0.5 text-sm">
                    <span className="truncate" title={f.fleet_name ?? f.fleet_id}>
                      {f.fleet_name ?? f.fleet_id}
                    </span>
                    <span className="tabular-nums">{f.requests.toLocaleString('es-CO')}</span>
                  </p>
                ))}
                {data.fleets.length === 0 && (
                  <p className="text-muted-foreground text-sm">Sin filtro de flota aplicado.</p>
                )}
              </div>
            </div>

            <div>
              <h3 className="mb-2 text-sm font-semibold">Endpoints</h3>
              <div className="max-h-56 overflow-y-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Ruta</TableHead>
                      <TableHead className="text-right">Peticiones</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.routes.map((r) => (
                      <TableRow key={`${r.method} ${r.route}`}>
                        <TableCell>
                          <code className="text-xs">
                            {r.method} {r.route}
                          </code>
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {r.requests.toLocaleString('es-CO')}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
