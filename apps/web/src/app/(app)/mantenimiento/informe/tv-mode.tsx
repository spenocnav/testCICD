'use client';

import { ClipboardList, X } from 'lucide-react';
import * as React from 'react';
import { createPortal } from 'react-dom';

import { ChartCard } from '@/components/charts/chart-card';
import { CHART_COLORS, CHART_TONES } from '@/components/charts/chart-theme';
import { GaugeCard } from '@/components/charts/gauge-card';
import { LineChartCard } from '@/components/charts/line-chart-card';
import { PageTitle } from '@/components/layout/page-title';
import { KpiCard, fmtInt } from '@/app/(app)/reportes/shared';
import {
  RankingList,
  fmtHours,
  fmtPct100,
  monthsAgo,
  today,
} from '@/app/(app)/mantenimiento/shared';
import { cn } from '@/lib/utils';
import {
  useConfiabilidad,
  useDisponibilidad,
  useDisponibilidadTimeseries,
  useOrdenes,
  type MttoFilters,
  type OpenOrderSummary,
} from '@/lib/mantenimiento';

/**
 * Modo TV del tab de Disponibilidad: una sola pantalla para un tablero colgado
 * en pared, sin interacción. Cuatro cuadrantes fijos —indicadores, tendencia
 * mensual, disponibilidad por flota y monitor de órdenes abiertas—.
 *
 * No define estilo propio: reutiliza los MISMOS componentes y la misma paleta
 * que el resto del portal (`GaugeCard`, `KpiCard`, `LineChartCard`,
 * `RankingList`, `ChartCard`). Un tablero con colores o gráficos inventados
 * dejaría de parecer la aplicación y, peor, mostraría la misma métrica con dos
 * lenguajes visuales distintos según la pantalla.
 *
 * Reglas de diseño que no conviene revertir:
 * - Nada es clicable dentro de los cuadrantes. Si algo hay que explorar, se
 *   hace en el tab normal; aquí no hay nadie con un mouse.
 * - Las listas largas ROTAN por páginas en vez de hacer scroll: un tablero se
 *   lee de un vistazo y un scroll a medio camino esconde filas para siempre.
 * - Las metas son de PRESENTACIÓN (las zonas del gauge). No entran en ningún
 *   cálculo del backend ni cambian una cifra publicada.
 */

// Metas del tablero. Viven aquí porque mantenimiento no tiene tabla de
// calibración por flota: son el umbral con el que se pintan las zonas.
const META = {
  mecanica: 94,
  proyecto: 96,
  /** MTTR: se cumple por DEBAJO del umbral. */
  mttrHoras: 24,
  /** MTBF: se cumple por ENCIMA del umbral. */
  mtbfHoras: 500,
} as const;

const ROTATE_MS = 12_000;
const REFRESH_MS = 120_000;
const FLEETS_PER_PAGE = 8;
const ORDERS_PER_PAGE = 10;

/**
 * Zonas de disponibilidad. `GaugeCard` colorea por `valor <= upto`, así que van
 * de peor a mejor y la última cierra en el máximo de la escala.
 */
function availabilityZones(meta: number) {
  return [
    { upto: meta - 4, color: CHART_COLORS.red, label: `Crítica (<${meta - 4}%)` },
    { upto: meta, color: CHART_TONES.yellowDark, label: `Bajo meta (<${meta}%)` },
    { upto: 100, color: CHART_TONES.limeDark, label: `En meta (≥${meta}%)` },
  ];
}

/**
 * Escala y zonas de un gauge de horas (MTTR/MTBF).
 *
 * `GaugeCard` RECORTA el valor al máximo de la escala y muestra el recortado,
 * así que una escala fija mentiría: un MTTR de 142 h sobre un máximo de 48
 * imprimiría "48.0 h". El máximo se estira hasta el valor observado y la última
 * zona cierra ahí, que además es lo que hace que la aguja signifique algo.
 *
 * `lowerIsBetter` invierte el orden de los colores, no la escala: en MTTR el
 * verde está al principio y en MTBF al final.
 */
