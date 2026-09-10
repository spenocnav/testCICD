'use client';

import { Check, ChevronDown } from 'lucide-react';
import * as React from 'react';

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { GroupFilter, useGroupFilterAvailable } from '@/components/vehicles/group-filter';
import { cn } from '@/lib/utils';
import { useMttoPlacas, type MttoFilters, type RankingItem } from '@/lib/mantenimiento';
import { expandGroupIds, useVehicleGroups } from '@/lib/vehicle-groups';

export function toDateInput(d: Date): string {
  return d.toISOString().slice(0, 10);
}

export function monthsAgo(n: number): string {
  const d = new Date();
  // Los presets representan meses calendario, incluyendo el mes en curso.
  // Así, 3M el 17 de julio comienza el 1 de mayo (no el 17 de abril).
  d.setDate(1);
  d.setMonth(d.getMonth() - Math.max(0, n - 1));
  return toDateInput(d);
}

export function today(): string {
  return toDateInput(new Date());
}

/** Restringe el rango actual al mes seleccionado en una serie temporal. */
export function rangeForMonth(month: string, filters: MttoFilters): MttoFilters {
  const [yearText, monthText] = month.split('-');
  const year = Number(yearText);
  const monthNumber = Number(monthText);
  const monthStart = `${month}-01`;
  const lastDay = new Date(year, monthNumber, 0).getDate();
  const monthEnd = `${month}-${String(lastDay).padStart(2, '0')}`;

  return {
    ...filters,
    date_from: filters.date_from && filters.date_from > monthStart ? filters.date_from : monthStart,
    date_to: filters.date_to && filters.date_to < monthEnd ? filters.date_to : monthEnd,
  };
}

export interface MttoToolbarState {
  placas: string[];
  /** Grupos internos del cliente seleccionados (nodos sin expandir). */
  groupIds: string[];
  dateFrom: string;
  dateTo: string;
}

interface MttoToolbarProps {
  state: MttoToolbarState;
  onChange: (next: MttoToolbarState) => void;
}

const PRESETS: { label: string; months: number }[] = [
  { label: '3M', months: 4 },
  { label: '6M', months: 7 },
  { label: '12M', months: 13 },
];

