'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  ChevronsUpDown,
  Loader2,
  Plus,
  Search,
  X,
} from 'lucide-react';
import { toast } from 'sonner';

import {
  ALLOWED_TYPES,
  EvidencePicker,
  MAX_ATTACHMENTS,
  MAX_IMAGE_BYTES,
  MAX_VIDEO_BYTES,
} from '@/components/novedades/evidence-picker';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { useCreateNovedad } from '@/lib/novedades';
import type { ApiError, NovedadPriority, Vehicle } from '@/lib/types';
import { useAccessibleVehicles } from '@/lib/vehicles';
import { cn } from '@/lib/utils';

function toDatetimeLocal(date: Date): string {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

interface VehicleComboboxProps {
  vehicles: Vehicle[];
  value: string;
  onChange: (value: string) => void;
  isLoading: boolean;
  isError: boolean;
}

function vehicleLabel(vehicle: Vehicle): string {
  const model =
    vehicle.marketing_model_name || vehicle.service_model_name || vehicle.nombre_vehiculo;
  return `${vehicle.plate}${model ? ` - ${model}` : ''}`;
}

function VehicleCombobox({ vehicles, value, onChange, isLoading, isError }: VehicleComboboxProps) {
  const [open, setOpen] = React.useState(false);
  const [search, setSearch] = React.useState('');
  const inputRef = React.useRef<HTMLInputElement>(null);
  const selected = vehicles.find((vehicle) => vehicle.id === value) ?? null;

  React.useEffect(() => {
    if (!open) return;
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }, [open]);

  const filtered = React.useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return vehicles;
    return vehicles.filter((vehicle) =>
      [
        vehicle.plate,
        vehicle.nombre_vehiculo,
        vehicle.marca,
        vehicle.linea,
        vehicle.marketing_model_name,
        vehicle.service_model_name,
      ]
        .filter(Boolean)
        .some((item) => item!.toLowerCase().includes(q)),
    );
  }, [vehicles, search]);

  return (
    <DropdownMenu
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setSearch('');
      }}
    >
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="outline"
          disabled={isLoading || isError}
          className="h-11 w-full justify-between px-3.5 text-left font-normal"
        >
          <span className={cn('truncate', !selected && 'text-muted-foreground')}>
            {isLoading
              ? 'Cargando vehículos...'
              : isError
                ? 'No se pudieron cargar vehículos'
                : selected
                  ? vehicleLabel(selected)
                  : 'Selecciona un vehículo'}
          </span>
          <ChevronsUpDown className="text-muted-foreground h-4 w-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-[--radix-dropdown-menu-trigger-width] p-0">
        <div className="border-border sticky top-0 border-b bg-white p-2">
          <div className="relative">
            <Search className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2" />
            <Input
              ref={inputRef}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => event.stopPropagation()}
              placeholder="Buscar placa, nombre, marca..."
              inputMode="search"
              autoCapitalize="characters"
              autoCorrect="off"
              className="h-11 pl-9"
            />
          </div>
        </div>
        <div className="max-h-[min(18rem,var(--radix-dropdown-menu-content-available-height))] overflow-y-auto p-1">
          {filtered.length > 0 ? (
            filtered.map((vehicle) => (
              <DropdownMenuItem
                key={vehicle.id}
                onSelect={() => {
                  onChange(vehicle.id);
                  setOpen(false);
                }}
                className="flex min-h-11 items-start gap-2 py-2"
              >
                <Check
                  className={cn(
                    'text-accent-blue mt-0.5 h-4 w-4',
                    vehicle.id === value ? 'opacity-100' : 'opacity-0',
                  )}
                />
                <span className="min-w-0">
                  <span className="block truncate font-mono text-xs font-semibold">
                    {vehicle.plate}
                  </span>
                  <span className="text-muted-foreground block truncate text-xs">
                    {vehicle.marketing_model_name ||
                      vehicle.service_model_name ||
                      vehicle.nombre_vehiculo ||
                      [vehicle.marca, vehicle.linea].filter(Boolean).join(' ') ||
                      'Sin descripción'}
                  </span>
                </span>
              </DropdownMenuItem>
            ))
          ) : (
            <div className="text-muted-foreground px-3 py-6 text-center text-sm">
              No hay vehículos con ese filtro.
            </div>
          )}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

interface NovedadFormProps {
  initialVehicleId?: string;
}

type CreateErrorModal = 'vehicle-not-found' | 'generic' | null;

/** Qué pasó con cada tarjeta durante el envío. Se muestra en su cabecera. */
type DraftStatus = 'idle' | 'uploading' | 'done' | 'error';

