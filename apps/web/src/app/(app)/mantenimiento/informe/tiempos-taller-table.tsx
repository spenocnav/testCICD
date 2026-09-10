'use client';

import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Clock3,
  FlaskConical,
  Gauge,
  Search,
  TimerReset,
  Wrench,
  X,
} from 'lucide-react';
import * as React from 'react';

import { KpiCard } from '@/app/(app)/reportes/shared';
import { BarChartCard } from '@/components/charts/bar-chart-card';
import { ComposedChartCard } from '@/components/charts/composed-chart-card';
import { DonutChartCard } from '@/components/charts/donut-chart-card';
import { Badge } from '@/components/ui/badge';
import { UserPagination } from '@/components/users/user-pagination';
import {
  useTiemposTaller,
  type MttoFilters,
  type WorkshopOrder as ApiWorkshopOrder,
} from '@/lib/mantenimiento';
import { cn } from '@/lib/utils';

type StageKey =
  | 'recepcion'
  | 'diagnostico'
  | 'autorizacion'
  | 'repuestos'
  | 'reparacion'
  | 'calidad'
  | 'entrega';

type GroupBy = 'site' | 'fleet' | 'vehicle';
type ComparisonSort = 'average' | 'waitingShare' | 'partsShare' | 'repairShare' | 'count';
type ComparisonTableSortKey = 'name' | 'count' | 'average' | 'waitingShare';
type ComparisonTableSort = {
  key: ComparisonTableSortKey;
  direction: 'asc' | 'desc';
} | null;
const ORDERS_PAGE_SIZE = 25;

interface StageDefinition {
  key: StageKey;
  label: string;
  shortLabel: string;
  color: string;
  kind: 'active' | 'waiting' | 'administrative';
}

interface WorkshopOrder {
  id: string;
  site: string;
  fleet: string;
  vehicle: string;
  plate: string;
  openedAt: string;
  closedAt: string | null;
  status: string | null;
  stages: Record<StageKey, number>;
  totalHours: number;
  classifiedHours: number;
  unclassifiedHours: number;
  coveragePct: number;
  preciseHours: number;
  trackingCount: number;
  labelEventCount: number;
  currentLabel: string | null;
}

const STAGES: StageDefinition[] = [
  {
    key: 'recepcion',
    label: 'Recepción e ingreso',
    shortLabel: 'Recepción',
    color: '#185979',
    kind: 'administrative',
  },
  {
    key: 'diagnostico',
    label: 'Diagnóstico',
    shortLabel: 'Diagnóstico',
    color: '#527f93',
    kind: 'active',
  },
  {
    key: 'autorizacion',
    label: 'Espera de autorización',
    shortLabel: 'Autorización',
    color: '#c88700',
    kind: 'waiting',
  },
  {
    key: 'repuestos',
    label: 'Espera de repuestos',
    shortLabel: 'Repuestos',
    color: '#ee2e2f',
    kind: 'waiting',
  },
  {
    key: 'reparacion',
    label: 'Reparación',
    shortLabel: 'Reparación',
    color: '#718f00',
    kind: 'active',
  },
  {
    key: 'calidad',
    label: 'Pruebas y control de calidad',
    shortLabel: 'Calidad',
    color: '#354550',
    kind: 'active',
  },
  {
    key: 'entrega',
    label: 'Listo para entrega',
    shortLabel: 'Entrega',
    color: '#9b945b',
    kind: 'administrative',
  },
];

const TRACKING_LABELS = [
  { id: 1, label: 'Pendiente de bahía', stageKey: 'recepcion' },
  { id: 2, label: 'Sin asignación de técnico', stageKey: 'recepcion' },
  { id: 3, label: 'En diagnóstico', stageKey: 'diagnostico' },
  { id: 4, label: 'Pendiente informe técnico', stageKey: 'diagnostico' },
  { id: 5, label: 'Pendiente de cotización', stageKey: 'autorizacion' },
  { id: 6, label: 'Pendiente aprobación cliente', stageKey: 'autorizacion' },
  { id: 7, label: 'Pendiente aprobación interna', stageKey: 'autorizacion' },
  { id: 8, label: 'Pendiente de repuestos', stageKey: 'repuestos' },
  { id: 9, label: 'En intervención', stageKey: 'reparacion' },
  { id: 10, label: 'En pruebas finales', stageKey: 'calidad' },
] as const satisfies ReadonlyArray<{ id: number; label: string; stageKey: StageKey }>;

function adaptApiOrder(order: ApiWorkshopOrder): WorkshopOrder {
  const stages = Object.fromEntries(
    STAGES.map((stage) => [stage.key, Number(order.stages[stage.key] ?? 0)]),
  ) as Record<StageKey, number>;
  return {
    id: String(order.number),
    site: order.site,
    fleet: order.fleet,
    vehicle: order.vehicle,
    plate: order.plate,
    openedAt: order.openedAt ?? '',
    closedAt: order.closedAt,
    status: order.status,
    stages,
    totalHours: order.totalHours,
    classifiedHours: order.classifiedHours,
    unclassifiedHours: order.unclassifiedHours,
    coveragePct: order.coveragePct,
    preciseHours: order.preciseHours,
    trackingCount: order.trackingCount,
    labelEventCount: order.labelEventCount,
    currentLabel: order.currentLabel,
  };
}

function sum(values: number[]): number {
  return values.reduce((total, value) => total + value, 0);
}

function orderHours(order: WorkshopOrder): number {
  return sum(STAGES.map((stage) => order.stages[stage.key]));
}

function stageHours(orders: WorkshopOrder[], key: StageKey): number {
  return sum(orders.map((order) => order.stages[key]));
}

