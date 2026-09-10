'use client';

import * as React from 'react';
import Link from 'next/link';
import { toast } from 'sonner';
import {
  AlertCircle,
  CheckCircle2,
  DatabaseZap,
  Eye,
  RefreshCw,
  ShieldAlert,
  Truck,
  Wrench,
} from 'lucide-react';

import { SyncAuditSection } from '@/components/data-quality/sync-audit-section';
import { TrackingHealthCard } from '@/components/data-quality/tracking-health-card';
import { PageTitle } from '@/components/layout/page-title';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
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
import { UserPagination } from '@/components/users/user-pagination';
import { extractErrorMessage } from '@/lib/api-client';
import { useMe } from '@/lib/auth';
import { useDataQualitySummary, useDistanceAnomalies } from '@/lib/data-quality';
import { healthScoreTone, metricTone } from '@/lib/data-quality-status';
import { useResolveDistance } from '@/lib/reportes';
import type {
  DataQualityMetric,
  DistanceAnomaly,
  DistanceResolutionAction,
  DistanceReviewStatus,
} from '@/lib/types';
import { cn } from '@/lib/utils';

const TONE_STYLES = {
  success: {
    badge: 'success' as const,
    icon: CheckCircle2,
    panel: 'border-accent-lime/40 bg-accent-lime/10',
    text: 'text-[#3F5E00]',
  },
  warning: {
    badge: 'warning' as const,
    icon: AlertCircle,
    panel: 'border-accent-yellow/40 bg-accent-yellow/10',
    text: 'text-[#7A5300]',
  },
  critical: {
    badge: 'destructive' as const,
    icon: ShieldAlert,
    panel: 'border-destructive/30 bg-destructive/5',
    text: 'text-destructive',
  },
};

const DISTANCE_PAGE_SIZE = 20;

/**
 * El tono de cada estado responde a si queda algo por hacer. `no_data` se pinta
 * apagado y nunca en alerta porque no es una tarea: el día no tuvo distancia en
 * ninguna de las dos fuentes y no hay decisión humana que tomar. Separarlo del
 * amarillo de `pending` es justo el punto: lo pendiente espera a alguien.
 */
const REVIEW_STATUS_BADGE: Record<
  DistanceReviewStatus,
  { label: string; variant: 'outline' | 'warning' | 'default'; hint: string }
> = {
  pending: {
    label: 'Pendiente',
    variant: 'warning',
    hint: 'Requiere una decisión manual entre ECM y GPS.',
  },
  auto_corrected: {
    label: 'Autocorregido',
    variant: 'outline',
    hint: 'El ETL resolvió la fuente sin intervención.',
  },
  resolved: {
    label: 'Resuelto',
    variant: 'outline',
    hint: 'Un administrador publicó la fuente a usar.',
  },
  excluded: {
    label: 'Excluido',
    variant: 'outline',
    hint: 'El día quedó fuera de los reportes.',
  },
  no_data: {
    label: 'Sin dato',
    variant: 'default',
    hint: 'No hubo distancia ni en ECM ni en GPS: no hay nada que decidir.',
  },
};