interface NovedadDraft {
  id: string;
  priority: NovedadPriority;
  comment: string;
  images: File[];
}

let draftSequence = 0;

function createDraft(id?: string): NovedadDraft {
  draftSequence += 1;
  return {
    id: id ?? `novedad-draft-${draftSequence}`,
    priority: 'medium',
    comment: '',
    images: [],
  };
}

function createErrorModalFor(err: unknown): CreateErrorModal {
  const detail = (err as ApiError | null)?.detail;
  if (
    detail &&
    typeof detail === 'object' &&
    'code' in detail &&
    (detail as { code?: unknown }).code === 'cloudfleet_vehicle_not_found'
  ) {
    return 'vehicle-not-found';
  }
  return 'generic';
}

function DraftStatusLabel({ status }: { status: DraftStatus }) {
  if (status === 'uploading') {
    return (
      <span className="text-muted-foreground inline-flex items-center gap-1 text-xs">
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        Enviando…
      </span>
    );
  }
  if (status === 'done') {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-[#3F5E00]">
        <Check className="h-3.5 w-3.5" aria-hidden />
        Enviada
      </span>
    );
  }
  if (status === 'error') {
    return (
      <span className="text-destructive inline-flex items-center gap-1 text-xs font-medium">
        <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
        Falló, revisa e intenta de nuevo
      </span>
    );
  }
  return null;
}