function hoursGauge(value: number | null | undefined, meta: number, lowerIsBetter: boolean) {
  const max = Math.max(meta * 2, Math.ceil(value ?? 0));
  const zones = lowerIsBetter
    ? [
        { upto: meta, color: CHART_TONES.limeDark, label: `En meta (<${meta} h)` },
        { upto: meta * 1.5, color: CHART_TONES.yellowDark, label: 'Sobre meta' },
        { upto: max, color: CHART_COLORS.red, label: 'Muy sobre meta' },
      ]
    : [
        { upto: meta / 2, color: CHART_COLORS.red, label: 'Muy bajo meta' },
        { upto: meta, color: CHART_TONES.yellowDark, label: 'Bajo meta' },
        { upto: max, color: CHART_TONES.limeDark, label: `En meta (>${meta} h)` },
      ];
  return { max, zones };
}

function openFor(openHours: number): string {
  const d = Math.floor(openHours / 24);
  return d >= 1 ? `${d} d` : `${Math.round(openHours)} h`;
}

/** Rota el índice de página cada `ROTATE_MS`; se queda quieto con una sola página. */
function useRotatingPage(pageCount: number): number {
  const [page, setPage] = React.useState(0);
  React.useEffect(() => {
    if (pageCount <= 1) {
      setPage(0);
      return;
    }
    const id = window.setInterval(() => setPage((p) => (p + 1) % pageCount), ROTATE_MS);
    return () => window.clearInterval(id);
  }, [pageCount]);
  return Math.min(page, Math.max(0, pageCount - 1));
}

/**
 * Alto del área de gráfico de un cuadrante. Los componentes del portal reciben
 * la altura en px y un tablero no puede depender del scroll, así que se deriva
 * del viewport en vez de fijarse a un número.
 */