function DistanceAnomaliesSection({ enabled }: { enabled: boolean }) {
  const [status, setStatus] = React.useState<DistanceReviewStatus | undefined>('pending');
  const [offset, setOffset] = React.useState(0);
  const query = useDistanceAnomalies(enabled, status, DISTANCE_PAGE_SIZE, offset);
  const resolveDistance = useResolveDistance();
  React.useEffect(() => setOffset(0), [status]);

  const applyResolution = React.useCallback(
    async (item: DistanceAnomaly, action: DistanceResolutionAction) => {
      if (!item.distance_quality_fingerprint) {
        toast.error('La observación no tiene una huella válida. Actualiza la lista.');
        return;
      }
      const labels: Record<DistanceResolutionAction, string> = {
        use_ecm: 'usar los kilómetros del ECM',
        use_gps: 'usar GPS',
        exclude: 'excluir la distancia',
        restore_auto: 'restaurar la decisión automática',
      };
      const reason = window.prompt(`Motivo para ${labels[action]} (mínimo 10 caracteres):`);
      if (reason === null) return;
      if (reason.trim().length < 10) {
        toast.error('El motivo debe tener al menos 10 caracteres.');
        return;
      }

      try {
        await resolveDistance.mutateAsync({
          factRowId: item.fact_row_id,
          action,
          reason: reason.trim(),
          expectedFingerprint: item.distance_quality_fingerprint,
        });
        toast.success('Decisión de distancia aplicada.');
      } catch (error) {
        toast.error(extractErrorMessage(error, 'No se pudo aplicar la decisión.'));
      }
    },
    [resolveDistance],
  );

  return (
    <section className="space-y-3 rounded-lg border bg-white p-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-heading text-lg font-extrabold">Desfase diario ECM–GPS</h2>
          <p className="text-muted-foreground text-xs">
            Distancias autocorregidas, pendientes o excluidas sin modificar las fuentes crudas. «Sin
            dato» agrupa los días sin lectura en ninguna de las dos fuentes: no son tareas.
          </p>
        </div>
        <select
          className="border-input bg-background h-9 rounded-md border px-3 text-sm"
          value={status ?? ''}
          onChange={(event) =>
            setStatus((event.target.value || undefined) as DistanceReviewStatus | undefined)
          }
          aria-label="Filtrar estado de revisión"
        >
          <option value="">Todos</option>
          <option value="pending">Pendientes</option>
          <option value="auto_corrected">Autocorregidos</option>
          <option value="resolved">Resueltos</option>
          <option value="excluded">Excluidos</option>
          <option value="no_data">Sin dato (ECM y GPS vacíos)</option>
        </select>
      </div>
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Fecha</TableHead>
              <TableHead>Placa</TableHead>
              <TableHead className="text-right">ECM</TableHead>
              <TableHead className="text-right">GPS</TableHead>
              <TableHead className="text-right">Efectivos</TableHead>
              <TableHead className="text-right">Diferencia</TableHead>
              <TableHead>Fuente</TableHead>
              <TableHead>Estado</TableHead>
              <TableHead>Motivo</TableHead>
              <TableHead>Acciones</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {query.isLoading && (
              <TableRow>
                <TableCell colSpan={10}>
                  <Skeleton className="h-8 w-full" />
                </TableCell>
              </TableRow>
            )}
            {query.isError && (
              <TableRow>
                <TableCell colSpan={10} className="text-destructive text-center">
                  No se pudieron cargar las anomalías de distancia. Intenta nuevamente.
                </TableCell>
              </TableRow>
            )}
            {!query.isLoading && query.data?.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={10} className="text-muted-foreground text-center">
                  Sin hallazgos para este filtro.
                </TableCell>
              </TableRow>
            )}
            {query.data?.items.map((item) => (
              <TableRow key={item.fact_row_id}>
                <TableCell>{item.fecha ?? '—'}</TableCell>
                <TableCell className="font-semibold">{item.placa ?? '—'}</TableCell>
                <TableCell className="text-right">
                  {item.kms_ecm?.toLocaleString('es-CO') ?? '—'}
                </TableCell>
                <TableCell className="text-right">
                  {item.kms_gps?.toLocaleString('es-CO') ?? '—'}
                </TableCell>
                <TableCell className="text-right">
                  {item.kms_effective?.toLocaleString('es-CO') ?? '—'}
                </TableCell>
                <TableCell className="text-right">
                  {item.distance_diff_pct == null
                    ? '—'
                    : `${(item.distance_diff_pct * 100).toLocaleString('es-CO', { maximumFractionDigits: 1 })}%`}
                </TableCell>
                <TableCell>
                  <Badge variant="outline">{item.distance_source ?? 'none'}</Badge>
                </TableCell>
                <TableCell>
                  <Badge
                    variant={
                      (REVIEW_STATUS_BADGE[item.review_status] ?? REVIEW_STATUS_BADGE.pending)
                        .variant
                    }
                    title={
                      (REVIEW_STATUS_BADGE[item.review_status] ?? REVIEW_STATUS_BADGE.pending).hint
                    }
                  >
                    {(REVIEW_STATUS_BADGE[item.review_status] ?? REVIEW_STATUS_BADGE.pending).label}
                  </Badge>
                </TableCell>
                <TableCell className="max-w-64 text-xs">
                  {item.distance_quality_reason ?? '—'}
                </TableCell>
                <TableCell>
                  {/* Sin lectura en ninguna de las dos fuentes no hay kilometraje que
                      publicar, así que ofrecer botones invitaría a una decisión imposible. */}
                  {item.review_status === 'no_data' ? (
                    <span className="text-muted-foreground text-xs">Sin acción disponible</span>
                  ) : (
                    <div className="flex min-w-48 flex-wrap gap-1">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        title="Publica el kilometraje del ECM. Aplica cuando el ECM es coherente y el GPS perdió viajes."
                        disabled={
                          item.kms_ecm == null ||
                          !item.distance_quality_fingerprint ||
                          resolveDistance.isPending
                        }
                        onClick={() => applyResolution(item, 'use_ecm')}
                      >
                        Usar ECM
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        title="Publica el kilometraje del GPS. Aplica cuando el GPS es utilizable y el ECM no lo es."
                        disabled={
                          !item.gps_quality_valid ||
                          !item.distance_quality_fingerprint ||
                          resolveDistance.isPending
                        }
                        onClick={() => applyResolution(item, 'use_gps')}
                      >
                        Usar GPS
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={!item.distance_quality_fingerprint || resolveDistance.isPending}
                        onClick={() => applyResolution(item, 'exclude')}
                      >
                        Excluir
                      </Button>
                      {item.resolution_action && (
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          disabled={!item.distance_quality_fingerprint || resolveDistance.isPending}
                          onClick={() => applyResolution(item, 'restore_auto')}
                        >
                          Restaurar
                        </Button>
                      )}
                    </div>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <UserPagination
        total={query.data?.total ?? 0}
        limit={DISTANCE_PAGE_SIZE}
        offset={offset}
        onOffsetChange={setOffset}
      />
    </section>
  );
}

function MetricCard({
  metric,
  onViewDetails,
  canManageDistance,
  onManageDistance,
}: {
  metric: DataQualityMetric;
  onViewDetails: (metric: DataQualityMetric) => void;
  canManageDistance: boolean;
  onManageDistance: () => void;
}) {
  const tone = metricTone(metric);
  const style = TONE_STYLES[tone];
  const Icon = style.icon;

  return (
    <article className={cn('rounded-lg border p-4', style.panel)}>
      <div className="flex items-start justify-between gap-3">
        <Icon className={cn('mt-0.5 h-5 w-5 shrink-0', style.text)} aria-hidden />
        <Badge variant={style.badge}>{metric.count === 0 ? 'Sin hallazgos' : metric.count}</Badge>
      </div>
      <h2 className="mt-3 text-sm font-bold">{metric.label}</h2>
      <p className="text-muted-foreground mt-1 text-xs leading-relaxed">{metric.description}</p>
      {metric.key === 'distance_ecm_gps_anomalies' && canManageDistance ? (
        <div className="border-current/10 mt-3 border-t pt-3">
          <Button
            type="button"
            variant="link"
            size="sm"
            className="h-auto px-0 text-xs"
            onClick={onManageDistance}
          >
            <Eye aria-hidden />
            Gestionar pendientes
          </Button>
        </div>
      ) : metric.count > 0 && metric.examples.length > 0 ? (
        <div className="border-current/10 mt-3 border-t pt-3">
          <Button
            type="button"
            variant="link"
            size="sm"
            className="h-auto px-0 text-xs"
            onClick={() => onViewDetails(metric)}
          >
            <Eye aria-hidden />
            Ver detalles
          </Button>
        </div>
      ) : null}
    </article>
  );
}

function MetricDetailsDialog({
  metric,
  onOpenChange,
}: {
  metric: DataQualityMetric | null;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={metric !== null} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[min(85vh,680px)] max-w-2xl flex-col overflow-hidden">
        {metric && (
          <>
            <DialogHeader>
              <DialogTitle>{metric.label}</DialogTitle>
              <DialogDescription>
                {metric.count} hallazgo{metric.count === 1 ? '' : 's'} detectado
                {metric.count === 1 ? '' : 's'}. Estos son los ejemplos disponibles para revisar.
              </DialogDescription>
            </DialogHeader>
            <div className="min-h-0 flex-1 overflow-y-auto pr-1">
              <ul className="space-y-3">
                {metric.examples.map((example) => (
                  <li
                    key={`${example.label}-${example.detail ?? ''}`}
                    className="bg-background/70 rounded-md border p-3 text-sm"
                  >
                    <span className="font-semibold">{example.label}</span>
                    {example.detail && (
                      <span className="text-muted-foreground mt-1 block leading-relaxed">
                        {example.detail}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
              {metric.count > metric.examples.length && (
                <p className="text-muted-foreground mt-3 text-xs">
                  + {metric.count - metric.examples.length} hallazgos adicionales no incluidos en la
                  muestra.
                </p>
              )}
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

export default function CalidadDatosPage() {
  const [selectedMetric, setSelectedMetric] = React.useState<DataQualityMetric | null>(null);
  const [distanceManagerOpen, setDistanceManagerOpen] = React.useState(false);
  const me = useMe();
  const isAdmin = me.data?.user.roles.some((role) => role.code === 'admin') ?? false;
  const canView = isAdmin || (me.data?.permissions.includes('calidad_datos.view') ?? false);
  const canViewFleets = isAdmin || (me.data?.permissions.includes('flotas.view') ?? false);
  const canViewVehicles = isAdmin || (me.data?.permissions.includes('reportes.view') ?? false);
  const summary = useDataQualitySummary(Boolean(me.data) && canView);

  if (me.isLoading || (canView && summary.isLoading)) {
    return (
      <section className="space-y-6" aria-label="Cargando calidad de datos">
        <Skeleton className="h-14 w-80" />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} className="h-28" />
          ))}
        </div>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, index) => (
            <Skeleton key={index} className="h-36" />
          ))}
        </div>
      </section>
    );
  }

  if (!canView) {
    return (
      <section className="border-destructive/30 bg-destructive/5 rounded-lg border p-8 text-center">
        <ShieldAlert className="text-destructive mx-auto h-8 w-8" aria-hidden />
        <h1 className="mt-3 text-lg font-bold">Sin acceso a Calidad de datos</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Solicita el permiso calidad_datos.view a un administrador.
        </p>
      </section>
    );
  }

  if (summary.isError || !summary.data) {
    return (
      <section className="rounded-lg border bg-white p-8 text-center">
        <AlertCircle className="text-destructive mx-auto h-8 w-8" aria-hidden />
        <h1 className="mt-3 text-lg font-bold">No pudimos calcular la calidad de datos</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Conservamos el último estado en caché y puedes intentar de nuevo.
        </p>
        <Button className="mt-4" onClick={() => summary.refetch()}>
          <RefreshCw aria-hidden /> Reintentar
        </Button>
      </section>
    );
  }

  const data = summary.data;
  const scoreTone = healthScoreTone(data.health_score);
  const scoreStyle = TONE_STYLES[scoreTone];
  const latestSync = data.latest_master_sync_at
    ? new Intl.DateTimeFormat('es-CO', { dateStyle: 'medium', timeStyle: 'short' }).format(
        new Date(data.latest_master_sync_at),
      )
    : 'Sin sincronización registrada';

  return (
    <section className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <PageTitle
          icon={DatabaseZap}
          title="Calidad de datos"
          description="Diagnóstico de fuente maestra, Geotab y extracción para la flota seleccionada."
          iconClassName="bg-accent-blue/15 text-accent-blue"
        />
        <Button variant="outline" onClick={() => summary.refetch()} disabled={summary.isFetching}>
          <RefreshCw className={summary.isFetching ? 'animate-spin' : undefined} aria-hidden />
          Actualizar
        </Button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <article className={cn('rounded-lg border p-5', scoreStyle.panel)}>
          <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
            Puntaje maestro
          </p>
          <p className={cn('mt-2 text-3xl font-extrabold', scoreStyle.text)}>
            {data.health_score.toFixed(1)}%
          </p>
          <p className="text-muted-foreground mt-1 text-xs">
            {data.healthy_vehicles} de {data.active_vehicles} vehículos completos
          </p>
        </article>
        <article className="rounded-lg border bg-white p-5">
          <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
            Vehículos activos
          </p>
          <p className="mt-2 text-3xl font-extrabold">{data.active_vehicles}</p>
          <p className="text-muted-foreground mt-1 text-xs">
            En {data.fleet_count} flota{data.fleet_count === 1 ? '' : 's'} del filtro
          </p>
        </article>
        <article className="rounded-lg border bg-white p-5">
          <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
            Bases Geotab
          </p>
          <p className="mt-2 text-3xl font-extrabold">{data.geotab_databases}</p>
          <p className="text-muted-foreground mt-1 text-xs">
            Con ventana de vigencia de {data.stale_after_hours} horas
          </p>
        </article>
        <article className="rounded-lg border bg-white p-5">
          <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
            Última réplica maestra
          </p>
          <p className="mt-3 text-sm font-bold">{latestSync}</p>
          <p className="text-muted-foreground mt-1 text-xs">
            Actualización más reciente del alcance
          </p>
        </article>
        <TrackingHealthCard enabled={Boolean(me.data) && canView} />
      </div>

      <div>
        <div className="mb-3 flex items-end justify-between gap-3">
          <div>
            <h2 className="font-heading text-lg font-extrabold">Hallazgos accionables</h2>
            <p className="text-muted-foreground text-xs">
              Un mismo vehículo puede aparecer en varias categorías.
            </p>
          </div>
          <span className="text-muted-foreground text-xs">
            Generado{' '}
            {new Intl.DateTimeFormat('es-CO', { timeStyle: 'short' }).format(
              new Date(data.generated_at),
            )}
          </span>
        </div>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {data.metrics.map((metric) => (
            <MetricCard
              key={metric.key}
              metric={metric}
              onViewDetails={setSelectedMetric}
              canManageDistance={isAdmin}
              onManageDistance={() => setDistanceManagerOpen(true)}
            />
          ))}
        </div>
      </div>

      <MetricDetailsDialog
        metric={selectedMetric}
        onOpenChange={(open) => {
          if (!open) setSelectedMetric(null);
        }}
      />

      <Dialog open={distanceManagerOpen} onOpenChange={setDistanceManagerOpen}>
        <DialogContent className="flex max-h-[90vh] max-w-[min(96vw,1400px)] flex-col overflow-hidden">
          <DialogHeader>
            <DialogTitle>Gestión administrativa de distancias</DialogTitle>
            <DialogDescription>
              Revisa únicamente los casos que requieren una decisión manual. Esta información no se
              publica en los reportes del cliente.
            </DialogDescription>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-y-auto">
            <DistanceAnomaliesSection enabled={isAdmin && distanceManagerOpen} />
          </div>
        </DialogContent>
      </Dialog>

      <SyncAuditSection enabled={canView} />

      {(canViewFleets || canViewVehicles) && (
        <div className="grid gap-4 md:grid-cols-2">
          {canViewFleets && (
            <Link
              href="/gestion/flotas"
              className="hover:border-accent-blue/50 group rounded-lg border bg-white p-5 transition-colors"
            >
              <div className="flex items-center gap-3">
                <Truck className="text-accent-blue h-5 w-5" aria-hidden />
                <div>
                  <p className="text-sm font-bold group-hover:underline">
                    Sincronizar fuente maestra
                  </p>
                  <p className="text-muted-foreground text-xs">
                    Revisa flotas, bases y credenciales.
                  </p>
                </div>
              </div>
            </Link>
          )}
          {canViewVehicles && (
            <Link
              href="/vehiculos"
              className="hover:border-accent-yellow/60 group rounded-lg border bg-white p-5 transition-colors"
            >
              <div className="flex items-center gap-3">
                <Wrench className="text-accent-yellow h-5 w-5" aria-hidden />
                <div>
                  <p className="text-sm font-bold group-hover:underline">Completar clasificación</p>
                  <p className="text-muted-foreground text-xs">
                    Corrige motor y vínculos de vehículo.
                  </p>
                </div>
              </div>
            </Link>
          )}
        </div>
      )}
    </section>
  );
}
