'use client';

import * as React from 'react';
import { FileText } from 'lucide-react';

import { MotorCurveViewer } from '@/components/vehicles/motor-curve-viewer';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Separator } from '@/components/ui/separator';
import { DATASET_LABELS, useVehicleExtractionState } from '@/lib/extraction';
import type { MotorCurve, MotorCurveMatch, Vehicle, VehicleExtractionState } from '@/lib/types';
import { useMotorCurves } from '@/lib/vehicles';

// Catálogo compartido con la vista de gestión de flotas.
export const GEOTAB_STATUS_META: Record<
  string,
  { label: string; variant: 'success' | 'destructive' | 'outline' | 'info' }
> = {
  found: { label: 'Vinculado', variant: 'success' },
  not_found: { label: 'No encontrado', variant: 'destructive' },
  unknown: { label: 'Sin verificar', variant: 'outline' },
  not_applicable: { label: 'No aplica', variant: 'info' },
};

const EXTRACTION_STATUS_META: Record<
  string,
  { label: string; variant: 'success' | 'destructive' | 'outline' }
> = {
  ok: { label: 'OK', variant: 'success' },
  error: { label: 'Error', variant: 'destructive' },
  pending: { label: 'Pendiente', variant: 'outline' },
};

const INACTIVE_DATASETS = new Set(['altimetria', 'consumo_def', 'ubicaciones']);

function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleDateString('es-CO') : '—';
}

function Field({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-muted-foreground text-[11px] font-semibold uppercase tracking-wider">
        {label}
      </dt>
      <dd className={`truncate text-sm font-medium ${mono ? 'font-mono text-xs' : ''}`}>
        {value ?? <span className="text-brand-clear">—</span>}
      </dd>
    </div>
  );
}

function ExtractionRow({ state }: { state: VehicleExtractionState }) {
  const meta = EXTRACTION_STATUS_META[state.status] ?? {
    label: state.status,
    variant: 'outline' as const,
  };
  return (
    <tr className="border-t">
      <td className="py-1.5 pr-2 font-medium">{DATASET_LABELS[state.dataset] ?? state.dataset}</td>
      <td className="px-2">
        <div className="space-y-0.5">
          <Badge variant={meta.variant}>{meta.label}</Badge>
          {state.last_error && (
            <span className="text-destructive block max-w-44 text-[11px] leading-tight">
              {state.last_error}
            </span>
          )}
        </div>
      </td>
      <td className="text-muted-foreground px-2">{fmtDate(state.watermark)}</td>
      <td className="text-muted-foreground px-2">{fmtDate(state.from_date)}</td>
    </tr>
  );
}