function useQuadrantChartHeight(): number {
  const [height, setHeight] = React.useState(260);
  React.useEffect(() => {
    const measure = () => setHeight(Math.max(180, Math.round(window.innerHeight / 2) - 170));
    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, []);
  return height;
}

/** Estado de plazo de una OT abierta, con los tonos de badge del portal. */
function DeadlineBadge({ overdue }: { overdue: boolean | null }) {
  const label = overdue == null ? 'Sin estimado' : overdue ? 'Excedida' : 'En plazo';
  return (
    <span
      className={cn(
        'rounded-pill px-2 py-0.5 text-xs font-medium',
        overdue == null
          ? 'bg-muted text-muted-foreground'
          : overdue
            ? 'bg-rose-100 text-rose-700'
            : 'bg-emerald-100 text-emerald-700',
      )}
    >
      {label}
    </span>
  );
}

function OpenOrdersTable({ rows, height }: { rows: OpenOrderSummary[]; height: number }) {
  const pageCount = Math.max(1, Math.ceil(rows.length / ORDERS_PER_PAGE));
  const page = useRotatingPage(pageCount);
  const slice = rows.slice(page * ORDERS_PER_PAGE, page * ORDERS_PER_PAGE + ORDERS_PER_PAGE);

  return (
    <ChartCard
      title="Monitor de órdenes abiertas"
      subtitle={
        pageCount > 1
          ? `${rows.length} OTs abiertas, más antiguas primero · página ${page + 1} de ${pageCount}`
          : 'OTs abiertas ahora, más antiguas primero'
      }
      isEmpty={rows.length === 0}
      height={height}
    >
      <table className="w-full table-fixed text-sm">
        <thead>
          <tr className="text-muted-foreground text-xs">
            <th className="w-[13%] pb-2 text-left font-semibold">Orden</th>
            <th className="w-[16%] pb-2 text-left font-semibold">Vehículo</th>
            <th className="w-[21%] pb-2 text-left font-semibold">Flota</th>
            <th className="w-[19%] pb-2 text-left font-semibold">Tipo</th>
            <th className="w-[19%] pb-2 text-left font-semibold">Estado tiempo</th>
            <th className="w-[12%] pb-2 text-right font-semibold">Abierta</th>
          </tr>
        </thead>
        <tbody>
          {slice.map((row) => (
            <tr key={row.number} className="border-t">
              <td className="py-1.5 font-mono tabular-nums">{row.number}</td>
              <td className="py-1.5 font-mono">{row.plate}</td>
              <td className="truncate py-1.5" title={row.fleet}>
                {row.fleet}
              </td>
              <td className="text-muted-foreground truncate py-1.5" title={row.type ?? ''}>
                {row.type ?? '—'}
              </td>
              <td className="py-1.5">
                <DeadlineBadge overdue={row.overdue} />
              </td>
              <td
                className={cn(
                  'py-1.5 text-right font-mono tabular-nums',
                  row.overdue ? 'text-brand-red font-semibold' : undefined,
                )}
              >
                {openFor(row.openHours)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </ChartCard>
  );
}

/**
 * `filters` gobierna KPIs, flotas y órdenes (es el alcance que el usuario ya
 * eligió en la barra del módulo). La tendencia usa SIEMPRE los últimos 6 meses
 * calendario: un tablero de pared debe mostrar la trayectoria, no el recorte de
 * fechas con el que alguien abrió la pantalla.
 */
export function TvModeScreen({ filters, onClose }: { filters: MttoFilters; onClose: () => void }) {
  const trendFilters = React.useMemo<MttoFilters>(
    () => ({ ...filters, date_from: monthsAgo(6), date_to: today() }),
    [filters],
  );

  const disp = useDisponibilidad(filters, 20);
  const conf = useConfiabilidad(filters);
  const ord = useOrdenes(filters);
  const ts = useDisponibilidadTimeseries(trendFilters);
  const chartHeight = useQuadrantChartHeight();
  const rootRef = React.useRef<HTMLDivElement>(null);

  // Refresco periódico: los hechos de mantenimiento se replican una vez al día,
  // así que 2 min es de sobra y no castiga al pool (PERF-009).
  const refetchAll = React.useCallback(() => {
    void disp.refetch();
    void conf.refetch();
    void ord.refetch();
    void ts.refetch();
  }, [disp, conf, ord, ts]);
  const refetchRef = React.useRef(refetchAll);
  refetchRef.current = refetchAll;
  React.useEffect(() => {
    const id = window.setInterval(() => refetchRef.current(), REFRESH_MS);
    return () => window.clearInterval(id);
  }, []);

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Pantalla completa sobre el PROPIO tablero, no sobre `documentElement`: un
  // elemento en fullscreen entra al top layer y nada del shell —barra lateral,
  // barra superior, diálogos— puede pintarse encima. Pidiéndola sobre la raíz,
  // el shell seguía compartiendo capa con el overlay y asomaba por los bordes.
  // Best-effort: si el navegador la niega, el overlay ya cubre la ventana.
  React.useEffect(() => {
    const el = rootRef.current;
    void el?.requestFullscreen?.().catch(() => undefined);
    return () => {
      if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
    };
  }, []);

  // El fondo no debe poder desplazarse detrás del tablero.
  React.useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  }, []);

  const s = disp.data?.summary;
  const c = conf.data;
  const openOrders = ord.data?.currentlyOpenOrders ?? [];
  const vehiclesInWorkshop = new Set(openOrders.map((o) => o.plate)).size;
  const overdueNow = openOrders.filter((o) => o.overdue === true).length;

  const fleets = React.useMemo(
    () =>
      [...(disp.data?.porFlota ?? [])]
        .sort((a, b) => a.availabilityPct - b.availabilityPct)
        .map((f) => ({ name: f.name, value: f.availabilityPct })),
    [disp.data?.porFlota],
  );
  const fleetPageCount = Math.max(1, Math.ceil(fleets.length / FLEETS_PER_PAGE));
  const fleetPage = useRotatingPage(fleetPageCount);
  const fleetSlice = fleets.slice(
    fleetPage * FLEETS_PER_PAGE,
    fleetPage * FLEETS_PER_PAGE + FLEETS_PER_PAGE,
  );

  // El tablero se monta en `document.body` y NO donde vive el componente: es UI
  // de viewport, y colgada del `<main>` queda a merced del apilamiento y de los
  // contenedores del shell.
  return createPortal(
    <div
      ref={rootRef}
      className="fixed inset-0 z-[100] overflow-y-auto p-4"
      // El mismo degradado que `body::before` en globals.css: el tablero es la
      // aplicación a pantalla completa, no otra superficie.
      style={{ background: 'linear-gradient(160deg, #f7f9f8 0%, #e9edeb 55%, #e2e7e4 100%)' }}
    >
      <header className="mb-3 flex items-center gap-3">
        <PageTitle
          icon={ClipboardList}
          title="Disponibilidad de flotas"
          description={`Modo TV · ${filters.date_from} a ${filters.date_to}${
            filters.placas?.length ? ` · ${filters.placas.length} placas` : ' · todas las placas'
          }`}
          iconClassName="bg-accent-blue/10 text-accent-blue"
        />
        <button
          type="button"
          onClick={onClose}
          aria-label="Salir del modo TV"
          className="bg-card hover:bg-muted ml-auto flex h-9 items-center gap-2 rounded-md border px-3 text-sm font-semibold transition-colors"
        >
          <X size={15} />
          Salir
        </button>
      </header>

      <div className="grid gap-3 xl:grid-cols-2">
        {/* 1 · Indicadores */}
        <div className="space-y-3">
          <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <GaugeCard
              title="Disp. Mecánica"
              value={s?.availabilityPctMec ?? null}
              hint={`Meta: ≥${META.mecanica}%`}
              zones={availabilityZones(META.mecanica)}
            />
            <GaugeCard
              title="Disp. Proyecto"
              value={s?.availabilityPctProj ?? null}
              hint={`Meta: ≥${META.proyecto}%`}
              zones={availabilityZones(META.proyecto)}
            />
            <GaugeCard
              title="MTTR"
              value={c?.mttrHoursAvg ?? null}
              unit=" h"
              hint={`Meta: <${META.mttrHoras} h`}
              {...hoursGauge(c?.mttrHoursAvg, META.mttrHoras, true)}
            />
            <GaugeCard
              title="MTBF"
              value={c?.mtbfHoursAvg ?? null}
              unit=" h"
              hint={`Meta: >${META.mtbfHoras} h`}
              {...hoursGauge(c?.mtbfHoursAvg, META.mtbfHoras, false)}
            />
          </section>
          <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <KpiCard
              label="Vehículos en taller"
              value={fmtInt(vehiclesInWorkshop)}
              hint="Placas distintas con OT abierta"
              compact
            />
            <KpiCard
              label="Órdenes abiertas"
              value={fmtInt(ord.data?.currentlyOpen ?? 0)}
              hint="OTs en estado activo"
              compact
              index={1}
            />
            <KpiCard
              label="Órdenes excedidas"
              value={fmtInt(overdueNow)}
              hint="Pasaron su fecha estimada de cierre"
              compact
              index={2}
            />
            <KpiCard
              label="Placas en alcance"
              value={fmtInt(s?.placas)}
              hint={`${fmtHours(s?.shouldHours)} calendario`}
              compact
              index={3}
            />
          </section>
        </div>

        {/* 2 · Tendencia: mismas series y colores que la curva del tab */}
        <LineChartCard
          title="Tendencia mensual"
          subtitle="Disponibilidad mecánica vs. proyecto, últimos 6 meses"
          data={ts.data ?? []}
          xKey="label"
          isLoading={ts.isLoading}
          height={chartHeight}
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
        />

        {/* 3 · Disponibilidad por flota: barra y porcentaje, peor arriba */}
        <RankingList
          title={
            fleetPageCount > 1
              ? `Disponibilidad por flota (${fleetPage + 1}/${fleetPageCount})`
              : 'Disponibilidad por flota'
          }
          rows={fleetSlice}
          format={fmtPct100}
          emptyText="Sin flotas en el alcance."
        />

        {/* 4 · Órdenes abiertas */}
        <OpenOrdersTable rows={openOrders} height={chartHeight} />
      </div>
    </div>,
    document.body,
  );
}
