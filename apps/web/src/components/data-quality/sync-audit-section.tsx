'use client';

import * as React from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  History,
  Loader2,
  RefreshCw,
  XCircle,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useHasPermission, useMe } from '@/lib/auth';
import { extractErrorMessage } from '@/lib/api-client';
import { useSyncRuns, useTriggerReportesEtl } from '@/lib/data-quality';
import { useSyncMasterData } from '@/lib/fleets';
import { useSyncCloudfleet } from '@/lib/mantenimiento';
import { SyncRunDetailDialog } from '@/components/data-quality/sync-run-detail-dialog';
import { UserPagination } from '@/components/users/user-pagination';
import type { ActiveSyncRun, SyncRun, SyncRunKind } from '@/lib/types';
import { cn } from '@/lib/utils';

const KIND_LABEL: Record<SyncRunKind, string> = {
  cloudfleet: 'CloudFleet',
  master: 'Fuente maestra',
  novedades: 'Estado de novedades',
  reportes: 'Reportes',
};

const FILTERS: { value: SyncRunKind | 'all'; label: string }[] = [
  { value: 'all', label: 'Todos' },
  { value: 'cloudfleet', label: 'CloudFleet' },
  { value: 'master', label: 'Fuente maestra' },
  { value: 'novedades', label: 'Estado de novedades' },
  { value: 'reportes', label: 'Reportes' },
];

