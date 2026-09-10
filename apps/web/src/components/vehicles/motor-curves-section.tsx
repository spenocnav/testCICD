'use client';

import * as React from 'react';
import { ChevronDown, FileText, GaugeCircle } from 'lucide-react';

import { MotorCurveViewer } from '@/components/vehicles/motor-curve-viewer';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useMotorCurves } from '@/lib/vehicles';
import type { MotorCurve, MotorCurveMatch } from '@/lib/types';

const MATCH_LABEL: Record<MotorCurveMatch, string> = {
  cpl: 'Coincide el CPL',
  motor: 'Único del motor',
  ambiguo: 'Sin CPL coincidente',
};

const MATCH_VARIANT: Record<MotorCurveMatch, 'success' | 'info' | 'warning'> = {
  cpl: 'success',
  motor: 'info',
  ambiguo: 'warning',
};

const MATCH_HINT: Record<MotorCurveMatch, string> = {
  cpl: 'El CPL del documento es el mismo del vehículo.',
  motor: 'El motor tiene un solo documento, así que aplica aunque el CPL no coincida.',
  ambiguo:
    'El motor tiene varios documentos y ninguno coincide con el CPL del vehículo. Se muestran todos: elegir uno por parecido mostraría la curva equivocada.',
};

function formatSize(bytes: number | null): string | null {
  if (bytes === null || bytes <= 0) return null;
  const kb = bytes / 1024;
  if (kb < 1024) return `${Math.round(kb)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

function CurveCard({ curve, onOpen }: { curve: MotorCurve; onOpen: () => void }) {
  const size = formatSize(curve.file_size);
  return (
    <li className="flex flex-col gap-3 rounded-md border bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-heading text-base font-bold">{curve.motor_type}</p>
          <p
            className="text-muted-foreground truncate text-xs"
            title={curve.original_filename ?? ''}
          >
            {curve.original_filename ?? 'Documento sin nombre'}
          </p>
        </div>
        <Badge variant={MATCH_VARIANT[curve.match]} title={MATCH_HINT[curve.match]}>
          {MATCH_LABEL[curve.match]}
        </Badge>
      </div>

      <dl className="grid grid-cols-2 gap-2 text-xs">
        <div>
          <dt className="text-muted-foreground">CPL del documento</dt>
          <dd className="font-mono font-semibold">{curve.cpl ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Vehículos que lo usan</dt>
          <dd className="font-semibold">{curve.vehicle_count}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Tamaño</dt>
          <dd className="font-semibold">{size ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Actualizado</dt>
          <dd className="font-semibold">
            {curve.source_updated_at
              ? new Date(curve.source_updated_at).toLocaleDateString('es-CO')
              : '—'}
          </dd>
        </div>
      </dl>

      {curve.coverage.length > 1 && (
        <ul className="text-muted-foreground space-y-1 text-xs">
          {curve.coverage.map((group) => (
            <li
              key={`${curve.id}-${group.cpl ?? 'sin-cpl'}`}
              className="flex justify-between gap-2"
            >
              <span>
                CPL <span className="font-mono">{group.cpl ?? 'sin CPL'}</span>
              </span>
              <span title={MATCH_HINT[group.match]}>
                {group.vehicle_count} · {MATCH_LABEL[group.match].toLowerCase()}
              </span>
            </li>
          ))}
        </ul>
      )}

      <Button type="button" variant="outline" className="mt-auto" onClick={onOpen}>
        <FileText className="mr-2 h-4 w-4" aria-hidden />
        Ver curva
      </Button>
    </li>
  );
}

/**
 * Curvas de par y potencia de los motores presentes en la flota o flotas
 * seleccionadas. El catálogo de documentos es global; acá sólo se listan los
 * motores que el alcance realmente usa.
 */
export function MotorCurvesSection() {
  const { data, isLoading, isError } = useMotorCurves();
  const [selectedCurve, setSelectedCurve] = React.useState<MotorCurve | null>(null);
  const [showMissing, setShowMissing] = React.useState(false);

  const curves = data?.curvas ?? [];
  const missing = data?.sin_curva ?? [];

  return (
    <section className="space-y-3">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex items-center gap-2">
          <GaugeCircle className="text-accent-blue h-5 w-5" aria-hidden />
          <div>
            <h2 className="font-heading text-lg font-extrabold">Curvas de motor</h2>
            <p className="text-muted-foreground text-sm">
              Documentos de par y potencia de los motores de tus flotas seleccionadas.
            </p>
          </div>
        </div>
        {missing.length > 0 && (
          <Button
            type="button"
            variant="ghost"
            className="text-xs"
            onClick={() => setShowMissing((open) => !open)}
            aria-expanded={showMissing}
          >
            {missing.length === 1 ? '1 motor sin curva' : `${missing.length} motores sin curva`}
            <ChevronDown
              className={`ml-1 h-4 w-4 transition-transform ${showMissing ? 'rotate-180' : ''}`}
              aria-hidden
            />
          </Button>
        )}
      </header>

      {showMissing && missing.length > 0 && (
        <ul className="text-muted-foreground grid gap-1 rounded-md border bg-white p-4 text-xs sm:grid-cols-2 lg:grid-cols-3">
          {missing.map((motor) => (
            <li key={`${motor.motor_type}-${motor.cpl ?? 'sin-cpl'}`}>
              <span className="text-foreground font-semibold">{motor.motor_type}</span>
              {motor.cpl ? <span className="font-mono"> · CPL {motor.cpl}</span> : null}
              {' · '}
              {motor.vehicle_count} vehículo{motor.vehicle_count === 1 ? '' : 's'}
            </li>
          ))}
        </ul>
      )}

      {isLoading ? (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, index) => (
            <li key={index} className="rounded-md border bg-white p-4">
              <Skeleton className="h-24 w-full" />
            </li>
          ))}
        </ul>
      ) : isError ? (
        <p className="text-destructive rounded-md border bg-white p-4 text-sm">
          No se pudieron cargar las curvas de motor.
        </p>
      ) : curves.length > 0 ? (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {curves.map((curve) => (
            <CurveCard key={curve.id} curve={curve} onOpen={() => setSelectedCurve(curve)} />
          ))}
        </ul>
      ) : (
        <p className="text-muted-foreground rounded-md border bg-white p-4 text-sm">
          No hay curvas disponibles para los motores de estas flotas.
        </p>
      )}

      <MotorCurveViewer
        curve={selectedCurve}
        onOpenChange={(open) => {
          if (!open) setSelectedCurve(null);
        }}
      />
    </section>
  );
}
