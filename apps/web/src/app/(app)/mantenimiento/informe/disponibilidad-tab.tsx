'use client';

import { Maximize2, Tv } from 'lucide-react';
import * as React from 'react';

import { ChartCard } from '@/components/charts/chart-card';
import { LineChartCard } from '@/components/charts/line-chart-card';
import { CHART_COLORS } from '@/components/charts/chart-theme';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { KpiCard, fmtInt } from '@/app/(app)/reportes/shared';
import { useHasPermission } from '@/lib/auth';
import { cn } from '@/lib/utils';
import { RankingList, fmtHours, fmtPct100 } from '@/app/(app)/mantenimiento/shared';
import {
  GroupComparisonCard,
  type GroupComparisonRow,
} from '@/components/vehicles/group-comparison-card';
import { useGroupFilterAvailable } from '@/components/vehicles/group-filter';
import {
  useDisponibilidad,
  useDisponibilidadPorGrupo,
  useDisponibilidadTimeseries,
  type MttoFilters,
  type PlacaDowntime,
} from '@/lib/mantenimiento';
import { TvModeScreen } from './tv-mode';

/**
 * Tab de disponibilidad del Informe Mtto. Monta sus hooks solo cuando el tab
 * está activo; la selección mensual llega del shell para que todos los tabs
 * compartan el mismo filtro cruzado.
 */