function isNavitransWorkshop(site: string): boolean {
  return site.trim().toLocaleUpperCase('es-CO').startsWith('NAVITRANS S.A.S.');
}

function filterWorkshopOrders(orders: WorkshopOrder[], filters: MttoFilters): WorkshopOrder[] {
  return orders.filter(
    (order) =>
      !isCancelledOrder(order.status) &&
      (!filters.date_from || order.openedAt.slice(0, 10) >= filters.date_from) &&
      (!filters.date_to || order.openedAt.slice(0, 10) <= filters.date_to) &&
      (!filters.placas?.length || filters.placas.includes(order.plate)),
  );
}

function buildMonthlyStageData(orders: WorkshopOrder[], labels: Map<string, string>) {
  const byMonth = new Map<string, WorkshopOrder[]>();
  for (const order of orders) {
    const month = order.openedAt.slice(0, 7);
    if (!month) continue;
    byMonth.set(month, [...(byMonth.get(month) ?? []), order]);
  }

  return [...byMonth.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([month, rows]) => ({
      month,
      label:
        labels.get(month) ??
        new Intl.DateTimeFormat('es-CO', { month: 'short', year: 'numeric' }).format(
          new Date(`${month}-01T00:00:00Z`),
        ),
      orders: rows.length,
      total: sum(rows.map((row) => row.totalHours)) / rows.length,
      ...Object.fromEntries(
        STAGES.map((stage) => [stage.key, stageHours(rows, stage.key) / rows.length]),
      ),
    }));
}

function percent(value: number, total: number): number {
  return total > 0 ? (value / total) * 100 : 0;
}

function fmtHours(value: number): string {
  return `${value.toLocaleString('es-CO', { maximumFractionDigits: 1 })} h`;
}

function fmtPercent(value: number): string {
  return `${value.toLocaleString('es-CO', { maximumFractionDigits: 1 })} %`;
}

function formatOrderStatus(status: string | null): string {
  if (!status) return 'Sin estado';
  return status
    .trim()
    .toLocaleLowerCase('es-CO')
    .replace(/(^|\s)\S/g, (letter) => letter.toLocaleUpperCase('es-CO'));
}