export function NovedadForm({ initialVehicleId = '' }: NovedadFormProps) {
  const router = useRouter();
  const {
    data: vehicles,
    isLoading: vehiclesLoading,
    isError: vehiclesError,
  } = useAccessibleVehicles();
  const create = useCreateNovedad();

  const [vehicleId, setVehicleId] = React.useState('');
  const [reportedAt, setReportedAt] = React.useState(() => toDatetimeLocal(new Date()));
  const [odometer, setOdometer] = React.useState('');
  const [drafts, setDrafts] = React.useState<NovedadDraft[]>(() => [
    createDraft('novedad-draft-inicial'),
  ]);
  const [draftStatus, setDraftStatus] = React.useState<Record<string, DraftStatus>>({});
  const [guidanceOpen, setGuidanceOpen] = React.useState(true);
  const [createErrorModal, setCreateErrorModal] = React.useState<CreateErrorModal>(null);
  const [createdCount, setCreatedCount] = React.useState(0);
  const [progress, setProgress] = React.useState<{ current: number; total: number } | null>(null);

  React.useEffect(() => {
    if (!initialVehicleId || vehicleId || !vehicles?.length) return;
    if (vehicles.some((vehicle) => vehicle.id === initialVehicleId)) {
      setVehicleId(initialVehicleId);
    }
  }, [initialVehicleId, vehicleId, vehicles]);

  function onFilesSelected(draftId: string, files: FileList | null) {
    if (!files) return;
    const draft = drafts.find((item) => item.id === draftId);
    if (!draft) return;
    const next = [...draft.images];
    for (const file of Array.from(files)) {
      if (next.length >= MAX_ATTACHMENTS) {
        toast.error(`Máximo ${MAX_ATTACHMENTS} archivos por novedad`);
        break;
      }
      if (!ALLOWED_TYPES.has(file.type)) {
        toast.error(`${file.name} no tiene un formato permitido`);
        continue;
      }
      const maxBytes = file.type.startsWith('video/') ? MAX_VIDEO_BYTES : MAX_IMAGE_BYTES;
      const limitMb = maxBytes / (1024 * 1024);
      if (file.size > maxBytes) {
        toast.error(`${file.name} supera el límite de ${limitMb} MB`);
        continue;
      }
      next.push(file);
    }
    setDrafts((current) =>
      current.map((item) => (item.id === draftId ? { ...item, images: next } : item)),
    );
  }

  function updateDraft(draftId: string, changes: Partial<Omit<NovedadDraft, 'id'>>) {
    setDrafts((current) =>
      current.map((item) => (item.id === draftId ? { ...item, ...changes } : item)),
    );
    setDraftStatus((current) =>
      current[draftId] === 'error' ? { ...current, [draftId]: 'idle' } : current,
    );
  }

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const odometerValue = odometer.trim() ? Number(odometer) : null;

    if (!vehicleId) {
      toast.error('Selecciona un vehículo');
      return;
    }
    if (!reportedAt) {
      toast.error('Indica la fecha de la novedad');
      return;
    }
    if (odometer.trim() && (!Number.isFinite(odometerValue) || odometerValue! < 0)) {
      toast.error('Ingresa un odómetro válido');
      return;
    }

    const emptyDraftIndex = drafts.findIndex((draft) => !draft.comment.trim());
    if (emptyDraftIndex >= 0) {
      toast.error(`Escribe la descripción de la novedad ${emptyDraftIndex + 1}`);
      return;
    }

    const created: string[] = [];
    const doneIds = new Set<string>();
    setProgress({ current: 0, total: drafts.length });
    try {
      for (const [index, draft] of drafts.entries()) {
        setProgress({ current: index + 1, total: drafts.length });
        setDraftStatus((current) => ({ ...current, [draft.id]: 'uploading' }));
        try {
          const result = await create.mutateAsync({
            vehicle_id: vehicleId,
            reported_at: new Date(reportedAt).toISOString(),
            priority: draft.priority,
            odometer: odometerValue,
            comment: draft.comment.trim(),
            images: draft.images,
          });
          created.push(result.id);
          doneIds.add(draft.id);
          setDraftStatus((current) => ({ ...current, [draft.id]: 'done' }));
        } catch (err) {
          setDraftStatus((current) => ({ ...current, [draft.id]: 'error' }));
          throw err;
        }
      }
      toast.success(
        created.length === 1
          ? 'Novedad creada en Cloudfleet'
          : `${created.length} novedades creadas en Cloudfleet`,
      );
      router.push(created.length === 1 ? `/novedades/${created[0]}` : '/novedades');
    } catch (err: unknown) {
      // Fallo parcial: las tarjetas ya enviadas salen del formulario y las
      // demás se quedan para reintentar, en vez de mandar al usuario a la
      // lista con la mitad del trabajo perdido (red móvil intermitente).
      setCreatedCount(created.length);
      if (doneIds.size > 0) {
        setDrafts((current) => current.filter((draft) => !doneIds.has(draft.id)));
      }
      setCreateErrorModal(createErrorModalFor(err));
    } finally {
      setProgress(null);
    }
  }

  function closeCreateErrorModal() {
    setCreateErrorModal(null);
  }

  const submitting = create.isPending || progress !== null;

  return (
    <section className="space-y-6">
      <Dialog open={guidanceOpen} onOpenChange={setGuidanceOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Registra cada novedad por separado</DialogTitle>
            <DialogDescription className="space-y-3 pt-2">
              <span className="block">
                Si un vehículo tiene varios problemas, crea una tarjeta para cada uno. El vehículo,
                la fecha y el odómetro se comparten automáticamente.
              </span>
              <span className="block">
                Escribe una sola situación por tarjeta, elige su prioridad y adjunta únicamente las
                evidencias que correspondan. Así cada novedad puede asignarse y recibir seguimiento
                como un trabajo individual.
              </span>
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" className="h-11" onClick={() => setGuidanceOpen(false)}>
              Entendido
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={createErrorModal !== null}
        onOpenChange={(open) => !open && closeCreateErrorModal()}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {createErrorModal === 'vehicle-not-found'
                ? 'Vehículo no registrado en Cloudfleet'
                : 'No fue posible completar el registro'}
            </DialogTitle>
            <DialogDescription>
              {createErrorModal === 'vehicle-not-found'
                ? 'Vehículo no encontrado. Contacta a tu administrador para solicitar su registro en Cloudfleet.'
                : createdCount > 0
                  ? `Se registraron ${createdCount} novedad(es). Las que fallaron siguen en el formulario para que las revises y vuelvas a enviar.`
                  : 'Ocurrió un problema al registrar la novedad en Cloudfleet. Inténtalo de nuevo más tarde.'}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" className="h-11" onClick={closeCreateErrorModal}>
              Entendido
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <header className="flex items-center gap-3">
        <Button variant="ghost" size="icon" className="h-11 w-11 shrink-0" asChild>
          <Link href="/novedades" aria-label="Volver a novedades">
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <div className="min-w-0">
          <h1 className="font-heading text-2xl font-extrabold tracking-tight">
            Registrar novedades
          </h1>
          <p className="text-muted-foreground text-sm">
            Agrupa varias novedades del mismo vehículo y asigna evidencias a cada una.
          </p>
        </div>
      </header>

      <form onSubmit={onSubmit} className="space-y-5">
        <section className="space-y-4 rounded-md border bg-white p-4 sm:p-5">
          <div>
            <h2 className="font-heading text-base font-bold">Datos compartidos</h2>
            <p className="text-muted-foreground text-xs">
              Estos datos se usarán en todas las novedades de este registro.
            </p>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2 md:col-span-2">
              <Label>Vehículo</Label>
              <VehicleCombobox
                vehicles={vehicles ?? []}
                value={vehicleId}
                onChange={setVehicleId}
                isLoading={vehiclesLoading}
                isError={vehiclesError}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="reported-at">Fecha de novedad</Label>
              <Input
                id="reported-at"
                type="datetime-local"
                value={reportedAt}
                onChange={(event) => setReportedAt(event.target.value)}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="odometer">Odómetro</Label>
              <Input
                id="odometer"
                type="number"
                inputMode="decimal"
                min="0"
                step="0.01"
                value={odometer}
                onChange={(event) => setOdometer(event.target.value)}
                placeholder="Opcional"
              />
            </div>
          </div>
        </section>

        <section className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="font-heading text-base font-bold">Novedades a registrar</h2>
              <p className="text-muted-foreground text-xs">
                Una tarjeta equivale a un trabajo individual.
              </p>
            </div>
            <Button
              type="button"
              variant="outline"
              className="h-11 w-full sm:w-auto"
              onClick={() => setDrafts((current) => [...current, createDraft()])}
              disabled={submitting}
            >
              <Plus className="h-4 w-4" />
              Agregar novedad
            </Button>
          </div>

          {drafts.map((draft, index) => (
            <article key={draft.id} className="space-y-4 rounded-md border bg-white p-4 sm:p-5">
              <div className="flex items-center justify-between gap-3">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <h3 className="font-heading font-bold">Novedad {index + 1}</h3>
                  <DraftStatusLabel status={draftStatus[draft.id] ?? 'idle'} />
                </div>
                {drafts.length > 1 && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-11 sm:h-9"
                    onClick={() =>
                      setDrafts((current) => current.filter((item) => item.id !== draft.id))
                    }
                    disabled={submitting}
                  >
                    <X className="h-4 w-4" />
                    Quitar
                  </Button>
                )}
              </div>

              <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
                <div className="space-y-4">
                  <div className="space-y-2">
                    <Label htmlFor={`priority-${draft.id}`}>Prioridad</Label>
                    <select
                      id={`priority-${draft.id}`}
                      value={draft.priority}
                      onChange={(event) =>
                        updateDraft(draft.id, {
                          priority: event.target.value as NovedadPriority,
                        })
                      }
                      disabled={submitting}
                      // `text-base` en móvil: iOS hace zoom sobre controles con
                      // fuente menor a 16 px al enfocarlos.
                      className="border-input bg-background text-foreground focus-visible:border-accent-blue focus-visible:ring-ring h-11 w-full rounded-md border px-3 text-base shadow-sm focus-visible:outline-none focus-visible:ring-2 md:text-sm"
                    >
                      <option value="low">Baja</option>
                      <option value="medium">Media</option>
                      <option value="high">Alta</option>
                    </select>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor={`comment-${draft.id}`}>Descripción de la novedad</Label>
                    <Textarea
                      id={`comment-${draft.id}`}
                      value={draft.comment}
                      onChange={(event) => updateDraft(draft.id, { comment: event.target.value })}
                      maxLength={1000}
                      disabled={submitting}
                      placeholder="Describe solo una situación o trabajo"
                    />
                    <p className="text-muted-foreground text-xs">
                      {draft.comment.length}/1000 caracteres
                    </p>
                  </div>
                </div>

                <div className="space-y-3">
                  <div>
                    <h4 className="font-semibold">Evidencias de esta novedad</h4>
                    <p className="text-muted-foreground text-xs">
                      JPG, PNG, WebP, MP4 o WebM. Máximo {MAX_ATTACHMENTS} archivos.
                    </p>
                  </div>
                  <EvidencePicker
                    files={draft.images}
                    disabled={submitting}
                    onAdd={(files) => onFilesSelected(draft.id, files)}
                    onRemove={(fileIndex) =>
                      updateDraft(draft.id, {
                        images: draft.images.filter((_, i) => i !== fileIndex),
                      })
                    }
                  />
                </div>
              </div>
            </article>
          ))}
        </section>

        {/* Pie pegado abajo en el teléfono, con margen para la barra del
            sistema; en `md` vuelve a ser un botón normal al final del formulario. */}
        <div className="bg-background/95 sticky bottom-0 z-30 -mx-4 flex flex-col gap-2 border-t px-4 py-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] backdrop-blur md:static md:mx-0 md:flex-row md:items-center md:border-0 md:bg-transparent md:p-0 md:backdrop-blur-0">
          <Button type="submit" disabled={submitting} className="h-11 w-full md:w-auto">
            {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
            {progress
              ? `Enviando novedad ${progress.current} de ${progress.total}…`
              : `Registrar ${drafts.length === 1 ? 'novedad' : `${drafts.length} novedades`}`}
          </Button>
        </div>
      </form>
    </section>
  );
}
