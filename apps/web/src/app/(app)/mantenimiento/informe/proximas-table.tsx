'use client';

import { ChevronDown, ChevronRight, Download } from 'lucide-react';
import * as React from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { buildXlsx, type XlsxCell } from '@/lib/xlsx';
import { cn } from '@/lib/utils';
import type { ProgramacionRutina } from '@/lib/mantenimiento';

const numFmt = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 0 });
const dayFmt = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 1 });

function fmtDate(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString('es-CO', { day: '2-digit', month: 'short', year: 'numeric' });
}

/**
 * Guía de uso de la rutina. Rutinas duales (km/hr, p. ej. "R0 15.000km/500hr")
 * muestran ambos valores — "44.320 km · 1.452 hr" — para no mezclar unidades
 * entre filas (mixers van por horas, tractos por km, LT por ambas).
 */
function fmtDual(km: number | null, hr: number | null): string {
  const parts: string[] = [];
  if (km != null) parts.push(`${numFmt.format(km)} km`);
  if (hr != null) parts.push(`${numFmt.format(hr)} hr`);
  return parts.length ? parts.join(' · ') : '—';
}

/**
 * ETA en días: el umbral que se alcanza primero manda (mínimo de km y hr).
 */
function etaDays(r: ProgramacionRutina): number | null {
  const etas = [r.etaOdometerDays, r.etaHourmeterDays].filter((v): v is number => v != null);
  return etas.length ? Math.min(...etas) : null;
}

/** Badge del estado del plan (fecha manda). */
function StatusBadge({ status }: { status: string | null }) {
  const tone =
    status === 'Vencido' || status === 'Ejecutada vencida'
      ? 'bg-red-100 text-red-700'
      : status === 'Próximo'
        ? 'bg-amber-100 text-amber-700'
        : status === 'Ejecutada a tiempo'
          ? 'bg-emerald-100 text-emerald-700'
          : 'bg-muted text-muted-foreground';
  return (
    <span className={cn('rounded-pill px-2 py-0.5 text-xs font-medium', tone)}>
      {status ?? '—'}
    </span>
  );
}

/** Días para el ingreso: negativo = vencido, positivo = faltan. */
function DaysCell({ days }: { days: number | null }) {
  if (days == null) return <span className="text-muted-foreground">—</span>;
  if (days < 0)
    return <span className="font-semibold text-red-600">vencido {Math.abs(days)} d</span>;
  if (days === 0) return <span className="font-semibold text-amber-600">hoy</span>;
  return <span className="tabular-nums">en {days} d</span>;
}

/** Celda de rutina: nombre + contador de tareas, expandible. */
function RoutineCell({
  r,
  expanded,
  onToggle,
}: {
  r: ProgramacionRutina;
  expanded: boolean;
  onToggle: () => void;
}) {
  const hasTasks = r.tasks.length > 0;
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={!hasTasks}
      className={cn(
        'flex max-w-[320px] items-center gap-1 text-left',
        hasTasks && 'cursor-pointer',
      )}
      title={r.routine ?? undefined}
    >
      {hasTasks ? (
        expanded ? (
          <ChevronDown size={14} className="shrink-0" />
        ) : (
          <ChevronRight size={14} className="shrink-0" />
        )
      ) : null}
      <span className="truncate">
        {r.routine ?? 'Sin rutina'}
        <span className="text-muted-foreground">
          {' '}
          · {r.tasks.length} tarea{r.tasks.length === 1 ? '' : 's'}
          {r.tipo ? ` · ${r.tipo}` : ''}
        </span>
      </span>
    </button>
  );
}

type Variant = 'proximas' | 'historico';

interface Props {
  rows: ProgramacionRutina[];
  isLoading: boolean;
  variant?: Variant;
}

/**
 * Tabla de ocurrencias de rutina (una fila por placa+rutina+fecha, tareas
 * expandibles). Variante `proximas`: pendientes con guía de odómetro/horómetro
 * y ETA. Variante `historico`: ejecutadas con OT y fecha de ejecución.
 */