/** Estado de extracción de los módulos activos para el vehículo. */
function ExtractionSection({ fleetId, vehicleId }: { fleetId: string; vehicleId: string }) {
  const { data, isLoading, isError } = useVehicleExtractionState(fleetId, vehicleId);

  return (
    <div className="space-y-3">
      <h3 className="font-heading text-sm font-bold">Extracción de datos</h3>
      {isLoading ? (
        <p className="text-muted-foreground text-sm">Cargando estado…</p>
      ) : isError || !data ? (
        <p className="text-destructive text-sm">No se pudo cargar el estado de extracción.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-muted-foreground text-[11px] font-semibold uppercase tracking-wider">
              <th className="pb-1 pr-2 text-left">Dataset</th>
              <th className="px-2 text-left">Estado</th>
              <th className="px-2 text-left">Último extraído</th>
              <th className="px-2 text-left">Próx. desde</th>
            </tr>
          </thead>
          <tbody>
            {data
              .filter((s) => !INACTIVE_DATASETS.has(s.dataset))
              .map((s) => (
                <ExtractionRow key={s.dataset} state={s} />
              ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

const CURVE_MATCH_LABEL: Record<MotorCurveMatch, string> = {
  cpl: 'coincide el CPL',
  motor: 'único documento del motor',
  ambiguo: 'ningún documento coincide con el CPL',
};

function normalizeCpl(value: string | null | undefined): string {
  return (value ?? '').trim().toLowerCase();
}

/**
 * Curvas que aplican a ESTE vehículo.
 *
 * El emparejamiento NO se recalcula acá: se lee de `coverage`, que el backend
 * ya resolvió por (motor, CPL). Duplicar la regla en el cliente es exactamente
 * cómo las dos versiones se separan sin que nada lo detecte.
 *
 * Vive en su propio componente para que el hook no dependa del early return del
 * diálogo.
 */
function MotorCurvesForVehicle({ vehicle }: { vehicle: Vehicle }) {
  const { data } = useMotorCurves(Boolean(vehicle.motor_type));
  const [selectedCurve, setSelectedCurve] = React.useState<MotorCurve | null>(null);

  const matches = React.useMemo(() => {
    if (!vehicle.motor_type) return [];
    return (data?.curvas ?? []).flatMap((curve) => {
      if (curve.motor_type !== vehicle.motor_type) return [];
      const covered = curve.coverage.find(
        (group) => normalizeCpl(group.cpl) === normalizeCpl(vehicle.cpl),
      );
      return covered ? [{ curve, match: covered.match }] : [];
    });
  }, [data?.curvas, vehicle.motor_type, vehicle.cpl]);

  if (matches.length === 0) return null;

  return (
    <>
      <Separator className="my-4" />
      <div className="space-y-2">
        <p className="text-muted-foreground text-[11px] font-semibold uppercase tracking-wider">
          Curvas de motor
        </p>
        <ul className="space-y-2">
          {matches.map(({ curve, match }) => (
            <li key={curve.id} className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">
                  {curve.original_filename ?? `Curva ${curve.motor_type}`}
                </p>
                <p className="text-muted-foreground text-xs">
                  {curve.cpl ? `CPL ${curve.cpl} · ` : ''}
                  {CURVE_MATCH_LABEL[match]}
                </p>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setSelectedCurve(curve)}
              >
                <FileText className="mr-2 h-4 w-4" aria-hidden />
                Ver
              </Button>
            </li>
          ))}
        </ul>
      </div>
      <MotorCurveViewer
        curve={selectedCurve}
        onOpenChange={(open) => {
          if (!open) setSelectedCurve(null);
        }}
      />
    </>
  );
}

interface VehicleDetailDialogProps {
  vehicle: Vehicle | null;
  onOpenChange: (open: boolean) => void;
  fleetId?: string;
}

export function VehicleDetailDialog({ vehicle, onOpenChange, fleetId }: VehicleDetailDialogProps) {
  if (!vehicle) return null;

  return (
    <Dialog open={!!vehicle} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[min(85vh,640px)] max-w-lg flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="border-border shrink-0 border-b px-6 py-4">
          <DialogTitle className="flex flex-wrap items-center gap-2">
            <span className="font-mono">{vehicle.plate}</span>
          </DialogTitle>
          <DialogDescription>
            {vehicle.nombre_vehiculo ?? 'Detalle completo del vehículo.'}
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
            <Field label="Marca" value={vehicle.marca} />
            <Field label="Línea" value={vehicle.linea} />
            <Field label="Tipo motor" value={vehicle.motor_type} />
            <Field label="CPL" value={vehicle.cpl} mono />
            <Field
              label="Uso"
              value={
                <Badge variant={vehicle.vocacional ? 'info' : 'outline'}>
                  {vehicle.vocacional ? 'Vocacional' : 'Comercial'}
                </Badge>
              }
            />
            <Field
              label="Categoría"
              value={
                <Badge variant={vehicle.category === 'Ninguna' ? 'outline' : 'info'}>
                  {vehicle.category}
                </Badge>
              }
            />
            <Field label="VIN" value={vehicle.vin} mono />
            <Field
              label="Sincronizado"
              value={vehicle.synced_at ? new Date(vehicle.synced_at).toLocaleString('es-CO') : null}
            />
          </dl>

          <MotorCurvesForVehicle vehicle={vehicle} />

          {fleetId && (
            <>
              <Separator className="my-4" />
              <ExtractionSection fleetId={fleetId} vehicleId={vehicle.id} />
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
