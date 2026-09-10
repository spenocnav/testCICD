'use client';

import * as React from 'react';

import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { CalificacionMetodologia, CalificacionUmbrales } from '@/lib/types';

import { fmt } from './shared';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  metodologia?: CalificacionMetodologia;
  umbrales?: CalificacionUmbrales;
}

const pesoPct = (peso: number) => `${fmt(peso * 100, 0)} %`;

function Seccion({ titulo, children }: { titulo: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="font-heading text-sm font-bold tracking-tight">{titulo}</h3>
      {children}
    </section>
  );
}

/**
 * Explica cómo se calcula la calificación. Todo el contenido numérico sale de
 * `metodologia`/`umbrales` que publica el API: si negocio recalibra un peso o
 * un umbral, este texto cambia solo. Escribirlo a mano fue lo que descuadró el
 * gauge respecto de la tabla.
 */
export function CalificacionMetodologiaDialog({
  open,
  onOpenChange,
  metodologia,
  umbrales,
}: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Cómo se calcula la calificación</DialogTitle>
          <DialogDescription>
            La nota de cada vehículo combina sus hábitos de operación y sus hábitos seguros.
          </DialogDescription>
        </DialogHeader>

        {!metodologia ? (
          <p className="text-muted-foreground text-sm">Cargando metodología…</p>
        ) : (
          <div className="space-y-6 text-sm">
            <Seccion titulo="Calificación general (Q General)">
              <p className="text-muted-foreground">
                Promedio ponderado de los dos bloques:{' '}
                <strong className="text-foreground">
                  {pesoPct(metodologia.peso_qho)} operación
                </strong>{' '}
                y{' '}
                <strong className="text-foreground">
                  {pesoPct(metodologia.peso_qhs)} seguridad
                </strong>
                . Si un bloque no es evaluable, la nota se toma del otro.
              </p>
              {umbrales && (
                <div className="flex flex-wrap gap-2 pt-1">
                  <Badge variant="destructive">No cumple &lt; {fmt(umbrales.en_riesgo, 0)}</Badge>
                  <Badge variant="warning">
                    En riesgo {fmt(umbrales.en_riesgo, 0)} – {fmt(umbrales.cumple, 0)}
                  </Badge>
                  <Badge variant="success">Cumple ≥ {fmt(umbrales.cumple, 0)}</Badge>
                </div>
              )}
            </Seccion>

            <Seccion titulo={`Hábitos de operación · ${pesoPct(metodologia.peso_qho)} de la nota`}>
              <p className="text-muted-foreground">
                Mide cómo se usa el motor. Cada componente se lleva a una escala de 0 a 100 y luego
                se pondera:
              </p>
              <ul className="space-y-2">
                {metodologia.componentes_qho.map((componente) => (
                  <li key={componente.nombre} className="border-border rounded-md border p-3">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-medium">{componente.nombre}</span>
                      <Badge variant="info">{pesoPct(componente.peso)}</Badge>
                    </div>
                    <p className="text-muted-foreground mt-1 text-xs">{componente.detalle}</p>
                  </li>
                ))}
              </ul>
            </Seccion>

            <Seccion titulo={`Hábitos seguros · ${pesoPct(metodologia.peso_qhs)} de la nota`}>
              <p className="text-muted-foreground">
                Cuenta los eventos de conducción por cada 1000 km recorridos. Sin eventos la nota es
                100; con{' '}
                <strong className="text-foreground">
                  {fmt(metodologia.eventos_tope_por_1000km, 0)} eventos ponderados por 1000 km
                </strong>{' '}
                o más, es 0. Un vehículo sin kilómetros registrados no es evaluable.
              </p>
              <p className="text-muted-foreground">No todos los eventos pesan igual:</p>
              <ul className="space-y-1">
                {metodologia.eventos_qhs.map((evento) => (
                  <li
                    key={evento.evento}
                    className="border-border flex items-center justify-between gap-3 rounded-md border px-3 py-2"
                  >
                    <span>{evento.evento}</span>
                    <Badge variant={evento.peso >= 1 ? 'warning' : 'default'}>
                      × {fmt(evento.peso, 2)}
                    </Badge>
                  </li>
                ))}
              </ul>
              <p className="text-muted-foreground text-xs">
                Un evento de un tipo no listado cuenta ×{' '}
                {fmt(metodologia.peso_evento_no_listado, 2)}.
              </p>
              {metodologia.eventos_excluidos.length > 0 && (
                <p className="text-muted-foreground text-xs">
                  No cuentan aquí: {metodologia.eventos_excluidos.join(', ')} — ya se penalizan
                  dentro de hábitos de operación, y contarlos dos veces distorsionaba la nota.
                </p>
              )}
            </Seccion>

            {/* La regla la redacta el API: aquí solo se pinta, igual que el
                resto de esta explicación. Escribirla a mano la desfasaría de la
                calibración vigente. */}
            {metodologia.penalizacion_sobrevelocidad && (
              <Seccion titulo="Penalización por sobrevelocidad">
                <p className="border-destructive/40 bg-destructive/5 rounded-md border p-3">
                  {metodologia.penalizacion_sobrevelocidad}
                </p>
              </Seccion>
            )}

            {/* Va antes de la agregación porque responde una pregunta previa:
                quién entra en la cifra, y sólo después cómo se combinan. */}
            {metodologia.exposicion_minima && (
              <Seccion titulo="Exposición mínima para calificar">
                <p className="text-muted-foreground">{metodologia.exposicion_minima}</p>
              </Seccion>
            )}

            <Seccion titulo="Cifra de la flota">
              <p className="text-muted-foreground">{metodologia.agregacion}</p>
            </Seccion>

            <p className="text-muted-foreground border-border border-t pt-3 text-xs">
              Los pesos, escalas y umbrales son parámetros de negocio: se recalibran sin cambiar la
              lógica de cálculo, y esta explicación refleja siempre los valores vigentes.
            </p>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