export function ProgramacionesTable({ rows, isLoading, variant = 'proximas' }: Props) {
  const [open, setOpen] = React.useState<Set<string>>(new Set());
  const isHist = variant === 'historico';

  // Próximas: fecha de ingreso ascendente. Histórico llega desc del API.
  const sorted = React.useMemo(() => {
    const copy = [...rows];
    if (!isHist) {
      copy.sort((a, b) => {
        if (!a.dateToExecute) return 1;
        if (!b.dateToExecute) return -1;
        return a.dateToExecute.localeCompare(b.dateToExecute);
      });
    }
    return copy;
  }, [rows, isHist]);

  const keyOf = (r: ProgramacionRutina) => `${r.plate}|${r.routine}|${r.dateToExecute}`;
  const toggle = (k: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });

  const colSpan = isHist ? 8 : 9;

  const exportProximas = () => {
    if (isHist || sorted.length === 0) return;

    const headers: XlsxCell[] = [
      { value: 'Placa', style: 3 },
      { value: 'Flota', style: 3 },
      { value: 'Centro de distribución', style: 3 },
      { value: 'Rutina', style: 3 },
      { value: 'Tareas', style: 3 },
      { value: 'Tipo', style: 3 },
      { value: 'Fuente', style: 3 },
      { value: 'Fecha de ingreso', style: 3 },
      { value: 'Días para ingreso', style: 3 },
      { value: 'Estado', style: 3 },
      { value: 'Odómetro actual (km)', style: 3 },
      { value: 'Horómetro actual (hr)', style: 3 },
      { value: 'Objetivo odómetro (km)', style: 3 },
      { value: 'Objetivo horómetro (hr)', style: 3 },
      { value: 'Faltan (km)', style: 3 },
      { value: 'Faltan (hr)', style: 3 },
      { value: 'ETA (días)', style: 3 },
    ];
    const rows: XlsxCell[][] = [
      [{ value: 'Próximas programaciones', style: 1 }],
      [{ value: `Rutinas pendientes: ${sorted.length}` }],
      headers,
      ...sorted.map((r) => [
        { value: r.plate },
        { value: r.fleet },
        { value: r.cd },
        { value: r.routine },
        { value: r.tasks.join(' | ') },
        { value: r.tipo },
        { value: r.source },
        { value: r.dateToExecute },
        { value: r.daysToExecute, style: 6 },
        { value: r.status },
        { value: r.currentOdometer, style: 6 },
        { value: r.currentHourmeter, style: 6 },
        { value: r.odometerToExecute, style: 6 },
        { value: r.hourmeterToExecute, style: 6 },
        { value: r.odometerDiff, style: 6 },
        { value: r.hourmeterDiff, style: 6 },
        { value: etaDays(r), style: 6 },
      ]),
    ];
    const taskRows: XlsxCell[][] = [
      [{ value: 'Tareas de próximas programaciones', style: 1 }],
      [
        { value: 'Placa', style: 3 },
        { value: 'Flota', style: 3 },
        { value: 'Centro de distribución', style: 3 },
        { value: 'Rutina', style: 3 },
        { value: 'Fecha de ingreso', style: 3 },
        { value: 'Estado', style: 3 },
        { value: 'Tarea', style: 3 },
      ],
      ...sorted.flatMap((r) =>
        r.tasks.map((task) => [
          { value: r.plate },
          { value: r.fleet },
          { value: r.cd },
          { value: r.routine },
          { value: r.dateToExecute },
          { value: r.status },
          { value: task },
        ]),
      ),
    ];
    if (taskRows.length === 2) {
      taskRows.push([{ value: 'Las rutinas seleccionadas no tienen tareas.' }]);
    }
    const workbook = buildXlsx([
      { name: 'Próximas', rows },
      { name: 'Tareas', rows: taskRows },
    ]);
    const url = URL.createObjectURL(workbook);
    const link = document.createElement('a');
    link.href = url;
    link.download = `proximas_programaciones_${new Date().toISOString().slice(0, 10)}.xlsx`;
    link.click();
    URL.revokeObjectURL(url);
    toast.success(`${sorted.length} rutinas exportadas.`);
  };

  return (
    <div className="bg-card rounded-lg border">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div>
          <h3 className="font-heading text-sm font-bold tracking-tight">
            {isHist ? 'Histórico de programaciones' : 'Próximas programaciones'}
          </h3>
          <p className="text-muted-foreground text-xs">
            {isHist
              ? 'Rutinas ejecutadas en el periodo seleccionado, con su OT de ejecución.'
              : 'Rutinas pendientes por fecha de ingreso (próximos 90 días y vencidas sin ejecutar).'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {!isHist && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={exportProximas}
              disabled={isLoading || sorted.length === 0}
              title="Exportar próximas programaciones a Excel"
            >
              <Download aria-hidden />
              Exportar Excel
            </Button>
          )}
          <span className="text-muted-foreground text-xs tabular-nums">
            {isLoading ? '…' : `${rows.length} rutinas`}
          </span>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[900px] whitespace-nowrap text-sm">
          <thead>
            <tr className="text-muted-foreground border-b text-left text-xs">
              <th className="px-4 py-2 font-medium">Placa</th>
              <th className="px-4 py-2 font-medium">Rutina</th>
              <th className="px-4 py-2 font-medium">Ingreso</th>
              {isHist ? (
                <th className="px-4 py-2 font-medium">Ejecutada</th>
              ) : (
                <th className="px-4 py-2 font-medium">Días</th>
              )}
              <th className="px-4 py-2 font-medium">Estado</th>
              {!isHist && <th className="px-4 py-2 text-right font-medium">Actual</th>}
              <th className="px-4 py-2 text-right font-medium">Objetivo</th>
              {!isHist && (
                <>
                  <th className="px-4 py-2 text-right font-medium">Faltan</th>
                  <th className="px-4 py-2 text-right font-medium">ETA</th>
                </>
              )}
              {isHist && <th className="px-4 py-2 text-right font-medium">OT</th>}
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={colSpan} className="text-muted-foreground px-4 py-8 text-center">
                  Cargando…
                </td>
              </tr>
            ) : sorted.length === 0 ? (
              <tr>
                <td colSpan={colSpan} className="text-muted-foreground px-4 py-8 text-center">
                  {isHist
                    ? 'Sin rutinas ejecutadas en la selección.'
                    : 'Sin rutinas pendientes en la selección.'}
                </td>
              </tr>
            ) : (
              sorted.map((r) => {
                const k = keyOf(r);
                // Actual solo en las unidades que tienen objetivo definido.
                const curKm = r.odometerToExecute != null ? r.currentOdometer : null;
                const curHr = r.hourmeterToExecute != null ? r.currentHourmeter : null;
                const eta = etaDays(r);
                const expanded = open.has(k);
                return (
                  <React.Fragment key={k}>
                    <tr className="border-b last:border-0">
                      <td className="px-4 py-2 font-mono font-medium">{r.plate}</td>
                      <td className="px-4 py-2">
                        <RoutineCell r={r} expanded={expanded} onToggle={() => toggle(k)} />
                      </td>
                      <td className="whitespace-nowrap px-4 py-2">{fmtDate(r.dateToExecute)}</td>
                      {isHist ? (
                        <td className="whitespace-nowrap px-4 py-2">
                          {fmtDate(r.woExecutionDate)}
                        </td>
                      ) : (
                        <td className="whitespace-nowrap px-4 py-2">
                          <DaysCell days={r.daysToExecute} />
                        </td>
                      )}
                      <td className="px-4 py-2">
                        <StatusBadge status={r.status} />
                      </td>
                      {!isHist && (
                        <td className="px-4 py-2 text-right tabular-nums">
                          {fmtDual(curKm, curHr)}
                        </td>
                      )}
                      <td className="px-4 py-2 text-right tabular-nums">
                        {fmtDual(r.odometerToExecute, r.hourmeterToExecute)}
                      </td>
                      {!isHist && (
                        <>
                          <td className="px-4 py-2 text-right tabular-nums">
                            {fmtDual(r.odometerDiff, r.hourmeterDiff)}
                          </td>
                          <td className="px-4 py-2 text-right tabular-nums">
                            {eta == null ? '—' : `${dayFmt.format(eta)} d`}
                          </td>
                        </>
                      )}
                      {isHist && (
                        <td className="px-4 py-2 text-right tabular-nums">
                          {r.woNumber == null ? '—' : `#${r.woNumber}`}
                        </td>
                      )}
                    </tr>
                    {expanded && (
                      <tr className="border-b last:border-0">
                        <td />
                        <td colSpan={colSpan - 1} className="px-4 pb-2 pt-2">
                          <ul className="text-muted-foreground list-disc space-y-0.5 whitespace-normal pl-5 text-xs">
                            {r.tasks.map((t) => (
                              <li key={t}>{t}</li>
                            ))}
                          </ul>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