const dateFmt = new Intl.DateTimeFormat('es-CO', {
  dateStyle: 'medium',
  timeStyle: 'short',
});

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} s`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s % 60);
  return `${m}m ${rem}s`;
}

/** Transcurrido de una corrida en vuelo, en mm:ss. */
function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

/**
 * Cronómetro para las corridas en vuelo.
 *
 * El servidor manda `elapsed_ms` (su reloj es la verdad, el del navegador puede
 * estar corrido); acá solo se le suma el tiempo local desde que llegó la
 * respuesta, y cada refetch vuelve a anclarlo. Un solo intervalo para toda la
 * sección en vez de uno por fila.
 */
function useElapsedTicker(active: ActiveSyncRun[]): (run: ActiveSyncRun) => number {
  const [, forceTick] = React.useReducer((n: number) => n + 1, 0);
  const anchorRef = React.useRef(Date.now());

  // Cada respuesta nueva reancla el origen local. Se compara por id+elapsed para
  // no reanclar en renders que no traen datos frescos.
  const signature = active.map((r) => `${r.id}:${r.elapsed_ms}`).join('|');
  React.useEffect(() => {
    anchorRef.current = Date.now();
  }, [signature]);

  const hasActive = active.length > 0;
  React.useEffect(() => {
    if (!hasActive) return;
    const id = window.setInterval(forceTick, 1000);
    return () => window.clearInterval(id);
  }, [hasActive]);

  return (run: ActiveSyncRun) => run.elapsed_ms + (Date.now() - anchorRef.current);
}

/** Fila de una corrida en curso. Ocupa las mismas columnas que el historial. */
function ActiveRunRow({ run, elapsedMs }: { run: ActiveSyncRun; elapsedMs: number }) {
  const queued = run.status === 'pending';
  const progressLabel =
    run.kind === 'cloudfleet'
      ? 'consultando vehículos, OTs y cronogramas'
      : run.kind === 'reportes'
        ? 'extract → transform → load'
        : run.kind === 'novedades'
          ? 'consultando issues resueltas en CloudFleet'
          : 'sincronizando datos';
  return (
    <TableRow className="bg-accent-blue/5">
      <TableCell>
        <Badge variant="outline" className="border-accent-blue text-accent-blue gap-1">
          <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
          {queued ? 'En cola' : 'En proceso'}
        </Badge>
      </TableCell>
      <TableCell className="font-semibold">{KIND_LABEL[run.kind]}</TableCell>
      <TableCell className="whitespace-nowrap text-xs">
        {dateFmt.format(new Date(run.started_at ?? run.queued_at))}
      </TableCell>
      <TableCell className="whitespace-nowrap text-xs font-semibold tabular-nums">
        <span aria-live="polite">{formatElapsed(elapsedMs)}</span>
      </TableCell>
      <TableCell className="text-muted-foreground whitespace-nowrap text-right text-xs">
        {queued ? 'esperando al worker' : progressLabel}
      </TableCell>
    </TableRow>
  );
}

function StatusBadge({ status }: { status: SyncRun['status'] }) {
  if (status === 'success') {
    return (
      <Badge variant="success" className="gap-1">
        <CheckCircle2 className="h-3 w-3" aria-hidden /> OK
      </Badge>
    );
  }
  if (status === 'partial') {
    return (
      <Badge variant="warning" className="gap-1">
        <AlertCircle className="h-3 w-3" aria-hidden /> Parcial
      </Badge>
    );
  }
  return (
    <Badge variant="destructive" className="gap-1">
      <XCircle className="h-3 w-3" aria-hidden /> Error
    </Badge>
  );
}

/** Botonera de disparo manual. CloudFleet y Fuente maestra pegan a sus
 * endpoints (trigger=manual). Reportes ENCOLA una corrida del ETL
 * (InformesRendimiento) que el worker toma en segundos — es asíncrono: el
 * resultado aparece en la tabla cuando el pipeline termina. */
function ManualTriggers({
  etlBusy,
  cloudfleetBusy,
}: {
  etlBusy: boolean;
  cloudfleetBusy: boolean;
}) {
  const qc = useQueryClient();
  const has = useHasPermission();
  const { data: me } = useMe();
  const cloudfleet = useSyncCloudfleet();
  const master = useSyncMasterData();
  const reportes = useTriggerReportesEtl();

  const invalidateRuns = () => qc.invalidateQueries({ queryKey: ['data-quality', 'sync-runs'] });

  const isPlatformAdmin = me?.user.roles.some((role) => role.code === 'admin') ?? false;
  const canCloudfleet = isPlatformAdmin && has('mantenimiento.edit');
  const canMaster = isPlatformAdmin;
  const canReportes = isPlatformAdmin;
  const mutationBusy = cloudfleet.isPending || master.isPending || reportes.isPending;

  const runReportes = async () => {
    try {
      await reportes.mutateAsync();
      toast.success(
        'Corrida de reportes encolada. El worker la ejecuta en segundos; ' +
          'el resultado aparecerá en la tabla al terminar (puede tardar varios minutos).',
      );
    } catch (err) {
      toast.error(extractErrorMessage(err, 'No se pudo encolar la corrida de reportes'));
    } finally {
      invalidateRuns();
    }
  };

  const runCloudfleet = async () => {
    // La mutación permanece abierta hasta terminar toda la réplica. Refrescar
    // poco después de iniciarla permite que la fila durable `running` aparezca
    // sin esperar el polling de reposo (30 s).
    const refreshTimer = window.setTimeout(() => {
      void invalidateRuns();
    }, 500);
    try {
      const r = await cloudfleet.mutateAsync(false);
      const meterStatus =
        r.metersFailed || r.metersUncertain
          ? ` Medidores: ${r.metersSent} enviados; ${r.metersFailed + r.metersUncertain} requieren revisión.`
          : ` Medidores: ${r.metersSent} enviados.`;
      toast.success(
        `CloudFleet: ${r.workOrdersUpserted} OTs, ${r.schedulesInserted} cronogramas, ` +
          `${r.vehiclesUpserted} vehículos.${meterStatus}`,
      );
    } catch (err) {
      toast.error(extractErrorMessage(err, 'No se pudo sincronizar CloudFleet'));
    } finally {
      window.clearTimeout(refreshTimer);
      invalidateRuns();
    }
  };

  const runMaster = async () => {
    try {
      const r = await master.mutateAsync(false);
      toast.success(
        `Fuente maestra: ${r.vehicles} vehículos, ${r.fleets} flotas, ${r.rules} reglas.`,
      );
    } catch (err) {
      toast.error(extractErrorMessage(err, 'No se pudo sincronizar la fuente maestra'));
    } finally {
      invalidateRuns();
    }
  };

  if (!canCloudfleet && !canMaster && !canReportes) return null;

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-white px-3 py-2">
      <span className="text-muted-foreground text-xs font-semibold">Disparar manual:</span>
      {canCloudfleet && (
        <Button
          variant="outline"
          size="sm"
          onClick={runCloudfleet}
          disabled={mutationBusy || cloudfleetBusy}
        >
          <RefreshCw className={cloudfleet.isPending ? 'animate-spin' : undefined} aria-hidden />
          CloudFleet
        </Button>
      )}
      {canMaster && (
        <Button variant="outline" size="sm" onClick={runMaster} disabled={mutationBusy}>
          <RefreshCw className={master.isPending ? 'animate-spin' : undefined} aria-hidden />
          Fuente maestra
        </Button>
      )}
      {canReportes && (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              {/* span: un Button deshabilitado no dispara eventos y el tooltip
                  se quedaría mudo justo cuando hay algo que explicar. */}
              <span className="inline-flex">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={runReportes}
                  disabled={mutationBusy || etlBusy}
                >
                  <RefreshCw
                    className={reportes.isPending ? 'animate-spin' : undefined}
                    aria-hidden
                  />
                  Reportes
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent>
              {etlBusy
                ? 'Ya hay una corrida de reportes en curso; esperá a que termine.'
                : 'Encola el ETL de reportes (Geotab → analytics). Corre en segundo plano; el resultado aparece en la tabla al terminar.'}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      )}
    </div>
  );
}

const PAGE_SIZE = 10;

export function SyncAuditSection({ enabled }: { enabled: boolean }) {
  const [filter, setFilter] = React.useState<SyncRunKind | 'all'>('all');
  const [offset, setOffset] = React.useState(0);
  const [detail, setDetail] = React.useState<SyncRun | null>(null);

  const runs = useSyncRuns(enabled, filter === 'all' ? undefined : filter, PAGE_SIZE, offset);
  const items = runs.data?.items ?? [];
  const active = runs.data?.active ?? [];
  const total = runs.data?.total ?? 0;
  const elapsedOf = useElapsedTicker(active);

  // Cambiar de filtro con offset viejo pediría una página que quizá no existe.
  // Se resetea en el mismo handler (no en un efecto) para no emitir dos queries.
  const changeFilter = (value: SyncRunKind | 'all') => {
    setFilter(value);
    setOffset(0);
  };

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex items-center gap-2">
          <History className="text-accent-blue h-5 w-5" aria-hidden />
          <div>
            <h2 className="font-heading text-lg font-extrabold">Auditoría de syncs</h2>
            <p className="text-muted-foreground text-xs">
              Corridas de CloudFleet, fuente maestra y reportes (worker y manuales). Podés
              dispararlas a mano abajo.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="bg-muted flex rounded-lg p-0.5">
            {FILTERS.map((f) => (
              <button
                key={f.value}
                type="button"
                onClick={() => changeFilter(f.value)}
                className={cn(
                  'rounded-md px-3 py-1 text-xs font-semibold transition-colors',
                  filter === f.value
                    ? 'bg-white shadow-sm'
                    : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {f.label}
              </button>
            ))}
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => runs.refetch()}
            disabled={runs.isFetching}
          >
            <RefreshCw className={runs.isFetching ? 'animate-spin' : undefined} aria-hidden />
          </Button>
        </div>
      </div>

      <ManualTriggers
        etlBusy={active.some((r) => r.kind === 'reportes')}
        cloudfleetBusy={active.some((r) => r.kind === 'cloudfleet')}
      />

      <div className="overflow-x-auto rounded-lg border bg-white">
        {runs.isLoading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : runs.isError ? (
          <div className="p-8 text-center">
            <AlertCircle className="text-destructive mx-auto h-6 w-6" aria-hidden />
            <p className="text-muted-foreground mt-2 text-sm">
              No pudimos cargar el historial de syncs.
            </p>
          </div>
        ) : items.length === 0 && active.length === 0 ? (
          <p className="text-muted-foreground p-8 text-center text-sm">
            Aún no hay corridas registradas.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Estado</TableHead>
                <TableHead>Tipo</TableHead>
                <TableHead>Cuándo</TableHead>
                <TableHead>Duración</TableHead>
                <TableHead className="w-px" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {/* En vuelo primero: es lo que el usuario está esperando. */}
              {active.map((run) => (
                <ActiveRunRow key={run.id} run={run} elapsedMs={elapsedOf(run)} />
              ))}
              {items.map((run) => (
                <TableRow
                  key={run.id}
                  // La fila entera abre el detalle, pero el foco de teclado vive
                  // en el botón de la última celda para no crear dos tab stops.
                  onClick={() => setDetail(run)}
                  className="hover:bg-muted/40 cursor-pointer"
                >
                  <TableCell>
                    <StatusBadge status={run.status} />
                  </TableCell>
                  <TableCell className="font-semibold">{KIND_LABEL[run.kind]}</TableCell>
                  <TableCell className="whitespace-nowrap text-xs">
                    {dateFmt.format(new Date(run.started_at))}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs tabular-nums">
                    {formatDuration(run.duration_ms)}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={(event) => {
                        event.stopPropagation();
                        setDetail(run);
                      }}
                    >
                      Detalle
                      <ChevronRight aria-hidden />
                      <span className="sr-only">
                        de la corrida de {KIND_LABEL[run.kind]} del{' '}
                        {dateFmt.format(new Date(run.started_at))}
                      </span>
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      {total > PAGE_SIZE && (
        <UserPagination
          total={total}
          limit={PAGE_SIZE}
          offset={offset}
          onOffsetChange={setOffset}
        />
      )}

      <SyncRunDetailDialog run={detail} onOpenChange={(open) => !open && setDetail(null)} />
    </section>
  );
}