/** Barra de filtros del módulo: grupo + placas (multiselect) + rango de fechas + presets. */
export function MttoToolbar({ state, onChange }: MttoToolbarProps) {
  const { data: placas } = useMttoPlacas();
  const { data: groups } = useVehicleGroups();
  const [search, setSearch] = React.useState('');

  const filtered = React.useMemo(() => {
    const s = search.trim().toLowerCase();
    let rows = placas ?? [];
    // El dropdown de placas se acota a los grupos elegidos: elegir CEDI y
    // después una placa de otro CEDI sería una intersección vacía silenciosa.
    if (state.groupIds.length > 0) {
      const expanded = new Set(expandGroupIds(groups ?? [], state.groupIds));
      rows = rows.filter(
        (p) => p.vehicle_group_id != null && expanded.has(p.vehicle_group_id),
      );
    }
    if (!s) return rows.slice(0, 200);
    return rows.filter((p) => p.plate.toLowerCase().includes(s)).slice(0, 200);
  }, [placas, groups, state.groupIds, search]);

  function togglePlaca(plate: string) {
    const set = new Set(state.placas);
    if (set.has(plate)) set.delete(plate);
    else set.add(plate);
    onChange({ ...state, placas: [...set] });
  }

  const placasLabel =
    state.placas.length === 0
      ? 'Todas las placas'
      : state.placas.length === 1
        ? state.placas[0]
        : `${state.placas.length} placas`;

  return (
    <div className="flex flex-wrap items-end gap-2">
      <GroupFilter
        selected={state.groupIds}
        onChange={(groupIds) => {
          // Cambiar de grupo invalida las placas elegidas que quedan fuera.
          const expanded = new Set(expandGroupIds(groups ?? [], groupIds));
          const allowed = new Set(
            (placas ?? [])
              .filter((p) => p.vehicle_group_id != null && expanded.has(p.vehicle_group_id))
              .map((p) => p.plate),
          );
          onChange({
            ...state,
            groupIds,
            placas: groupIds.length ? state.placas.filter((p) => allowed.has(p)) : state.placas,
          });
        }}
      />
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button className="flex h-9 items-center gap-2 rounded-md border bg-white px-3 text-sm">
            <span className="truncate">{placasLabel}</span>
            <ChevronDown size={14} className="text-muted-foreground" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-56 overflow-hidden">
          <DropdownMenuLabel>Filtrar por placa</DropdownMenuLabel>
          <div className="px-2 py-1.5">
            <Input
              placeholder="Buscar placa…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="h-8"
            />
          </div>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => onChange({ ...state, placas: [] })}>
            Todas las placas
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <div className="max-h-64 overflow-y-auto">
            {filtered.map((p) => (
              <DropdownMenuItem
                key={p.plate}
                onSelect={(e) => {
                  e.preventDefault();
                  togglePlaca(p.plate);
                }}
              >
                <Check
                  size={14}
                  className={cn(
                    'mr-2',
                    state.placas.includes(p.plate) ? 'opacity-100' : 'opacity-0',
                  )}
                />
                <span className="truncate">{p.plate}</span>
                <span className="text-muted-foreground ml-auto pl-2 text-xs">{p.fleet}</span>
              </DropdownMenuItem>
            ))}
          </div>
        </DropdownMenuContent>
      </DropdownMenu>

      <label className="text-muted-foreground flex flex-col text-xs">
        Desde
        <Input
          type="date"
          value={state.dateFrom}
          max={state.dateTo}
          onChange={(e) => onChange({ ...state, dateFrom: e.target.value })}
          className="h-9 w-40"
        />
      </label>
      <label className="text-muted-foreground flex flex-col text-xs">
        Hasta
        <Input
          type="date"
          value={state.dateTo}
          min={state.dateFrom}
          onChange={(e) => onChange({ ...state, dateTo: e.target.value })}
          className="h-9 w-40"
        />
      </label>

      <div className="bg-muted inline-flex rounded-md p-0.5">
        {PRESETS.map((p) => (
          <button
            key={p.label}
            onClick={() => onChange({ ...state, dateFrom: monthsAgo(p.months), dateTo: today() })}
            className="rounded px-2.5 py-1 text-xs font-medium hover:bg-white"
          >
            {p.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export function toFilters(state: MttoToolbarState): MttoFilters {
  return {
    placas: state.placas.length ? state.placas : undefined,
    date_from: state.dateFrom,
    date_to: state.dateTo,
  };
}

/**
 * Filtros efectivos del módulo: traduce los grupos elegidos a placas (nodo +
 * descendientes) y los cruza con la selección manual. Los endpoints de
 * mantenimiento ya filtran por `placas` con alcance de flota en el backend,
 * así que el grupo no necesita viajar como parámetro propio.
 *
 * Un grupo sin placas debe dar resultados vacíos, no "sin filtro": el
 * centinela no matchea ninguna placa real.
 */
export function useMttoFilters(state: MttoToolbarState): MttoFilters {
  const { data: placas } = useMttoPlacas();
  const { data: groups } = useVehicleGroups();
  const groupFilterAvailable = useGroupFilterAvailable();

  return React.useMemo(() => {
    const base = toFilters(state);
    // Con más de una flota en alcance el filtro de grupo no está disponible y
    // la traducción se apaga (GroupFilter limpia la selección sola).
    if (!groupFilterAvailable || state.groupIds.length === 0) {
      return base;
    }
    const expanded = new Set(expandGroupIds(groups ?? [], state.groupIds));
    const groupPlates = (placas ?? [])
      .filter((p) => p.vehicle_group_id != null && expanded.has(p.vehicle_group_id))
      .map((p) => p.plate);
    const groupSet = new Set(groupPlates);
    let effective = state.placas.length
      ? state.placas.filter((plate) => groupSet.has(plate))
      : groupPlates.sort();
    if (effective.length === 0) {
      effective = ['__GRUPO-SIN-PLACAS__'];
    }
    return { ...base, placas: effective };
  }, [state, placas, groups, groupFilterAvailable]);
}

export function fmtHours(v: number | null | undefined): string {
  if (v == null) return '—';
  return `${new Intl.NumberFormat('es-CO', { maximumFractionDigits: 0 }).format(v)} h`;
}

/** El API de mantenimiento devuelve porcentajes ya en escala 0..100. */
export function fmtPct100(v: number | null | undefined): string {
  if (v == null) return '—';
  return `${v.toFixed(1)} %`;
}

interface RankingListProps {
  title: string;
  rows: RankingItem[];
  format: (v: number) => string;
  emptyText?: string;
}

/** Lista compacta título + filas nombre/valor con barra proporcional. */
export function RankingList({ title, rows, format, emptyText }: RankingListProps) {
  const max = Math.max(...rows.map((r) => r.value), 1);
  return (
    <div className="bg-card rounded-lg border p-4">
      <h3 className="font-heading mb-3 text-sm font-bold tracking-tight">{title}</h3>
      {rows.length === 0 ? (
        <p className="text-muted-foreground text-sm">{emptyText ?? 'Sin datos.'}</p>
      ) : (
        <ul className="space-y-2">
          {rows.map((r) => (
            <li key={r.name} className="flex items-center gap-2 text-sm">
              <span className="w-28 shrink-0 truncate" title={r.name}>
                {r.name}
              </span>
              <div className="bg-muted rounded-pill h-2 flex-1 overflow-hidden">
                <div
                  className="bg-accent-blue rounded-pill h-full"
                  style={{ width: `${Math.max(3, (r.value / max) * 100)}%` }}
                />
              </div>
              <span className="w-16 shrink-0 text-right font-mono tabular-nums">
                {format(r.value)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
