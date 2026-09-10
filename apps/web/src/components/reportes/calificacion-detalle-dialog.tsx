'use client';

import * as React from 'react';
import { Clock, Gauge, ListChecks, Route, ShieldAlert, TriangleAlert } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { CalificacionVehiculo } from '@/lib/types';

// Formateadores locales, con la misma semántica que los de la pantalla de
// reportes: es-CO, decimales fijos y "—" para lo que no existe. Se duplican
// aquí a propósito para no importar desde `app/` hacia `components/`, igual que
// hace `fleets/vehicle-detail-dialog.tsx`.
function num(v: number | null | undefined, decimals = 1): string {
  if (v == null) return '—';
  return v.toLocaleString('es-CO', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function int(v: number | null | undefined): string {
  if (v == null) return '—';
  return Math.round(v).toLocaleString('es-CO');
}

const pesoPct = (peso: number) => `${num(peso * 100, 0)} %`;

const ESTADO_VARIANT: Record<string, 'success' | 'warning' | 'destructive'> = {
  Cumple: 'success',
  'En riesgo': 'warning',
  'No cumple': 'destructive',
};

function Section({
  title,
  icon,
  hint,
  children,
}: {
  title: string;
  icon?: React.ReactNode;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-2">
      <div className="flex items-baseline gap-2">
        {icon}
        <h3 className="font-heading text-sm font-bold tracking-tight">{title}</h3>
      </div>
      {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
      {children}
    </section>
  );
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="border-border bg-muted/20 min-w-0 rounded-lg border p-3">
      <dt className="text-muted-foreground text-[11px] font-semibold uppercase tracking-wider">
        {label}
      </dt>
      <dd className="mt-0.5 text-sm font-semibold">{value}</dd>
    </div>
  );
}

/** Tarjeta de un puntaje del vehículo. El Q General penalizado no se pinta como
 * un dato cualquiera: el 0 es una sanción, no una medición baja. */
function ScoreTile({
  label,
  value,
  penalized,
  sinExposicion,
  base,
}: {
  label: string;
  value: number | null;
  penalized?: boolean;
  /** Operó por debajo del mínimo: no hay nota, y no es lo mismo que un 0. */
  sinExposicion?: boolean;
  base?: number | null;
}) {
  return (
    <div
      className={
        penalized
          ? 'border-destructive/40 bg-destructive/5 rounded-lg border p-3'
          : 'border-border bg-muted/20 rounded-lg border p-3'
      }
    >
      <p className="text-muted-foreground text-[11px] font-semibold uppercase tracking-wider">
        {label}
      </p>
      <p
        className={
          penalized
            ? 'text-destructive font-heading text-2xl font-bold'
            : 'font-heading text-2xl font-bold'
        }
      >
        {num(value, 1)}
      </p>
      {penalized && (
        <p className="text-destructive text-[11px] font-semibold">
          Penalizado{base != null ? ` · habría sido ${num(base, 1)}` : ''}
        </p>
      )}
      {!penalized && sinExposicion && (
        <p className="text-muted-foreground text-[11px] font-semibold">
          Sin exposición{base != null ? ` · habría sido ${num(base, 1)}` : ''}
        </p>
      )}
    </div>
  );
}

interface Props {
  vehiculo: CalificacionVehiculo | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Periodo que está viendo la tabla, para que el desglose no quede sin contexto. */
  periodoLabel?: string | null;
}

/**
 * Desglose de la calificación de UN vehículo: por qué falla, cómo se armaron
 * Q.H. Operación y Q.H. Seguros, sobre qué exposición se midió y, si aplica, la
 * penalización por sobrevelocidad.
 *
 * Todo el contenido numérico y textual viene del API. `motivos` llega ordenado
 * por puntos perdidos y ya formateado: se pinta tal cual, sin reordenar.
 */
export function CalificacionDetalleDialog({ vehiculo, open, onOpenChange, periodoLabel }: Props) {
  const detalle = vehiculo?.detalle ?? null;
  const penalizado = vehiculo?.penalizado_por_sobrevelocidad ?? false;
  // Ausente = suficiente: el tipo es manual y el API que responde puede ser
  // anterior a la exposición mínima. La duda se resuelve a favor de calificar.
  const sinExposicion = !penalizado && vehiculo?.exposicion_suficiente === false;

  // Suma de los aportes ponderados listados: es literalmente el total de la
  // tabla de eventos, no una reconstrucción del QHS.
  const totalPonderado = detalle
    ? detalle.eventos_qhs.reduce((sum, evento) => sum + evento.aporte_ponderado, 0)
    : 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
        {!vehiculo ? null : (
          <>
            <DialogHeader>
              <DialogTitle className="flex flex-wrap items-center gap-2">
                {vehiculo.placa ?? vehiculo.vehicle_id}
                <Badge variant={vehiculo.vocacional ? 'info' : 'outline'}>
                  {vehiculo.vocacional ? 'Vocacional · por horas' : 'Comercial · por km'}
                </Badge>
                {vehiculo.estado && (
                  <Badge variant={ESTADO_VARIANT[vehiculo.estado] ?? 'outline'}>
                    {vehiculo.estado}
                  </Badge>
                )}
              </DialogTitle>
              <DialogDescription>
                Desglose de la calificación
                {periodoLabel ? ` de ${periodoLabel}` : ' del periodo seleccionado'}.
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-6 text-sm">
              {/* Los tres puntajes, con la penalización a la vista desde arriba. */}
              <div className="grid grid-cols-3 gap-3">
                <ScoreTile label="Q.H. Seguros" value={vehiculo.qhs} />
                <ScoreTile label="Q.H. Operación" value={vehiculo.qho} />
                <ScoreTile
                  label="Q General"
                  value={vehiculo.qgen}
                  penalized={penalizado}
                  sinExposicion={sinExposicion}
                  base={vehiculo.qgen_base}
                />
              </div>

              {/* 1. Por qué falla: lo primero que se quiere leer. */}
              <Section
                title="Por qué obtiene este puntaje"
                icon={<TriangleAlert className="text-accent-yellow h-4 w-4" aria-hidden />}
                hint={
                  detalle && detalle.motivos.length > 0
                    ? 'Ordenado por puntos perdidos: primero lo que más pesa.'
                    : undefined
                }
              >
                {!detalle ? (
                  <p className="border-border bg-muted/20 text-muted-foreground rounded-lg border p-3 text-xs">
                    El API no entregó el desglose de este vehículo para este periodo.
                  </p>
                ) : detalle.motivos.length === 0 ? (
                  <p className="border-border bg-muted/20 text-muted-foreground rounded-lg border p-3 text-xs">
                    Sin motivos reportados: el vehículo no perdió puntos atribuibles.
                  </p>
                ) : (
                  <ol className="space-y-2">
                    {detalle.motivos.map((motivo, index) => (
                      <li
                        key={motivo}
                        className="border-border bg-background flex items-start gap-3 rounded-lg border p-3"
                      >
                        <span className="bg-muted text-muted-foreground mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold">
                          {index + 1}
                        </span>
                        <span className="leading-snug">{motivo}</span>
                      </li>
                    ))}
                  </ol>
                )}
              </Section>

              {/* 2. QHO y sus componentes. */}
              {detalle && (
                <Section
                  title="Hábitos de operación (Q.H. Operación)"
                  icon={<Gauge className="text-accent-blue h-4 w-4" aria-hidden />}
                  hint="Cada componente se lleva a 0–100 y se pondera; los aportes forman el Q.H. Operación."
                >
                  {detalle.componentes_qho.length === 0 ? (
                    <p className="text-muted-foreground text-xs">Sin componentes evaluables.</p>
                  ) : (
                    <>
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>Componente</TableHead>
                            <TableHead>Medido</TableHead>
                            <TableHead>Objetivo</TableHead>
                            <TableHead className="text-right">Puntos</TableHead>
                            <TableHead className="text-right">Peso</TableHead>
                            <TableHead className="text-right">Aporte</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {detalle.componentes_qho.map((componente) => (
                            <TableRow key={componente.nombre}>
                              <TableCell className="font-medium">{componente.nombre}</TableCell>
                              <TableCell>{componente.valor_texto}</TableCell>
                              <TableCell className="text-muted-foreground text-xs">
                                {componente.objetivo_texto}
                              </TableCell>
                              <TableCell className="text-right">
                                {num(componente.puntos, 1)}
                              </TableCell>
                              <TableCell className="text-muted-foreground text-right">
                                {pesoPct(componente.peso)}
                              </TableCell>
                              <TableCell className="text-right font-semibold">
                                {num(componente.aporte, 1)}
                              </TableCell>
                            </TableRow>
                          ))}
                          <TableRow className="bg-muted/30 hover:bg-muted/30">
                            <TableCell colSpan={5} className="text-right font-semibold">
                              Q.H. Operación
                            </TableCell>
                            <TableCell className="text-right font-bold">
                              {num(vehiculo.qho, 1)}
                            </TableCell>
                          </TableRow>
                        </TableBody>
                      </Table>
                      {detalle.componentes_qho.some((c) => c.puntos == null) && (
                        <p className="text-muted-foreground text-xs">
                          Un componente con «—» no es evaluable en este periodo: no cuenta como 0.
                        </p>
                      )}
                    </>
                  )}
                </Section>
              )}

              {/* 3. QHS: eventos por tipo, su peso y el aporte ponderado. */}
              {detalle && (
                <Section
                  title="Hábitos seguros (Q.H. Seguros)"
                  icon={<ListChecks className="text-accent-blue h-4 w-4" aria-hidden />}
                  hint={`Eventos de conducción ponderados sobre la exposición del vehículo (${detalle.exposicion_base}).`}
                >
                  {detalle.eventos_qhs.length === 0 ? (
                    <p className="text-muted-foreground text-xs">
                      Sin eventos de conducción registrados en el periodo.
                    </p>
                  ) : (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Tipo de evento</TableHead>
                          <TableHead className="text-right">Eventos</TableHead>
                          <TableHead className="text-right">Peso</TableHead>
                          <TableHead className="text-right">Aporte ponderado</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {detalle.eventos_qhs.map((evento) => (
                          <TableRow key={evento.event_type}>
                            <TableCell className="font-medium">{evento.event_type}</TableCell>
                            <TableCell className="text-right">{int(evento.n_eventos)}</TableCell>
                            <TableCell className="text-muted-foreground text-right">
                              × {num(evento.peso, 2)}
                            </TableCell>
                            <TableCell className="text-right font-semibold">
                              {num(evento.aporte_ponderado, 2)}
                            </TableCell>
                          </TableRow>
                        ))}
                        <TableRow className="bg-muted/30 hover:bg-muted/30">
                          <TableCell colSpan={3} className="text-right font-semibold">
                            Total ponderado
                          </TableCell>
                          <TableCell className="text-right font-bold">
                            {num(totalPonderado, 2)}
                          </TableCell>
                        </TableRow>
                      </TableBody>
                    </Table>
                  )}
                  <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
                    <Field label="Kilómetros" value={int(detalle.km)} />
                    <Field
                      label={`Eventos por ${detalle.exposicion_base}`}
                      value={num(detalle.eventos_qhs_por_1000km, 2)}
                    />
                    <Field
                      label="Tope (nota 0)"
                      value={`${num(detalle.eventos_qhs_tope, 0)} eventos`}
                    />
                    <Field label="Q.H. Seguros" value={num(vehiculo.qhs, 1)} />
                  </dl>
                </Section>
              )}

              {/* 4. Exposición: cuánto operó y contra qué base se midió. */}
              {detalle && (
                <Section
                  title="Exposición del periodo"
                  icon={<Route className="text-accent-blue h-4 w-4" aria-hidden />}
                  hint={`Este vehículo se mide por ${detalle.exposicion_base}.`}
                >
                  <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
                    <Field label="Kilómetros" value={int(detalle.km)} />
                    <Field
                      label="Horas ECM"
                      value={
                        <span className="inline-flex items-center gap-1.5">
                          <Clock className="text-muted-foreground h-3.5 w-3.5" aria-hidden />
                          {num(detalle.horas_ecm, 1)}
                        </span>
                      }
                    />
                    <Field
                      label={`Exposición (${detalle.exposicion_base})`}
                      value={num(detalle.exposicion_unidades, 2)}
                    />
                    {/* El peso reconcilia el puntaje de la fila con el gauge:
                        un vehículo puede tener 93,5 y pesar el 0,003 % de la
                        flota, y sin este número esa cifra parece un error. */}
                    <Field
                      label="Peso en el promedio"
                      value={
                        vehiculo.peso_en_promedio == null
                          ? '—'
                          : `${num(vehiculo.peso_en_promedio * 100, 2)} %`
                      }
                    />
                  </dl>
                  {detalle.exposicion_minima != null && (
                    <p
                      className={
                        sinExposicion
                          ? 'border-border bg-muted/40 text-foreground rounded-lg border p-3 text-xs leading-snug'
                          : 'text-muted-foreground text-xs leading-snug'
                      }
                    >
                      {sinExposicion ? (
                        <>
                          <span className="font-semibold">No se califica.</span> Este vehículo
                          necesitaba al menos{' '}
                          <span className="font-semibold">
                            {num(detalle.exposicion_minima, 0)}{' '}
                            {vehiculo.vocacional ? 'horas ECM' : 'km'}
                          </span>{' '}
                          en el periodo
                          {detalle.dias_periodo ? ` (${int(detalle.dias_periodo)} días)` : ''} para
                          recibir puntaje. Con menos operación la nota mediría la falta de uso y no
                          la conducción, así que queda fuera del promedio y del estado de la flota.
                          Su Q General de referencia sigue arriba.
                        </>
                      ) : (
                        <>
                          Mínimo para calificar en este periodo:{' '}
                          {num(detalle.exposicion_minima, 0)}{' '}
                          {vehiculo.vocacional ? 'horas ECM' : 'km'}
                          {detalle.dias_periodo ? ` (${int(detalle.dias_periodo)} días)` : ''}.
                        </>
                      )}
                    </p>
                  )}
                </Section>
              )}

              {/* 5. La penalización, si aplica. */}
              {penalizado && (
                <Section
                  title="Penalización por sobrevelocidad"
                  icon={<ShieldAlert className="text-destructive h-4 w-4" aria-hidden />}
                >
                  <div className="border-destructive/40 bg-destructive/5 space-y-2 rounded-lg border p-4">
                    <p className="text-destructive font-semibold">
                      {int(vehiculo.eventos_rpm_sobre_sobrevelocidad)}{' '}
                      {vehiculo.eventos_rpm_sobre_sobrevelocidad === 1 ? 'exceso' : 'excesos'} por
                      encima de la sobrevelocidad máxima del motor.
                    </p>
                    <p className="text-muted-foreground text-xs">
                      Un solo exceso deja el Q General en 0 y el estado en «No cumple», sin importar
                      el resto de los puntajes.
                    </p>
                    <dl className="grid grid-cols-2 gap-3 pt-1">
                      <Field label="Q General sin penalizar" value={num(vehiculo.qgen_base, 1)} />
                      <Field label="Q General aplicado" value={num(vehiculo.qgen, 1)} />
                    </dl>
                  </div>
                </Section>
              )}
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
