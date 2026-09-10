'use client';

import * as React from 'react';
import { toast } from 'sonner';
import {
  CarFront,
  Check,
  ChevronRight,
  ChevronsUpDown,
  Loader2,
  MapPin,
  Search,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { extractErrorMessage } from '@/lib/api-client';
import {
  UBICACIONES_DEFAULT_SAMPLE,
  UBICACIONES_MAX_DAYS,
  UBICACIONES_SAMPLE_CHOICES,
  countDaysInclusive,
  fetchUbicaciones,
} from '@/lib/reportes';
import type { LocationPoint } from '@/lib/types';
import { cn } from '@/lib/utils';
import { useAccessibleVehicles } from '@/lib/vehicles';
import { buildXlsx, type XlsxCell, type XlsxSheet } from '@/lib/xlsx';

/** Catálogo de informes bajo demanda. Hoy solo Ubicaciones. */
const REPORTES = [
  {
    key: 'ubicaciones' as const,
    label: 'Ubicaciones',
    description: 'Recorrido de una placa con dirección, consultado a Geotab al momento.',
    icon: MapPin,
  },
];

type ReporteKey = (typeof REPORTES)[number]['key'];

const DAY_IN_MS = 24 * 60 * 60 * 1000;

function toDateInput(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function defaultRange(): { from: string; to: string } {
  const to = new Date();
  const from = new Date(to.getTime() - 6 * DAY_IN_MS);
  return { from: toDateInput(from), to: toDateInput(to) };
}

/** `YYYY-MM-DD HH:MM:SS` en la zona horaria del navegador. */
function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  );
}

function buildWorkbook(
  rows: LocationPoint[],
  meta: { placa: string; dateFrom: string; dateTo: string; sampleMinutes: number },
): Blob {
  const detalle: XlsxCell[][] = [
    [
      { value: 'Fecha y hora', style: 2 },
      { value: 'Placa', style: 2 },
      { value: 'Dirección', style: 2 },
      { value: 'Latitud', style: 2 },
      { value: 'Longitud', style: 2 },
      { value: 'Velocidad (km/h)', style: 2 },
    ],
    ...rows.map((row) => [
      { value: formatTimestamp(row.fecha_y_hora) },
      { value: row.placa ?? meta.placa },
      { value: row.direccion ?? '' },
      { value: row.latitude },
      { value: row.longitude },
      { value: row.speed, style: 4 },
    ]),
  ];
  const resumen: XlsxCell[][] = [
    [{ value: 'Informe de Ubicaciones', style: 1 }],
    [{ value: 'Filtros aplicados', style: 2 }],
    [{ value: 'Placa' }, { value: meta.placa }],
    [{ value: 'Desde' }, { value: meta.dateFrom }],
    [{ value: 'Hasta' }, { value: meta.dateTo }],
    [{ value: 'Intervalo' }, { value: `${meta.sampleMinutes} min` }],
    [{ value: 'Puntos exportados' }, { value: rows.length, style: 6 }],
    [
      { value: 'Direcciones resueltas' },
      { value: rows.filter((row) => row.direccion).length, style: 6 },
    ],
  ];
  const sheets: XlsxSheet[] = [
    { name: 'Resumen', rows: resumen },
    { name: 'Ubicaciones', rows: detalle },
  ];
  return buildXlsx(sheets);
}