function normalizeOrderSearch(value: string): string {
  return value
    .toLocaleLowerCase('es-CO')
    .replace(/[·,;|]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function isCancelledOrder(status: string | null): boolean {
  const normalized = normalizeOrderSearch(status ?? '');
  return /^(anulad|cancelad|cancelled|voided|void)\b/.test(normalized);
}

function extractOrderNumber(query: string): number | undefined {
  const match = query.trim().match(/^(\d+)(?:\s*·.*)?$/);
  return match ? Number(match[1]) : undefined;
}

function orderMatchesQuery(order: WorkshopOrder, query: string): boolean {
  const normalized = normalizeOrderSearch(query);
  if (!normalized) return true;
  return normalizeOrderSearch(
    [
      order.id,
      order.plate,
      order.vehicle,
      order.fleet,
      order.site,
      order.status ?? '',
      order.currentLabel ?? '',
    ].join(' '),
  ).includes(normalized);
}

function OrderSearch({
  orders,
  selectedId,
  onSelect,
  query,
  onQueryChange,
}: {
  orders: WorkshopOrder[];
  selectedId: string;
  onSelect: (id: string) => void;
  query: string;
  onQueryChange: (query: string) => void;
}) {
  const [open, setOpen] = React.useState(false);

  const matchingOrders = React.useMemo(() => {
    return orders.filter((order) => orderMatchesQuery(order, query));
  }, [orders, query]);
  const matches = matchingOrders.slice(0, 20);

  function selectOrder(order: WorkshopOrder) {
    onSelect(order.id);
    setOpen(false);
  }

  function clearSelection() {
    onSelect('all');
    onQueryChange('');
    setOpen(false);
  }

  return (
    <div
      className="relative min-w-[280px] flex-[2]"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <label htmlFor="workshop-order-search" className="text-muted-foreground text-xs">
        Abrir una orden del alcance
      </label>
      <div className="relative mt-1">
        <Search
          size={15}
          className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 -translate-y-1/2"
        />
        <input
          id="workshop-order-search"
          role="combobox"
          aria-expanded={open}
          aria-controls="workshop-order-results"
          autoComplete="off"
          value={query}
          placeholder={`Buscar entre ${orders.length} OTs por número, placa, flota…`}
          onFocus={() => setOpen(true)}
          onChange={(event) => {
            if (selectedId !== 'all') onSelect('all');
            onQueryChange(event.target.value);
            setOpen(true);
          }}
          className="border-input bg-background text-foreground focus:ring-ring h-9 w-full rounded-md border py-2 pl-9 pr-9 text-sm shadow-sm outline-none focus:ring-2"
        />
        {(query || selectedId !== 'all') && (
          <button
            type="button"
            aria-label="Limpiar orden seleccionada"
            onClick={clearSelection}
            className="text-muted-foreground hover:text-foreground absolute right-2 top-1/2 -translate-y-1/2 rounded p-1"
          >
            <X size={14} />
          </button>
        )}
      </div>

      {open && (
        <div
          id="workshop-order-results"
          role="listbox"
          className="bg-popover absolute z-30 mt-1 max-h-72 w-full overflow-y-auto rounded-md border p-1 shadow-lg"
        >
          <button
            type="button"
            role="option"
            aria-selected={selectedId === 'all'}
            onClick={clearSelection}
            className="hover:bg-muted w-full rounded px-3 py-2 text-left text-sm"
          >
            <span className="font-medium">Ver consolidado</span>
            <span className="text-muted-foreground ml-2 text-xs">{orders.length} OTs</span>
          </button>
          {matches.length === 0 ? (
            <p className="text-muted-foreground px-3 py-6 text-center text-sm">
              No hay coincidencias dentro del periodo y filtros actuales.
            </p>
          ) : (
            matches.map((order) => (
              <button
                key={order.id}
                type="button"
                role="option"
                aria-selected={selectedId === order.id}
                onClick={() => selectOrder(order)}
                className={cn(
                  'hover:bg-muted flex w-full items-center gap-3 rounded px-3 py-2 text-left text-sm',
                  selectedId === order.id && 'bg-accent-blue/5',
                )}
              >
                <span className="font-mono font-semibold">{order.id}</span>
                <span className="font-mono text-xs">{order.plate}</span>
                <span className="text-muted-foreground min-w-0 flex-1 truncate text-xs">
                  {order.fleet} · {order.site} · {formatOrderStatus(order.status)}
                </span>
              </button>
            ))
          )}
          {matchingOrders.length > matches.length && (
            <p className="text-muted-foreground border-t px-3 py-2 text-xs">
              Mostrando las primeras 20 coincidencias. Escribe más para precisar la búsqueda.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function CycleBar({ orders, compact = false }: { orders: WorkshopOrder[]; compact?: boolean }) {
  const total = sum(orders.map((order) => order.totalHours));
  const classified = sum(orders.map(orderHours));
  const unclassified = Math.max(0, total - classified);

  return (
    <div
      className={cn('bg-muted rounded-pill flex w-full overflow-hidden', compact ? 'h-2.5' : 'h-8')}
      aria-label="Composición porcentual del ciclo completo"
    >
      {STAGES.map((stage) => {
        const value = stageHours(orders, stage.key);
        const share = percent(value, total);
        return (
          <div
            key={stage.key}
            className="flex items-center justify-center overflow-hidden border-r border-white/60 text-[10px] font-bold text-white last:border-0"
            style={{ width: `${share}%`, backgroundColor: stage.color }}
            title={`${stage.label}: ${fmtHours(value)} (${fmtPercent(share)})`}
          >
            {!compact && share >= 8 ? `${share.toFixed(0)}%` : null}
          </div>
        );
      })}
      {unclassified > 0 && (
        <div
          className="flex items-center justify-center overflow-hidden border-r border-white/60 text-[10px] font-bold text-slate-700 last:border-0"
          style={{ width: `${percent(unclassified, total)}%`, backgroundColor: '#cbd5e1' }}
          title={`Sin clasificación: ${fmtHours(unclassified)} (${fmtPercent(percent(unclassified, total))})`}
        >
          {!compact && percent(unclassified, total) >= 8
            ? `${percent(unclassified, total).toFixed(0)}%`
            : null}
        </div>
      )}
    </div>
  );
}

function StageLegend({ orders }: { orders: WorkshopOrder[] }) {
  const total = sum(orders.map((order) => order.totalHours));
  const unclassified = sum(orders.map((order) => order.unclassifiedHours));
  return (
    <div className="grid gap-x-5 gap-y-2 sm:grid-cols-2 xl:grid-cols-4">
      {STAGES.map((stage) => {
        const hours = stageHours(orders, stage.key);
        return (
          <div key={stage.key} className="flex items-center gap-2 text-xs">
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ background: stage.color }}
            />
            <span className="min-w-0 flex-1 truncate" title={stage.label}>
              {stage.label}
            </span>
            <span className="font-mono font-semibold tabular-nums">
              {fmtPercent(percent(hours, total))}
            </span>
          </div>
        );
      })}
      {unclassified > 0 && (
        <div className="flex items-center gap-2 text-xs">
          <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-slate-300" />
          <span className="min-w-0 flex-1 truncate">Sin clasificación</span>
          <span className="font-mono font-semibold tabular-nums">
            {fmtPercent(percent(unclassified, total))}
          </span>
        </div>
      )}
    </div>
  );
}

function TrackingLabelLegend() {
  return (
    <section className="bg-card rounded-lg border p-4">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h3 className="font-heading text-sm font-bold tracking-tight">
            Leyenda de etiquetas de seguimiento
          </h3>
          <p className="text-muted-foreground text-xs">
            Los gráficos agrupan estas 10 etiquetas en las etapas operativas indicadas.
          </p>
        </div>
        <Badge variant="outline">10 etiquetas</Badge>
      </div>
      <div className="grid gap-x-5 gap-y-2 sm:grid-cols-2 xl:grid-cols-3">
        {TRACKING_LABELS.map((trackingLabel) => {
          const stage = STAGES.find((item) => item.key === trackingLabel.stageKey);
          return (
            <div key={trackingLabel.id} className="flex min-w-0 items-center gap-2 text-xs">
              <span
                className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white"
                style={{ backgroundColor: stage?.color }}
              >
                {trackingLabel.id}
              </span>
              <span className="min-w-0 flex-1 truncate" title={trackingLabel.label}>
                {trackingLabel.label}
              </span>
              <span className="text-muted-foreground shrink-0">→ {stage?.shortLabel}</span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function ComparisonSortHeader({
  label,
  sortKey,
  sort,
  align = 'left',
  onSort,
}: {
  label: string;
  sortKey: ComparisonTableSortKey;
  sort: ComparisonTableSort;
  align?: 'left' | 'right';
  onSort: (key: ComparisonTableSortKey) => void;
}) {
  const active = sort?.key === sortKey;
  return (
    <button
      type="button"
      className={cn(
        'hover:text-foreground inline-flex items-center gap-1 transition-colors',
        align === 'right' && 'ml-auto',
      )}
      onClick={() => onSort(sortKey)}
      aria-label={`Ordenar por ${label}`}
    >
      {active ? (
        sort.direction === 'asc' ? (
          <ArrowUp className="h-3.5 w-3.5" aria-hidden />
        ) : (
          <ArrowDown className="h-3.5 w-3.5" aria-hidden />
        )
      ) : (
        <ArrowUpDown className="text-muted-foreground h-3.5 w-3.5" aria-hidden />
      )}
      {label}
    </button>
  );
}

function ComparisonCard({
  orders,
  onGroupSelect,
}: {
  orders: WorkshopOrder[];
  onGroupSelect: (orderIds: string[] | null) => void;
}) {
  const [groupBy, setGroupBy] = React.useState<GroupBy>('site');
  const [selectedName, setSelectedName] = React.useState<string | null>(null);
  const [sortBy, setSortBy] = React.useState<ComparisonSort>('average');
  const [tableSort, setTableSort] = React.useState<ComparisonTableSort>(null);
  const [includeNavitrans, setIncludeNavitrans] = React.useState(false);
  const labelByGroup: Record<GroupBy, string> = {
    site: 'taller aliado',
    fleet: 'flota',
    vehicle: 'vehículo',
  };
  const sortOptions: { value: ComparisonSort; label: string }[] = [
    { value: 'average', label: 'Mayor ciclo' },
    { value: 'waitingShare', label: 'Más espera' },
    { value: 'partsShare', label: 'Más repuestos' },
    { value: 'repairShare', label: 'Más reparación' },
    { value: 'count', label: 'Más OTs' },
  ];
  const workshopOrders = React.useMemo(
    () => (includeNavitrans ? orders : orders.filter((order) => !isNavitransWorkshop(order.site))),
    [includeNavitrans, orders],
  );
  const groups = React.useMemo(() => {
    const grouped = new Map<string, WorkshopOrder[]>();
    for (const order of workshopOrders) {
      const name = order[groupBy];
      grouped.set(name, [...(grouped.get(name) ?? []), order]);
    }
    const groupedRows = [...grouped.entries()].map(([name, rows]) => {
      const total = sum(rows.map((row) => row.totalHours));
      const waiting = sum(
        STAGES.filter((stage) => stage.kind === 'waiting').map((stage) =>
          stageHours(rows, stage.key),
        ),
      );
      const parts = stageHours(rows, 'repuestos');
      const repair = stageHours(rows, 'reparacion');
      return {
        name,
        rows,
        count: rows.length,
        average: total / rows.length,
        total,
        waitingShare: percent(waiting, total),
        partsShare: percent(parts, total),
        repairShare: percent(repair, total),
      };
    });

    if (tableSort) {
      return groupedRows.sort((a, b) => {
        if (tableSort.key === 'name') {
          const delta = a.name.localeCompare(b.name, 'es');
          return tableSort.direction === 'asc' ? delta : -delta;
        }
        const delta = a[tableSort.key] - b[tableSort.key];
        return (
          (tableSort.direction === 'asc' ? delta : -delta) || a.name.localeCompare(b.name, 'es')
        );
      });
    }

    return groupedRows.sort((a, b) => {
      const delta = b[sortBy] - a[sortBy];
      return delta || a.name.localeCompare(b.name, 'es');
    });
  }, [groupBy, sortBy, tableSort, workshopOrders]);
  const selectedGroup = groups.find((group) => group.name === selectedName);

  React.useEffect(() => {
    if (selectedName && !selectedGroup) {
      setSelectedName(null);
      onGroupSelect(null);
    }
  }, [onGroupSelect, selectedGroup, selectedName]);

  function changeGroup(nextGroup: GroupBy) {
    setGroupBy(nextGroup);
    setSelectedName(null);
    onGroupSelect(null);
  }

  function toggleGroup(group: (typeof groups)[number]) {
    const nextSelected = selectedName === group.name ? null : group;
    setSelectedName(nextSelected?.name ?? null);
    onGroupSelect(nextSelected ? nextSelected.rows.map((order) => order.id) : null);
  }

  function changeNavitransVisibility(checked: boolean) {
    setIncludeNavitrans(checked);
    setSelectedName(null);
    onGroupSelect(null);
  }

  function toggleTableSort(key: ComparisonTableSortKey) {
    setTableSort((current) => {
      if (!current || current.key !== key) return { key, direction: 'asc' };
      if (current.direction === 'asc') return { key, direction: 'desc' };
      return null;
    });
  }

  return (
    <section className="bg-card rounded-lg border p-4">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-heading text-sm font-bold tracking-tight">
            {groupBy === 'site'
              ? 'Análisis comparativo de talleres aliados'
              : `Comparativo por ${labelByGroup[groupBy]}`}
          </h3>
          <p className="text-muted-foreground text-xs">
            Por defecto se muestran talleres aliados, es decir, nombres que no comienzan por
            NAVITRANS S.A.S. Cada barra está normalizada al 100% del ciclo de su{' '}
            {labelByGroup[groupBy]}.
          </p>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <label className="text-muted-foreground flex items-center gap-2 text-xs font-medium">
            Ver por
            <select
              value={groupBy}
              onChange={(event) => changeGroup(event.target.value as GroupBy)}
              className="border-input bg-background text-foreground focus:ring-ring h-9 rounded-md border px-3 text-xs font-medium shadow-sm outline-none focus:ring-2"
              aria-label="Agrupar comparativo por"
            >
              <option value="site">Taller aliado</option>
              <option value="fleet">Flota</option>
              <option value="vehicle">Vehículo</option>
            </select>
          </label>
          <label className="text-muted-foreground flex items-center gap-2 text-xs font-medium">
            Ordenar por
            <select
              value={sortBy}
              onChange={(event) => {
                setSortBy(event.target.value as ComparisonSort);
                setTableSort(null);
              }}
              className="border-input bg-background text-foreground focus:ring-ring h-9 rounded-md border px-3 text-xs font-medium shadow-sm outline-none focus:ring-2"
              aria-label="Ordenar comparativo por"
            >
              {sortOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <div className="mb-3 flex flex-wrap items-center justify-between gap-3 border-y py-3">
        <label className="text-foreground flex items-center gap-2 text-sm font-medium">
          <input
            type="checkbox"
            checked={includeNavitrans}
            onChange={(event) => changeNavitransVisibility(event.target.checked)}
            className="border-input text-accent-blue focus:ring-accent-blue h-4 w-4 rounded"
          />
          Talleres Navitrans
        </label>
        <span className="text-muted-foreground text-xs">
          {workshopOrders.length} {workshopOrders.length === 1 ? 'OT visible' : 'OTs visibles'}
        </span>
      </div>

      <div className="text-muted-foreground mb-2 hidden grid-cols-[minmax(140px,1fr)_minmax(260px,2fr)_48px_80px_72px] gap-3 px-3 text-[11px] font-medium uppercase tracking-wide sm:grid">
        <ComparisonSortHeader
          label={labelByGroup[groupBy]}
          sortKey="name"
          sort={tableSort}
          onSort={toggleTableSort}
        />
        <span>Ciclo normalizado al 100%</span>
        <ComparisonSortHeader
          label="OTs"
          sortKey="count"
          sort={tableSort}
          align="right"
          onSort={toggleTableSort}
        />
        <ComparisonSortHeader
          label="Ciclo prom."
          sortKey="average"
          sort={tableSort}
          align="right"
          onSort={toggleTableSort}
        />
        <ComparisonSortHeader
          label="Espera"
          sortKey="waitingShare"
          sort={tableSort}
          align="right"
          onSort={toggleTableSort}
        />
      </div>
      <div className="space-y-1.5">
        {groups.map((group) => (
          <button
            key={group.name}
            type="button"
            aria-pressed={selectedName === group.name}
            onClick={() => toggleGroup(group)}
            className={cn(
              'hover:bg-muted/70 grid w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm transition-all sm:grid-cols-[minmax(140px,1fr)_minmax(260px,2fr)_48px_80px_72px]',
              selectedName === group.name && 'bg-accent-blue/10 ring-accent-blue/30 ring-1',
              selectedName && selectedName !== group.name && 'opacity-40',
            )}
          >
            <div className="min-w-0">
              <p className="truncate font-medium" title={group.name}>
                {group.name}
              </p>
              <p className="text-muted-foreground text-xs">
                {group.count} {group.count === 1 ? 'OT' : 'OTs'}
              </p>
            </div>
            <CycleBar orders={group.rows} compact />
            <span className="text-right font-mono font-semibold tabular-nums">{group.count}</span>
            <span className="text-right font-mono font-semibold tabular-nums">
              {fmtHours(group.average)}
            </span>
            <span className="text-right font-mono font-semibold tabular-nums">
              {fmtPercent(group.waitingShare)}
            </span>
          </button>
        ))}
      </div>
      <div className="mt-4 border-t pt-3">
        {selectedGroup && (
          <p className="text-muted-foreground mb-2 text-xs">
            Composición seleccionada:{' '}
            <span className="text-foreground font-medium">{selectedGroup.name}</span>
          </p>
        )}
        <StageLegend orders={selectedGroup?.rows ?? workshopOrders} />
      </div>
    </section>
  );
}

function OrdersTable({
  orders,
  selectedId,
  onSelect,
}: {
  orders: WorkshopOrder[];
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  return (
    <section className="bg-card overflow-hidden rounded-lg border">
      <div className="border-b px-4 py-3">
        <h3 className="font-heading text-sm font-bold tracking-tight">
          Ciclo y cobertura de cada orden
        </h3>
        <p className="text-muted-foreground text-xs">
          La franja gris representa tiempo sin una etiqueta de etapa reconocida.
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[980px] text-sm">
          <thead>
            <tr className="text-muted-foreground border-b text-left text-xs">
              <th className="px-4 py-2 font-medium">Orden / vehículo</th>
              <th className="px-4 py-2 font-medium">Taller aliado</th>
              <th className="px-4 py-2 font-medium">Flota</th>
              <th className="w-[300px] px-4 py-2 font-medium">Composición del ciclo</th>
              <th className="px-4 py-2 text-right font-medium">Total</th>
              <th className="px-4 py-2 text-right font-medium">Espera</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((order) => {
              const total = order.totalHours;
              const selected = selectedId === order.id;
              const dimmed = selectedId !== 'all' && !selected;
              const wait = sum(
                STAGES.filter((stage) => stage.kind === 'waiting').map(
                  (stage) => order.stages[stage.key],
                ),
              );
              return (
                <tr
                  key={order.id}
                  onClick={() => onSelect(order.id)}
                  title={selected ? 'Quitar filtro de esta OT' : 'Filtrar por esta OT'}
                  className={cn(
                    'hover:bg-muted/60 cursor-pointer border-b transition-[background-color,opacity] last:border-0',
                    selected && 'bg-accent-blue/5',
                    dimmed && 'opacity-40 hover:opacity-70',
                  )}
                >
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-semibold">{order.id}</span>
                      <span className="text-muted-foreground inline-flex rounded border px-1.5 py-0.5 text-[10px] font-medium">
                        {formatOrderStatus(order.status)}
                      </span>
                      {selected && <Badge variant="info">Filtro activo</Badge>}
                    </div>
                    <span className="text-muted-foreground text-xs">
                      {order.plate} · {order.vehicle}
                    </span>
                  </td>
                  <td className="px-4 py-3">{order.site}</td>
                  <td className="px-4 py-3">{order.fleet}</td>
                  <td className="px-4 py-3">
                    <CycleBar orders={[order]} compact />
                  </td>
                  <td className="px-4 py-3 text-right font-mono font-semibold tabular-nums">
                    {fmtHours(total)}
                  </td>
                  <td className="px-4 py-3 text-right font-mono tabular-nums">
                    {fmtPercent(percent(wait, total))}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** Consulta el API de tracking y presenta la cobertura temporal de cada OT. */
export function TiemposTallerDashboard({
  chartFilters,
  activeFilters,
  selectedMonth,
  onMonthChange,
}: {
  chartFilters: MttoFilters;
  activeFilters: MttoFilters;
  selectedMonth: string | null;
  onMonthChange: (month: string | null) => void;
}) {
  const [orderQuery, setOrderQuery] = React.useState('');
  const [selectedOrderId, setSelectedOrderId] = React.useState('all');
  const [comparisonOrderIds, setComparisonOrderIds] = React.useState<Set<string> | null>(null);
  const [ordersOffset, setOrdersOffset] = React.useState(0);

  const searchOrderNumber = React.useMemo(() => extractOrderNumber(orderQuery), [orderQuery]);
  const workshop = useTiemposTaller(activeFilters);
  const chartWorkshop = useTiemposTaller(chartFilters);
  const searchWorkshop = useTiemposTaller(
    activeFilters,
    1,
    0,
    searchOrderNumber,
    searchOrderNumber !== undefined,
  );

  const orders = React.useMemo(
    () => (workshop.data?.items ?? []).map(adaptApiOrder),
    [workshop.data?.items],
  );

  const filteredOrders = React.useMemo(() => {
    return filterWorkshopOrders(orders, activeFilters);
  }, [activeFilters.date_from, activeFilters.date_to, activeFilters.placas, orders]);

  const chartOrders = React.useMemo(
    () => (chartWorkshop.data?.items ?? []).map(adaptApiOrder),
    [chartWorkshop.data?.items],
  );
  const chartFilteredOrders = React.useMemo(
    () => filterWorkshopOrders(chartOrders, chartFilters),
    [chartFilters.date_from, chartFilters.date_to, chartFilters.placas, chartOrders],
  );

  const activeComparisonOrderIds = React.useMemo(() => {
    if (!comparisonOrderIds) return null;
    const available = new Set(filteredOrders.map((order) => order.id));
    const ids = [...comparisonOrderIds].filter((id) => available.has(id));
    return ids.length > 0 ? new Set(ids) : null;
  }, [comparisonOrderIds, filteredOrders]);
  const comparisonOrders = React.useMemo(
    () =>
      activeComparisonOrderIds
        ? filteredOrders.filter((order) => activeComparisonOrderIds.has(order.id))
        : filteredOrders,
    [activeComparisonOrderIds, filteredOrders],
  );
  const searchableComparisonOrders = React.useMemo(() => {
    const merged = new Map(comparisonOrders.map((order) => [order.id, order]));
    for (const rawOrder of searchWorkshop.data?.items ?? []) {
      const order = adaptApiOrder(rawOrder);
      if (!filterWorkshopOrders([order], activeFilters).length) continue;
      if (comparisonOrderIds && !comparisonOrderIds.has(order.id)) continue;
      merged.set(order.id, order);
    }
    return [...merged.values()];
  }, [activeFilters, comparisonOrderIds, comparisonOrders, searchWorkshop.data?.items]);
  const effectiveOrderId = searchableComparisonOrders.some((order) => order.id === selectedOrderId)
    ? selectedOrderId
    : 'all';
  const selectedOrder =
    effectiveOrderId === 'all'
      ? null
      : (searchableComparisonOrders.find((order) => order.id === effectiveOrderId) ?? null);

  function handleOrderSelect(orderId: string) {
    if (orderId === 'all' || orderId === effectiveOrderId) {
      setSelectedOrderId('all');
      setOrderQuery('');
      return;
    }

    const order = searchableComparisonOrders.find((item) => item.id === orderId);
    setSelectedOrderId(orderId);
    setOrderQuery(order ? `${order.id} · ${order.plate}` : '');
  }

  const textFilteredOrders = React.useMemo(
    () =>
      selectedOrder
        ? [
            selectedOrder,
            ...searchableComparisonOrders.filter((order) => order.id !== selectedOrder.id),
          ]
        : searchableComparisonOrders.filter((order) => orderMatchesQuery(order, orderQuery)),
    [orderQuery, searchableComparisonOrders, selectedOrder],
  );
  React.useEffect(() => {
    setOrdersOffset(0);
  }, [
    activeFilters.date_from,
    activeFilters.date_to,
    activeFilters.placas,
    comparisonOrderIds,
    orderQuery,
  ]);
  const safeOrdersOffset = Math.min(
    ordersOffset,
    Math.max(0, (Math.ceil(textFilteredOrders.length / ORDERS_PAGE_SIZE) - 1) * ORDERS_PAGE_SIZE),
  );
  const pagedOrders = textFilteredOrders.slice(
    safeOrdersOffset,
    safeOrdersOffset + ORDERS_PAGE_SIZE,
  );
  const chartComparisonOrders = React.useMemo(
    () =>
      comparisonOrderIds
        ? chartFilteredOrders.filter((order) => comparisonOrderIds.has(order.id))
        : chartFilteredOrders,
    [chartFilteredOrders, comparisonOrderIds],
  );

  const scopedOrders = selectedOrder ? [selectedOrder] : searchableComparisonOrders;
  const totalHours = sum(scopedOrders.map((order) => order.totalHours));
  const loadedTotalHours = sum(comparisonOrders.map((order) => order.totalHours));
  const loadedClassifiedHours = sum(comparisonOrders.map((order) => order.classifiedHours));
  const loadedCoveragePct = percent(loadedClassifiedHours, loadedTotalHours);
  const classifiedHours = sum(scopedOrders.map((order) => order.classifiedHours));
  const unclassifiedHours = sum(scopedOrders.map((order) => order.unclassifiedHours));
  const activeHours = sum(
    STAGES.filter((stage) => stage.kind === 'active').map((stage) =>
      stageHours(scopedOrders, stage.key),
    ),
  );
  const waitingHours = sum(
    STAGES.filter((stage) => stage.kind === 'waiting').map((stage) =>
      stageHours(scopedOrders, stage.key),
    ),
  );
  const stageData = [
    ...STAGES.map((stage) => ({
      label: stage.shortLabel,
      value: stageHours(scopedOrders, stage.key),
    })),
    { label: 'Sin clasificación', value: unclassifiedHours },
  ];
  const monthlyLabels = React.useMemo(
    () =>
      new Map(
        (chartWorkshop.data?.summary.monthly ?? []).map((point) => [point.month, point.label]),
      ),
    [chartWorkshop.data?.summary.monthly],
  );
  const monthlyData = React.useMemo(() => {
    const base = buildMonthlyStageData(chartComparisonOrders, monthlyLabels);
    if (!selectedOrder) return base;

    const month = selectedOrder.openedAt.slice(0, 7);
    if (!month) return base;
    const existing = base.find((point) => point.month === month);
    const selectedPoint = {
      ...existing,
      month,
      label:
        existing?.label ??
        monthlyLabels.get(month) ??
        new Intl.DateTimeFormat('es-CO', { month: 'short', year: 'numeric' }).format(
          new Date(`${month}-01T00:00:00Z`),
        ),
      orders: 1,
      total: selectedOrder.totalHours,
      ...Object.fromEntries(STAGES.map((stage) => [stage.key, selectedOrder.stages[stage.key]])),
    };

    return [...base.filter((point) => point.month !== month), selectedPoint].sort((a, b) =>
      a.month.localeCompare(b.month),
    );
  }, [chartComparisonOrders, monthlyLabels, selectedOrder]);
  const selectedMonthLabel = monthlyData.find((point) => point.month === selectedMonth)?.label as
    | string
    | undefined;
  const selectedOrderMonthLabel = selectedOrder
    ? monthlyData.find((point) => point.month === selectedOrder.openedAt.slice(0, 7))?.label
    : undefined;
  const stageContractMismatch = React.useMemo(() => {
    const definitions = workshop.data?.stageDefinitions;
    if (!definitions) return false;
    if (definitions.length !== STAGES.length) return true;
    return STAGES.some((stage) => {
      const definition = definitions.find((item) => item.key === stage.key);
      return (
        !definition ||
        definition.label !== stage.label ||
        definition.shortLabel !== stage.shortLabel
      );
    });
  }, [workshop.data?.stageDefinitions]);

  if (stageContractMismatch) {
    return (
      <div className="bg-card text-destructive rounded-lg border p-10 text-center text-sm">
        El catálogo de etapas del API no coincide con esta versión de la interfaz. No se muestran
        métricas para evitar clasificaciones incorrectas.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <section className="border-accent-blue/20 bg-accent-blue/5 flex flex-wrap items-start justify-between gap-3 rounded-lg border p-4">
        <div className="flex gap-3">
          <div className="bg-accent-blue/10 text-accent-blue flex h-9 w-9 shrink-0 items-center justify-center rounded-md">
            <FlaskConical size={18} />
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-heading font-bold tracking-tight">
                Composición del ciclo de las órdenes
              </h2>
              <Badge variant="info">
                {workshop.isLoading
                  ? 'Cargando tracking'
                  : `${fmtPercent(loadedCoveragePct)} clasificado`}
              </Badge>
            </div>
            <p className="text-muted-foreground mt-1 max-w-3xl text-sm">
              Las etapas se calculan desde eventos reales de seguimiento. El tiempo sin una etiqueta
              equivalente se conserva como no clasificado y no se asigna artificialmente a una
              etapa.
            </p>
          </div>
        </div>
      </section>

      {workshop.data?.truncated && (
        <div
          role="status"
          className="border-accent-blue/30 bg-accent-blue/5 text-accent-blue rounded-md border px-3 py-2 text-xs"
        >
          El alcance contiene {workshop.data.total} OTs y se muestran las primeras {orders.length}.
          Reduce el rango o filtra por placa para consultar una muestra completa.
        </div>
      )}

      {workshop.isError ? (
        <div className="bg-card text-destructive rounded-lg border p-10 text-center text-sm">
          No se pudo cargar el tracking de órdenes. Intenta nuevamente en unos segundos.
        </div>
      ) : workshop.isLoading && !workshop.data ? (
        <div className="bg-card text-muted-foreground rounded-lg border p-10 text-center text-sm">
          Cargando el tracking de órdenes…
        </div>
      ) : scopedOrders.length === 0 ? (
        <div className="bg-card text-muted-foreground rounded-lg border p-10 text-center text-sm">
          No hay OTs activas para esta combinación de filtros. Las órdenes anuladas, canceladas o
          con estado Voided no se incluyen en este análisis.
        </div>
      ) : (
        <>
          <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <KpiCard
              label={selectedOrder ? 'Tiempo total de la OT' : 'Ciclo promedio por OT'}
              value={fmtHours(selectedOrder ? totalHours : totalHours / scopedOrders.length)}
              hint={
                selectedOrder
                  ? `${selectedOrder.openedAt} → ${selectedOrder.closedAt ?? 'abierta'}`
                  : `${scopedOrders.length} órdenes en el alcance`
              }
              icon={<Clock3 />}
            />
            <KpiCard
              label={selectedOrder ? 'Cobertura clasificada' : 'Horas clasificadas'}
              value={
                selectedOrder ? fmtPercent(selectedOrder.coveragePct) : fmtHours(classifiedHours)
              }
              hint={
                selectedOrder
                  ? `${fmtHours(selectedOrder.unclassifiedHours)} sin clasificación`
                  : `${fmtPercent(percent(classifiedHours, totalHours))} del ciclo total`
              }
              icon={<TimerReset />}
            />
            <KpiCard
              label="Trabajo activo"
              value={fmtPercent(percent(activeHours, totalHours))}
              hint={`${fmtHours(activeHours)} en diagnóstico, reparación y calidad`}
              icon={<Wrench />}
            />
            <KpiCard
              label="Tiempo de espera"
              value={fmtPercent(percent(waitingHours, totalHours))}
              hint={`${fmtHours(waitingHours)} por autorización o repuestos`}
              icon={<Gauge />}
            />
          </section>

          <ComposedChartCard
            title="Demora promedio mensual de las órdenes"
            subtitle={
              selectedOrder
                ? `La OT ${selectedOrder.id} se destaca en su mes; los demás meses permanecen atenuados como contexto.`
                : 'Cada barra agrupa las 10 etiquetas en 7 etapas; la línea marca el ciclo promedio total.'
            }
            data={monthlyData}
            xKey="label"
            bars={STAGES.map((stage) => ({
              key: stage.key,
              name: stage.label,
              color: stage.color,
              stackId: 'monthly-cycle',
              format: fmtHours,
            }))}
            lines={[
              {
                key: 'total',
                name: selectedOrder ? `Total OT ${selectedOrder.id}` : 'Promedio total',
                color: '#111827',
                format: fmtHours,
              },
            ]}
            action={
              <Badge variant="outline">
                {comparisonOrders.length}
                {workshop.data?.truncated ? ` de ${workshop.data.total}` : ''} OTs cargadas
              </Badge>
            }
            selectedBucket={selectedMonthLabel ?? selectedOrderMonthLabel ?? null}
            onBucketClick={(_label, payload) => {
              const month = typeof payload?.month === 'string' ? payload.month : null;
              if (month) onMonthChange(selectedMonth === month ? null : month);
            }}
            showValueLabels={false}
            height={340}
          />

          <TrackingLabelLegend />

          <section className="bg-card rounded-lg border p-4">
            <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="font-heading text-sm font-bold tracking-tight">
                  {selectedOrder
                    ? `${selectedOrder.id} · ${selectedOrder.plate}`
                    : 'Distribución consolidada del ciclo'}
                </h3>
                <p className="text-muted-foreground text-xs">
                  {selectedOrder
                    ? `${selectedOrder.fleet} · ${selectedOrder.site}`
                    : `${fmtHours(unclassifiedHours)} del ciclo no tiene una etapa clasificada.`}
                </p>
              </div>
              <Badge variant="outline">
                {fmtPercent(percent(classifiedHours, totalHours))} clasificado
              </Badge>
            </div>
            <CycleBar orders={scopedOrders} />
            <div className="mt-4">
              <StageLegend orders={scopedOrders} />
            </div>
          </section>

          <div className="grid gap-4 xl:grid-cols-2">
            <DonutChartCard
              title="Participación por etapa"
              subtitle={
                selectedOrder
                  ? 'Las 10 etiquetas se consolidan en 7 etapas dentro de la orden seleccionada'
                  : 'Las 10 etiquetas se consolidan en 7 etapas sobre las OTs filtradas'
              }
              data={stageData}
              colors={[...STAGES.map((stage) => stage.color), '#cbd5e1']}
              format={(value) => `${fmtHours(value)} · ${fmtPercent(percent(value, totalHours))}`}
              height={300}
            />
            <BarChartCard
              title="Horas por etapa"
              subtitle={
                selectedOrder
                  ? 'Tiempo agrupado por etapa dentro de la orden seleccionada'
                  : 'Horas agrupadas por etapa de las órdenes filtradas'
              }
              data={stageData}
              categoryKey="label"
              valueKey="value"
              valueName="Horas"
              format={fmtHours}
              categoryAxisWidth={92}
              color="#185979"
              height={300}
            />
          </div>

          <ComparisonCard
            orders={filteredOrders}
            onGroupSelect={(orderIds) => {
              setComparisonOrderIds(orderIds ? new Set(orderIds) : null);
              setSelectedOrderId('all');
              setOrderQuery('');
            }}
          />

          <section className="bg-card rounded-lg border p-4">
            <div className="flex flex-wrap items-end gap-3">
              <OrderSearch
                orders={searchableComparisonOrders}
                selectedId={effectiveOrderId}
                onSelect={handleOrderSelect}
                query={orderQuery}
                onQueryChange={setOrderQuery}
              />
              <div className="pb-1">
                <h3 className="font-heading text-sm font-bold tracking-tight">
                  Órdenes de trabajo
                </h3>
                <p className="text-muted-foreground text-xs">
                  {textFilteredOrders.length} OTs coinciden con los filtros y el texto.
                </p>
              </div>
            </div>
          </section>

          <OrdersTable
            orders={pagedOrders}
            selectedId={effectiveOrderId}
            onSelect={handleOrderSelect}
          />
          <UserPagination
            total={textFilteredOrders.length}
            limit={ORDERS_PAGE_SIZE}
            offset={safeOrdersOffset}
            onOffsetChange={setOrdersOffset}
            showPageSelect
          />
        </>
      )}
    </div>
  );
}
