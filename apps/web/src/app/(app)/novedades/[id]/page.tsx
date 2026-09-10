'use client';

import { useState, type ReactNode } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import {
  ArrowLeft,
  CheckCircle2,
  FileVideo,
  ImageIcon,
  Loader2,
  RefreshCw,
  Trash2,
} from 'lucide-react';
import { toast } from 'sonner';

import { Can } from '@/components/auth/can';
import { ImageViewer } from '@/components/novedades/image-viewer';
import {
  CLOUDFLEET_STATUS_META,
  PRIORITY_LABEL,
  externalResolution,
  formatNovedadDate,
  lifecycle,
} from '@/components/novedades/novedad-status';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Separator } from '@/components/ui/separator';
import { extractErrorMessage } from '@/lib/api-client';
import { useMe } from '@/lib/auth';
import { useDeleteNovedad, useNovedad, useRetryNovedad } from '@/lib/novedades';

function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <p className="text-muted-foreground text-xs font-medium uppercase">{label}</p>
      <div className="mt-1 text-sm font-medium">{value || '—'}</div>
    </div>
  );
}

export default function NovedadDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const router = useRouter();
  const { data: novedad, isLoading, isError } = useNovedad(id);
  const retry = useRetryNovedad(id);
  const remove = useDeleteNovedad(id);
  const { data: me } = useMe();
  // Borrar es una operación global de administrador (la issue de CloudFleet
  // queda viva); el backend lo exige y aquí sólo se oculta el botón.
  const isAdmin = me?.user.roles.some((role) => role.code === 'admin') ?? false;

  // `viewer` conserva el último adjunto abierto mientras el diálogo termina su
  // animación de cierre; `viewerOpen` es lo único que cambia al cerrar. Si se
  // pusiera `viewer` en null al cerrar, Radix seguiría montando el contenido
  // un instante y `<Image>` recibiría `src=""`.
  const [viewer, setViewer] = useState<{ src: string; filename: string; isVideo: boolean } | null>(
    null,
  );
  const [viewerOpen, setViewerOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  async function onRetry() {
    try {
      const updated = await retry.mutateAsync();
      if (updated.cloudfleet_status === 'sent') {
        toast.success('Novedad enviada a Cloudfleet');
      } else {
        toast.warning('Cloudfleet aún no recibió la novedad');
      }
    } catch (err) {
      toast.error(extractErrorMessage(err, 'No se pudo reintentar'));
    }
  }

  async function onDelete() {
    try {
      await remove.mutateAsync(id);
      toast.success('Novedad eliminada del portal');
      router.replace('/novedades');
    } catch (err) {
      toast.error(extractErrorMessage(err, 'No se pudo eliminar la novedad'));
    } finally {
      setConfirmDelete(false);
    }
  }

  if (isLoading) {
    return (
      <section className="flex min-h-[50dvh] items-center justify-center">
        <span className="text-muted-foreground inline-flex items-center gap-2 text-sm">
          <Loader2 className="h-4 w-4 animate-spin" />
          Cargando novedad...
        </span>
      </section>
    );
  }

  if (isError || !novedad) {
    return (
      <section className="flex min-h-[50dvh] flex-col items-center justify-center gap-4">
        <Button variant="ghost" className="h-11" asChild>
          <Link href="/novedades">
            <ArrowLeft className="h-4 w-4" />
            Volver
          </Link>
        </Button>
        <p className="text-muted-foreground text-center">Novedad no encontrada.</p>
      </section>
    );
  }

  const status = CLOUDFLEET_STATUS_META[novedad.cloudfleet_status];
  const resolution = externalResolution(novedad);
  // El título lleva UN estado (el ciclo de vida); el desglose técnico
  // envío/resolución queda en la sección Cloudfleet.
  const state = lifecycle(novedad);

  return (
    <section className="space-y-6">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-start gap-3">
          <Button variant="ghost" size="icon" className="h-11 w-11 shrink-0" asChild>
            <Link href="/novedades" aria-label="Volver a novedades">
              <ArrowLeft className="h-4 w-4" />
            </Link>
          </Button>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-heading text-2xl font-extrabold tracking-tight">
                Novedad {novedad.vehicle_code}
              </h1>
              <Badge variant={state.variant}>
                {state.code === 'resolved' && <CheckCircle2 className="mr-1 h-3 w-3" aria-hidden />}
                {state.label}
              </Badge>
            </div>
            <p className="text-muted-foreground text-sm">
              {formatNovedadDate(novedad.reported_at, 'full')}
              {state.detail && <> · {state.detail}</>}
            </p>
          </div>
        </div>

        <div className="flex flex-col gap-2 sm:flex-row">
          <Can permission="novedades.edit">
            {novedad.cloudfleet_status !== 'sent' && (
              <Button
                className="h-11 w-full sm:w-auto"
                onClick={onRetry}
                disabled={retry.isPending}
              >
                {retry.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="h-4 w-4" />
                )}
                Reintentar Cloudfleet
              </Button>
            )}
          </Can>
          {isAdmin && (
            <Button
              type="button"
              variant="outline"
              className="text-destructive hover:text-destructive h-11 w-full sm:w-auto"
              onClick={() => setConfirmDelete(true)}
              disabled={remove.isPending}
            >
              <Trash2 className="h-4 w-4" />
              Eliminar
            </Button>
          )}
        </div>
      </header>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
        {/* En el teléfono las evidencias van antes que el bloque técnico de
            CloudFleet: es lo que el conductor quiere ver. En `xl` vuelven a la
            columna derecha. */}
        <aside className="rounded-md border bg-white p-5 xl:order-last">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="font-heading text-lg font-bold">Evidencias</h2>
              <p className="text-muted-foreground text-xs">
                {novedad.attachments.length} adjunto(s)
              </p>
            </div>
            <ImageIcon className="text-muted-foreground h-5 w-5" />
          </div>

          {novedad.attachments.length > 0 ? (
            <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-2">
              {novedad.attachments.map((attachment) => (
                <button
                  key={attachment.id}
                  type="button"
                  onClick={() => {
                    setViewer({
                      src: attachment.download_url,
                      filename: attachment.filename,
                      isVideo: attachment.content_type.startsWith('video/'),
                    });
                    setViewerOpen(true);
                  }}
                  className="hover:border-accent-blue focus-visible:ring-ring group w-full overflow-hidden rounded-md border text-left transition-colors focus-visible:outline-none focus-visible:ring-2"
                >
                  {attachment.content_type.startsWith('video/') ? (
                    <div className="bg-muted flex aspect-square w-full items-center justify-center">
                      <FileVideo className="text-muted-foreground h-10 w-10" />
                    </div>
                  ) : (
                    <Image
                      src={attachment.download_url}
                      alt={attachment.filename}
                      width={400}
                      height={400}
                      unoptimized
                      className="aspect-square w-full object-cover"
                    />
                  )}
                  <div className="px-2 py-1">
                    <p className="truncate text-xs font-medium">{attachment.filename}</p>
                    <p className="text-muted-foreground text-[11px]">
                      {(attachment.size_bytes / 1024 / 1024).toFixed(2)} MB
                    </p>
                  </div>
                </button>
              ))}
            </div>
          ) : (
            <div className="text-muted-foreground mt-4 rounded-md border px-3 py-8 text-center text-sm">
              Sin evidencias adjuntas
            </div>
          )}
        </aside>

        <div className="space-y-5">
          <section className="rounded-md border bg-white p-5">
            <h2 className="font-heading text-lg font-bold">Datos de la novedad</h2>
            <div className="mt-4 grid grid-cols-2 gap-4 md:grid-cols-3">
              <Field
                label="Placa"
                value={<span className="font-mono">{novedad.vehicle_code}</span>}
              />
              <Field label="Prioridad" value={PRIORITY_LABEL[novedad.priority]} />
              <Field label="Odómetro" value={novedad.odometer ?? null} />
              <Field label="Creada por" value={novedad.created_by} />
              <Field label="Creación local" value={formatNovedadDate(novedad.created_at, 'full')} />
            </div>
            <Separator className="my-5" />
            <div>
              <p className="text-muted-foreground text-xs font-medium uppercase">Comentario</p>
              <p className="mt-2 whitespace-pre-wrap text-sm">
                {novedad.comment || 'Sin comentario'}
              </p>
            </div>
          </section>

          <section className="rounded-md border bg-white p-5">
            <h2 className="font-heading text-lg font-bold">Cloudfleet</h2>
            <div className="mt-4 grid grid-cols-2 gap-4 md:grid-cols-3">
              <Field label="Envío" value={<Badge variant={status.variant}>{status.label}</Badge>} />
              <Field label="Número" value={novedad.cloudfleet_issue_number} />
              <Field
                label="Resolución"
                value={
                  resolution ? (
                    resolution.done ? (
                      <Badge variant="success">Resuelta</Badge>
                    ) : (
                      <span className="text-muted-foreground">Abierta</span>
                    )
                  ) : (
                    <span className="text-muted-foreground">Sin verificar</span>
                  )
                }
              />
              {resolution?.done && (
                <>
                  <Field
                    label="Orden de trabajo"
                    value={resolution.workOrder != null ? `OT ${resolution.workOrder}` : null}
                  />
                  <Field
                    label="Resuelta el"
                    value={resolution.doneAt ? formatNovedadDate(resolution.doneAt, 'full') : null}
                  />
                </>
              )}
              {novedad.external_synced_at && (
                <Field
                  label="Última verificación"
                  value={formatNovedadDate(novedad.external_synced_at, 'medium')}
                />
              )}
            </div>
            {novedad.cloudfleet_error && (
              <div className="bg-destructive/10 text-destructive mt-4 rounded-md px-3 py-2 text-sm">
                {novedad.cloudfleet_error}
              </div>
            )}
            {novedad.cloudfleet_response && (
              <details className="mt-4">
                <summary className="flex min-h-11 cursor-pointer items-center text-sm font-medium">
                  Respuesta técnica de Cloudfleet
                </summary>
                <pre className="bg-muted mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-md p-3 text-xs">
                  {JSON.stringify(novedad.cloudfleet_response, null, 2)}
                </pre>
              </details>
            )}
          </section>
        </div>
      </div>

      {viewer && (
        <ImageViewer
          open={viewerOpen}
          onOpenChange={setViewerOpen}
          src={viewer.src}
          filename={viewer.filename}
          isVideo={viewer.isVideo}
        />
      )}

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Eliminar esta novedad del portal</DialogTitle>
            <DialogDescription className="space-y-2 pt-2">
              <span className="block">
                Se borran la novedad y sus evidencias en el portal. Esta acción no se puede
                deshacer.
              </span>
              {novedad.cloudfleet_issue_number != null && (
                <span className="block">
                  La issue <strong>#{novedad.cloudfleet_issue_number}</strong> sigue existiendo en
                  Cloudfleet: márcala como hecha desde allí.
                </span>
              )}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              className="h-11"
              onClick={() => setConfirmDelete(false)}
              disabled={remove.isPending}
            >
              Cancelar
            </Button>
            <Button
              type="button"
              variant="destructive"
              className="h-11"
              onClick={onDelete}
              disabled={remove.isPending}
            >
              {remove.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Trash2 className="h-4 w-4" />
              )}
              Eliminar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