export function InformePersonalizadoDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [reporte, setReporte] = React.useState<ReporteKey | null>(null);
  const [vehicleId, setVehicleId] = React.useState<string | null>(null);
  const [vehicleOpen, setVehicleOpen] = React.useState(false);
  const [vehicleSearch, setVehicleSearch] = React.useState('');
  const [range, setRange] = React.useState(defaultRange);
  const [sampleMinutes, setSampleMinutes] = React.useState<number>(UBICACIONES_DEFAULT_SAMPLE);
  const [isGenerating, setIsGenerating] = React.useState(false);
  const [progress, setProgress] = React.useState<{ day: string; loaded: number } | null>(null);
  const abortRef = React.useRef<AbortController | null>(null);

  // Placas del maestro (vehicles), no del modelo semántico: el informe no
  // depende de que el ETL haya corrido.
  // Este catálogo solo es necesario al abrir el diálogo. Evita una consulta
  // completa de vehículos cada vez que se monta la página de reportes.
  const { data: vehiculos } = useAccessibleVehicles(false, open);

  // Cerrar cancela la consulta en curso y devuelve el diálogo a la lista.
  React.useEffect(() => {
    if (open) return;
    abortRef.current?.abort();
    abortRef.current = null;
    setReporte(null);
    setVehicleSearch('');
    setIsGenerating(false);
    setProgress(null);
  }, [open]);

  const normalizedSearch = vehicleSearch.trim().toLowerCase();
  const filteredVehiculos = React.useMemo(() => {
    const list = (vehiculos ?? []).filter((v) => v.geotab_device_id);
    if (!normalizedSearch) return list;
    return list.filter((v) => v.plate.toLowerCase().includes(normalizedSearch));
  }, [vehiculos, normalizedSearch]);

  const selectedVehicle = vehiculos?.find((v) => v.id === vehicleId);
  const selectedLabel = selectedVehicle?.plate ?? 'Selecciona una placa';

  const days = countDaysInclusive(range.from, range.to);
  const rangeError =
    days === null
      ? 'Rango de fechas inválido.'
      : days <= 0
        ? 'La fecha inicial no puede ser mayor que la final.'
        : days > UBICACIONES_MAX_DAYS
          ? `El rango no puede superar ${UBICACIONES_MAX_DAYS} días.`
          : null;
  const canGenerate = Boolean(vehicleId) && !rangeError && !isGenerating;

  async function generate() {
    if (!vehicleId || !canGenerate) return;
    const controller = new AbortController();
    abortRef.current = controller;
    setIsGenerating(true);
    setProgress(null);
    try {
      const rows = await fetchUbicaciones(
        {
          vehicle_id: vehicleId,
          date_from: range.from,
          date_to: range.to,
          sample_minutes: sampleMinutes,
        },
        {
          signal: controller.signal,
          onDay: (day, _rows, loaded) => setProgress({ day, loaded }),
        },
      );
      if (rows.length === 0) {
        toast.info('Geotab no reportó posiciones para esa placa en el rango seleccionado.');
        return;
      }
      const placa = selectedVehicle?.plate ?? rows[0]?.placa ?? '';
      const workbook = buildWorkbook(rows, {
        placa,
        dateFrom: range.from,
        dateTo: range.to,
        sampleMinutes,
      });
      const url = URL.createObjectURL(workbook);
      const link = document.createElement('a');
      link.href = url;
      link.download = `ubicaciones_${placa}_${range.from}_${range.to}.xlsx`;
      link.click();
      URL.revokeObjectURL(url);
      toast.success(`${rows.length} ubicaciones exportadas.`);
      onOpenChange(false);
    } catch (err) {
      if (controller.signal.aborted) return;
      toast.error(extractErrorMessage(err, 'No se pudo generar el informe'));
    } finally {
      abortRef.current = null;
      setIsGenerating(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Informe personalizado</DialogTitle>
          <DialogDescription>
            {reporte === null
              ? 'Elige el informe que quieres generar.'
              : 'Configura los filtros y descarga el archivo.'}
          </DialogDescription>
        </DialogHeader>

        {reporte === null ? (
          <ul className="flex flex-col gap-2">
            {REPORTES.map((item) => (
              <li key={item.key}>
                <button
                  type="button"
                  onClick={() => setReporte(item.key)}
                  className="border-border hover:bg-muted focus-visible:ring-ring flex w-full items-center gap-3 rounded-md border px-3 py-3 text-left transition focus-visible:outline-none focus-visible:ring-2"
                >
                  <item.icon className="text-accent-blue h-5 w-5 shrink-0" aria-hidden />
                  <span className="flex-1">
                    <span className="text-foreground block text-sm font-semibold">
                      {item.label}
                    </span>
                    <span className="text-muted-foreground block text-xs">{item.description}</span>
                  </span>
                  <ChevronRight className="text-muted-foreground h-4 w-4" aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label>Placa</Label>
              <DropdownMenu
                open={vehicleOpen}
                onOpenChange={(next) => {
                  setVehicleOpen(next);
                  if (!next) setVehicleSearch('');
                }}
              >
                <DropdownMenuTrigger asChild>
                  <button
                    type="button"
                    disabled={isGenerating}
                    className="border-border hover:bg-muted focus-visible:ring-ring flex w-full items-center gap-2 rounded-md border bg-white px-3 py-2 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 disabled:opacity-60"
                  >
                    <CarFront className="text-muted-foreground h-4 w-4 shrink-0" aria-hidden />
                    <span className="flex-1 truncate text-left">{selectedLabel}</span>
                    <ChevronsUpDown
                      className="text-muted-foreground h-4 w-4 shrink-0"
                      aria-hidden
                    />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent
                  align="start"
                  className="w-[--radix-dropdown-menu-trigger-width] overflow-hidden"
                >
                  <DropdownMenuLabel>Selecciona una sola placa</DropdownMenuLabel>
                  <div className="relative px-1 pb-1">
                    <Search
                      className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
                      aria-hidden
                    />
                    <Input
                      value={vehicleSearch}
                      onChange={(event) => setVehicleSearch(event.target.value)}
                      onKeyDown={(event) => event.stopPropagation()}
                      placeholder="Buscar placa"
                      className="h-9 pl-8"
                      aria-label="Buscar placa"
                    />
                  </div>
                  <DropdownMenuSeparator />
                  <div className="max-h-[min(260px,var(--radix-dropdown-menu-content-available-height))] overflow-y-auto">
                    {filteredVehiculos.length > 0 ? (
                      filteredVehiculos.map((v) => (
                        <DropdownMenuItem key={v.id} onSelect={() => setVehicleId(v.id)}>
                          <Check
                            className={cn(
                              'h-4 w-4',
                              vehicleId === v.id ? 'opacity-100' : 'opacity-0',
                            )}
                            aria-hidden
                          />
                          <span className="truncate">{v.plate}</span>
                        </DropdownMenuItem>
                      ))
                    ) : (
                      <p className="text-muted-foreground px-2 py-3 text-sm">
                        No hay placas con dispositivo Geotab que coincidan.
                      </p>
                    )}
                  </div>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="ubicaciones-desde">Desde</Label>
                <Input
                  id="ubicaciones-desde"
                  type="date"
                  value={range.from}
                  max={range.to}
                  disabled={isGenerating}
                  onChange={(event) => setRange((prev) => ({ ...prev, from: event.target.value }))}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="ubicaciones-hasta">Hasta</Label>
                <Input
                  id="ubicaciones-hasta"
                  type="date"
                  value={range.to}
                  min={range.from}
                  disabled={isGenerating}
                  onChange={(event) => setRange((prev) => ({ ...prev, to: event.target.value }))}
                />
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label>Un punto cada</Label>
              <div className="bg-muted inline-flex w-fit rounded-md p-0.5">
                {UBICACIONES_SAMPLE_CHOICES.map((minutes) => (
                  <button
                    key={minutes}
                    type="button"
                    disabled={isGenerating}
                    onClick={() => setSampleMinutes(minutes)}
                    className={cn(
                      'rounded px-3 py-1 text-xs font-medium transition disabled:opacity-60',
                      sampleMinutes === minutes ? 'bg-card shadow-sm' : 'text-muted-foreground',
                    )}
                  >
                    {minutes} min
                  </button>
                ))}
              </div>
            </div>

            <p className="text-muted-foreground text-xs">
              Las posiciones y direcciones se piden a Geotab en el momento, un día por consulta, y
              no se almacenan. Máximo {UBICACIONES_MAX_DAYS} días por informe.
            </p>
            {rangeError && <p className="text-destructive text-sm">{rangeError}</p>}
            {isGenerating && (
              <p className="text-muted-foreground text-sm" role="status">
                {progress
                  ? `Consultando ${progress.day}… ${progress.loaded} puntos`
                  : 'Consultando Geotab…'}
              </p>
            )}

            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => (isGenerating ? onOpenChange(false) : setReporte(null))}
              >
                {isGenerating ? 'Cancelar' : 'Volver'}
              </Button>
              <Button type="button" onClick={generate} disabled={!canGenerate}>
                {isGenerating && <Loader2 className="animate-spin" aria-hidden />}
                {isGenerating ? 'Generando…' : 'Generar Excel'}
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