export function DisponibilidadTab({
  filters,
  activeFilters,
  selectedMonth,
  onMonthChange,
  onSelectGroup,
}: {
  filters: MttoFilters;
  activeFilters: MttoFilters;
  selectedMonth: string | null;
  onMonthChange: (month: string | null) => void;
  /** Clic en una barra del comparativo por grupo → filtrar el módulo. */
  onSelectGroup?: (groupId: string) => void;
}) {
  const { data, isLoading } = useDisponibilidad(activeFilters, 20);
  const { data: ts, isLoading: tsLoading } = useDisponibilidadTimeseries(filters);
  const [detail, setDetail] = React.useState<PlacaDowntime | null>(null);
  // El modo TV es un tablero de operación interna: 'admin' no existe en el
  // catálogo de permisos y solo lo satisface el bypass del rol admin, así que
  // ningún permiso delegable lo habilita.
  const isAdmin = useHasPermission()('admin');
  const [tvMode, setTvMode] = React.useState(false);

  // Comparativo por grupo interno del cliente: solo consulta cuando el
  // comparativo aplica (una flota en alcance y con grupos).
  const groupComparisonAvailable = useGroupFilterAvailable();
  const porGrupo = useDisponibilidadPorGrupo(activeFilters, groupComparisonAvailable);
  const groupRows: GroupComparisonRow[] = React.useMemo(
    () =>
      (porGrupo.data ?? []).map((bucket) => ({
        groupId: bucket.group_id,
        values: {
          placas: bucket.placas,
          should_hours: bucket.should_hours,
          downtime_hours: bucket.downtime_hours,
        },
      })),
    [porGrupo.data],
  );

  const s = data?.summary;
  const selectedMonthLabel = ts?.find((point) => point.month === selectedMonth)?.label;
  const topData = data;
  // Flota = dimensión secundaria; se muestra solo si el scope abarca >=2 flotas.
  const multiFleet = (data?.porFlota?.length ?? 0) >= 2;

  if (tvMode) {
    // El modo TV usa el alcance ya elegido en la barra del módulo (mes cruzado
    // incluido) y monta sus propias consultas.
    return <TvModeScreen filters={activeFilters} onClose={() => setTvMode(false)} />;
  }

  return (
    <div className="space-y-3">
      {isAdmin && (
        <div className="flex justify-end">
          <button
            type="button"
            onClick={() => setTvMode(true)}
            className="bg-brand-gray/5 text-brand-gray hover:bg-brand-gray/10 flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm font-semibold transition-colors"
          >
            <Tv size={15} />
            Modo TV
          </button>
        </div>
      )}

      <section className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
        <KpiCard
          label="Disp. Mecánica"
          value={fmtPct100(s?.availabilityPctMec)}
          hint="Horas en taller sobre calendario"
        />
        <KpiCard
          label="Disp. Proyecto"
          value={fmtPct100(s?.availabilityPctProj)}
          hint="Solo OTs que afectan disponibilidad"
        />
        <KpiCard
          label="Horas no disp. (taller)"
          value={fmtHours(s?.unavailableHoursMec)}
          hint="Intervalos unidos por placa"
        />
        <KpiCard
          label="Horas no disp. (proyecto)"
          value={fmtHours(s?.unavailableHoursProj)}
          hint="Base del indicador de proyecto"
        />
        <KpiCard
          label="Placas en alcance"
          value={fmtInt(s?.placas)}
          hint={`${fmtHours(s?.shouldHours)} calendario`}
        />
      </section>

      <LineChartCard
        title="Curva de disponibilidad"
        subtitle="Mecánica vs. proyecto por mes"
        data={ts ?? []}
        xKey="label"
        isLoading={tsLoading}
        height={280}
        action={
          selectedMonthLabel ? (
            <button
              type="button"
              onClick={() => onMonthChange(null)}
              className="bg-accent-blue/10 text-accent-blue hover:bg-accent-blue/20 rounded-pill px-3 py-1 text-xs font-semibold transition-colors [transition-timing-function:cubic-bezier(0.4,0,0.2,1)]"
              aria-label={`Quitar selección de ${selectedMonthLabel}`}
            >
              Viendo: {selectedMonthLabel} ×
            </button>
          ) : null
        }
        series={[
          {
            key: 'mecanica',
            name: 'Mecánica',
            color: CHART_COLORS.yellow,
            format: fmtPct100,
            labelPosition: 'top',
          },
          {
            key: 'proyecto',
            name: 'Proyecto',
            color: CHART_COLORS.blue,
            format: fmtPct100,
            labelPosition: 'bottom',
          },
        ]}
        selectedBucket={selectedMonthLabel ?? null}
        onBucketClick={(_label, payload) => {
          const month = typeof payload?.month === 'string' ? payload.month : null;
          if (month) onMonthChange(selectedMonth === month ? null : month);
        }}
      />

      <ChartCard
        title="Top placas por horas no disponibles"
        subtitle={
          selectedMonthLabel
            ? `Solo ${selectedMonthLabel}; clic de nuevo para ver todo el rango.`
            : 'Intervalos unidos por placa; clic para ver el detalle de OTs'
        }
        isLoading={isLoading}
        isEmpty={!isLoading && (topData?.topPlacas.length ?? 0) === 0}
        height={
          topData && topData.topPlacas.length
            ? Math.min(topData.topPlacas.length, 12) * 34 + 8
            : 120
        }
      >
        <div className="space-y-1.5">
          {(topData?.topPlacas ?? []).slice(0, 12).map((row, index) => {
            const max = Math.max(...(topData?.topPlacas ?? []).map((r) => r.hours), 1);
            return (
              <button
                key={`${selectedMonth ?? 'range'}-${row.plate}`}
                onClick={() => setDetail(row)}
                className="hover:bg-muted/60 flex w-full items-center gap-3 rounded-md px-2 py-1 text-left"
              >
                <span className="w-20 shrink-0 font-mono text-sm">{row.plate}</span>
                <div className="bg-muted rounded-pill h-2.5 flex-1 overflow-hidden">
                  <div
                    className="bg-brand-red rounded-pill motion-safe:animate-downtime-bar-grow h-full origin-left motion-safe:transition-[width] motion-safe:duration-500 motion-safe:[transition-timing-function:cubic-bezier(0.4,0,0.2,1)] motion-reduce:animate-none"
                    style={{
                      width: `${Math.max(3, (row.hours / max) * 100)}%`,
                      animationDelay: `${index * 35}ms`,
                    }}
                  />
                </div>
                <span className="w-20 shrink-0 text-right font-mono text-sm tabular-nums">
                  {fmtHours(row.hours)}
                </span>
                <Maximize2 size={13} className="text-muted-foreground shrink-0" />
              </button>
            );
          })}
        </div>
      </ChartCard>

      <section className={cn('grid gap-3', multiFleet ? 'md:grid-cols-2' : 'md:grid-cols-1')}>
        {/* Flota es dimensión secundaria: solo con >=2 flotas en el scope. */}
        {multiFleet && (
          <RankingList
            title="Menor disponibilidad mecánica (flota)"
            rows={(data?.porFlota ?? [])
              .slice(0, 10)
              .map((f) => ({ name: f.name, value: f.availabilityPct }))}
            format={fmtPct100}
            emptyText="Sin flotas en alcance."
          />
        )}
        <RankingList
          title="Más horas no disponibles (CD)"
          rows={[...(data?.porCd ?? [])]
            .sort((a, b) => b.unavailableHours - a.unavailableHours)
            .slice(0, 10)
            .map((c) => ({ name: c.name, value: c.unavailableHours }))}
          format={fmtHours}
          emptyText="Sin horas no disponibles."
        />
      </section>

      {/* Comparativo por grupo interno del cliente (se oculta solo si no aplica). */}
      <GroupComparisonCard
        title="Disponibilidad por grupo"
        subtitle="Horas fuera de servicio sobre calendario; peor disponibilidad arriba. Clic filtra el módulo."
        rows={groupRows}
        derive={(t) =>
          (t.should_hours ?? 0) > 0
            ? (1 - (t.downtime_hours ?? 0) / (t.should_hours ?? 1)) * 100
            : null
        }
        format={fmtPct100}
        detail={(t) =>
          `${fmtInt(t.placas ?? 0)} placas · ${fmtHours(t.downtime_hours ?? 0)} no disp.`
        }
        reference={
          s?.availabilityPctMec != null
            ? { label: 'Disponibilidad del alcance', value: s.availabilityPctMec }
            : null
        }
        sortDesc={false}
        onSelectGroup={onSelectGroup}
        isLoading={porGrupo.isLoading}
      />

      <Dialog open={detail != null} onOpenChange={(o) => !o && setDetail(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>
              {detail?.plate} · {fmtHours(detail?.hours)} inoperativo · {detail?.fleet}
            </DialogTitle>
          </DialogHeader>
          <div className="max-h-[60vh] space-y-2 overflow-y-auto">
            {(detail?.orders ?? []).length === 0 ? (
              <p className="text-muted-foreground text-sm">Sin OTs detalladas para el periodo.</p>
            ) : (
              (detail?.orders ?? []).map((ot) => {
                const closed =
                  (ot.status ?? '').toLowerCase().includes('close') ||
                  (ot.status ?? '').toLowerCase().includes('terminad');
                return (
                  <div key={ot.number} className="rounded-md border p-3 text-sm">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium">OT #{ot.number}</span>
                      <span className="rounded-pill bg-muted px-2 py-0.5 text-xs">
                        {ot.type ?? 'Sin tipo'}
                      </span>
                      <span
                        className={
                          'rounded-pill px-2 py-0.5 text-xs ' +
                          (closed
                            ? 'bg-accent-lime/20 text-brand-gray'
                            : 'bg-accent-yellow/20 text-brand-gray')
                        }
                      >
                        {closed ? 'Cerrada' : 'Abierta'}
                      </span>
                    </div>
                    {ot.reason && (
                      <p className="text-muted-foreground mt-1 italic">
                        &ldquo;{ot.reason.trim()}&rdquo;
                      </p>
                    )}
                    <div className="text-muted-foreground mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
                      <span>
                        Desde: <strong>{ot.start.replace('T', ' ').slice(0, 16)}</strong>
                      </span>
                      <span>
                        Hasta:{' '}
                        <strong>
                          {ot.technicalCompletion
                            ? ot.technicalCompletion.replace('T', ' ').slice(0, 16)
                            : 'En taller (abierta)'}
                        </strong>
                      </span>
                      <span>
                        Parada: <strong>{fmtHours(ot.overlapHours)}</strong>
                      </span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
