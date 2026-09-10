'use client';

import * as React from 'react';
import {
  CarFront,
  Check,
  ChevronsUpDown,
  Cog,
  FileBarChart,
  FileDown,
  Filter,
  Search,
  X,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { useFleetFilter } from '@/components/fleet/fleet-provider';

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { PageTitle } from '@/components/layout/page-title';
import { Skeleton } from '@/components/ui/skeleton';
import { GroupFilter, useGroupFilterAvailable } from '@/components/vehicles/group-filter';
import { cn } from '@/lib/utils';
import { expandGroupIds, useVehicleGroups } from '@/lib/vehicle-groups';
import {
  type CrossFilter,
  type ReportesFilters,
  getDailyDateBounds,
  getDefaultDailyDateRange,
  limitDailyDateRange,
  ralentiTabAvailable,
  useMotorTypes,
  useVehiculos,
} from '@/lib/reportes';
import type { FuelKind, Granularity } from '@/lib/types';

import { CalificacionTab } from './calificacion-tab';
import { CombustibleTab } from './combustible-tab';
import { FallasTab } from './fallas-tab';
import { HabitosTab } from './habitos-tab';
import { InformePersonalizadoDialog } from './informe-personalizado-dialog';
import { OperativosTab } from './operativos-tab';
import { RalentiTab } from './ralenti-tab';
import { type TabKey, useReportesTabPrefetch } from './use-tab-prefetch';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'combustible', label: 'Combustible / Rendimiento' },
  { key: 'operativos', label: 'Hábitos operativos' },
  { key: 'ralenti', label: 'Análisis Ralentí' },
  { key: 'habitos', label: 'Hábitos seguros' },
  { key: 'calificacion', label: 'Calificación' },
  { key: 'fallas', label: 'Fallas' },
];

function isTabKey(value: string | null): value is TabKey {
  return TABS.some((tab) => tab.key === value);
}

const ALL_VEHICLES_LABEL = 'Todos los vehículos';
const ALL_MOTORS_LABEL = 'Todos los tipos de motor';

function toDateInput(d: Date): string {
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, '0');
  const day = String(d.getUTCDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function firstDayOfMonth(d: Date): Date {
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1));
}

function today(): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/Bogota',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function threeMonthsAgo(referenceDate = today()): string {
  const d = new Date(`${referenceDate}T00:00:00Z`);
  d.setUTCMonth(d.getUTCMonth() - 3);
  return toDateInput(firstDayOfMonth(d));
}

/**
 * Rango que Calificación impone al entrar: el puntaje se calcula solo por mes.
 *
 * Vive en una función porque la usan DOS caminos que tienen que coincidir:
 * `selectTab`, que lo aplica al abrir la pestaña, y los filtros con los que se
 * PRECARGA esa misma pestaña desde otra. Si divergieran, la precarga guardaría
 * la respuesta bajo una clave que el montaje nunca va a pedir.
 * Además fija `today()` una sola vez: llamarlo dos veces podía caer a distintos
 * lados de la medianoche.
 */
function calificacionDateRange(): { dateFrom: string; dateTo: string } {
  const current = today();
  return { dateFrom: threeMonthsAgo(current), dateTo: current };
}

/** Chip de un cross-filter activo (clic en gráfico); la X lo limpia. */
function CrossChip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <span className="border-accent-blue/30 bg-accent-blue/10 text-accent-blue inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-semibold">
      {label}
      <button
        type="button"
        onClick={onClear}
        aria-label={`Quitar filtro ${label}`}
        className="hover:bg-accent-blue/15 -mr-1 rounded-full p-0.5 transition"
      >
        <X className="h-3 w-3" aria-hidden />
      </button>
    </span>
  );
}

export function ReportesClient() {
  const [tab, setTab] = React.useState<TabKey>('combustible');
  const [cross, setCross] = React.useState<CrossFilter>({});
  const [vehicleIds, setVehicleIds] = React.useState<string[]>([]);
  const [motorTypes, setMotorTypes] = React.useState<string[]>([]);
  const [groupIds, setGroupIds] = React.useState<string[]>([]);
  // Las fechas dependen de la zona horaria del producto. No se calculan en el
  // primer render: el servidor y el navegador podrían estar en días distintos
  // y producir HTML de hidratación diferente.
  const [datesReady, setDatesReady] = React.useState(false);
  const [reportToday, setReportToday] = React.useState('');
  const [dateFrom, setDateFrom] = React.useState('');
  const [dateTo, setDateTo] = React.useState('');
  const [open, setOpen] = React.useState(false);
  const [vehicleSearch, setVehicleSearch] = React.useState('');
  const [motorOpen, setMotorOpen] = React.useState(false);
  const [motorSearch, setMotorSearch] = React.useState('');
  const [informeOpen, setInformeOpen] = React.useState(false);
  const [granularity, setGranularity] = React.useState<Granularity>('monthly');
  const [fuelKind, setFuelKind] = React.useState<FuelKind>('liquid');
  const currentDate = reportToday;

  // "Análisis Ralentí" sólo existe si TODAS las flotas efectivas del alcance
  // tienen el módulo contratado. `[]` en el selector significa "todas las
  // visibles", igual que lo materializa el header `X-Fleet-Id`.
  const { fleets, selectedFleetIds, currentFleets } = useFleetFilter();
  const ralentiAvailable = ralentiTabAvailable(
    selectedFleetIds.length > 0 ? currentFleets : fleets,
  );
  const visibleTabs = React.useMemo(
    () => (ralentiAvailable ? TABS : TABS.filter((t) => t.key !== 'ralenti')),
    [ralentiAvailable],
  );
  const visibleTabKeys = React.useMemo(() => visibleTabs.map((t) => t.key), [visibleTabs]);

  React.useEffect(() => {
    const current = today();
    setReportToday(current);
    setDateFrom(threeMonthsAgo(current));
    setDateTo(current);
    setDatesReady(true);
  }, []);

  React.useEffect(() => {
    const syncTabFromUrl = () => {
      const value = new URLSearchParams(window.location.search).get('tab');
      if (isTabKey(value)) setTab(value);
    };
    syncTabFromUrl();
    window.addEventListener('popstate', syncTabFromUrl);
    return () => window.removeEventListener('popstate', syncTabFromUrl);
  }, []);

  // Si el alcance deja de ofrecer la pestaña (cambio de flota, o `?tab=ralenti`
  // en una URL compartida), se vuelve a la pestaña inicial en vez de dejar una
  // pantalla que consulta un módulo que la flota no tiene. Sólo cuando ya se
  // conocen las flotas: antes de `/me` la lista está vacía y un enlace directo
  // a la pestaña se perdería sin motivo.
  React.useEffect(() => {
    if (tab === 'ralenti' && fleets.length > 0 && !ralentiAvailable) setTab('combustible');
  }, [tab, fleets.length, ralentiAvailable]);

  const dailyBounds = getDailyDateBounds(currentDate);
  const dailyDateToMax = limitDailyDateRange(
    dateFrom,
    dailyBounds.maxDate,
    currentDate,
    'from',
  ).dateTo;

  // El cross-filter (clic en gráfico) se limpia al cambiar de pestaña o los
  // filtros globales: su contexto (placa/fecha) deja de ser válido.
  React.useEffect(() => {
    setCross({});
  }, [tab, vehicleIds, motorTypes, groupIds, dateFrom, dateTo]);

  const { data: vehiculos } = useVehiculos(datesReady);
  const { data: motorCatalog } = useMotorTypes(datesReady);
  const { data: groupCatalog } = useVehicleGroups(datesReady);
  const groupFilterAvailable = useGroupFilterAvailable();

  // El filtro de grupo se traduce a vehículos en el cliente y reutiliza el
  // filtro `vehicle_id`, que el backend ya scopea por flota: los 22 endpoints
  // de reportes no necesitan conocer los grupos. Filtrar por una categoría
  // incluye sus subcategorías (expansión del árbol). Con más de una flota en
  // alcance el filtro no está disponible y la traducción se apaga (GroupFilter
  // limpia la selección; este gate evita la consulta del render transitorio).
  const groupVehicleIdSet = React.useMemo(() => {
    if (!groupFilterAvailable || groupIds.length === 0) return null;
    const expanded = new Set(expandGroupIds(groupCatalog ?? [], groupIds));
    return new Set(
      (vehiculos ?? [])
        .filter((v) => v.vehicle_group_id != null && expanded.has(v.vehicle_group_id))
        .map((v) => v.vehicle_id),
    );
  }, [groupFilterAvailable, groupIds, groupCatalog, vehiculos]);

  const fuelScopeVehicles = React.useMemo(() => {
    let list = vehiculos ?? [];
    if (groupVehicleIdSet) {
      list = list.filter((vehicle) => groupVehicleIdSet.has(vehicle.vehicle_id));
    }
    if (vehicleIds.length > 0) {
      const selected = new Set(vehicleIds);
      list = list.filter((vehicle) => selected.has(vehicle.vehicle_id));
    }
    if (motorTypes.length > 0) {
      const selected = new Set(motorTypes);
      list = list.filter(
        (vehicle) => vehicle.motor_type != null && selected.has(vehicle.motor_type),
      );
    }
    return list;
  }, [vehiculos, groupVehicleIdSet, vehicleIds, motorTypes]);
  const hasGas = fuelScopeVehicles.some((vehicle) => vehicle.fuel_kind === 'gas');
  const hasLiquid = fuelScopeVehicles.some((vehicle) => vehicle.fuel_kind !== 'gas');

  // Una selección homogénea elige automáticamente su unidad. En una flota
  // mixta se conserva la elección explícita del selector de Combustible.
  React.useEffect(() => {
    if (hasGas && !hasLiquid) setFuelKind('gas');
    if (hasLiquid && !hasGas) setFuelKind('liquid');
  }, [hasGas, hasLiquid]);

  const normalizedSearch = vehicleSearch.trim().toLowerCase();
  const filteredVehiculos = React.useMemo(() => {
    if (!normalizedSearch) return vehiculos ?? [];
    return (vehiculos ?? []).filter(
      (v) =>
        (v.vehicle_label ?? '').toLowerCase().includes(normalizedSearch) ||
        (v.vehicle_id ?? '').toLowerCase().includes(normalizedSearch),
    );
  }, [vehiculos, normalizedSearch]);

  const normalizedMotorSearch = motorSearch.trim().toLowerCase();
  const filteredMotors = React.useMemo(() => {
    const list = motorCatalog ?? [];
    if (!normalizedMotorSearch) return list;
    return list.filter((m) => m.motor_type.toLowerCase().includes(normalizedMotorSearch));
  }, [motorCatalog, normalizedMotorSearch]);

  const count = vehicleIds.length;
  const activeLabel =
    count === 0
      ? ALL_VEHICLES_LABEL
      : count === 1
        ? (vehiculos?.find((v) => v.vehicle_id === vehicleIds[0])?.vehicle_label ?? vehicleIds[0])
        : `${count} vehículos seleccionados`;

  const motorCount = motorTypes.length;
  const motorLabel =
    motorCount === 0
      ? ALL_MOTORS_LABEL
      : motorCount === 1
        ? motorTypes[0]
        : `${motorCount} motores seleccionados`;

  // Memoizar para que las queries hijas no se invaliden en cada render.
  const filters: ReportesFilters = React.useMemo(() => {
    const effectiveRange =
      granularity === 'daily'
        ? limitDailyDateRange(dateFrom, dateTo, currentDate)
        : { dateFrom, dateTo };

    // El grupo se materializa como lista de vehicle_id (ver groupVehicleIdSet).
    // Un grupo sin vehículos debe dar resultados vacíos, no "sin filtro": el
    // centinela no matchea ningún vehículo en el backend.
    let effectiveVehicleIds = vehicleIds;
    if (groupVehicleIdSet) {
      effectiveVehicleIds =
        vehicleIds.length > 0
          ? vehicleIds.filter((id) => groupVehicleIdSet.has(id))
          : [...groupVehicleIdSet].sort();
      if (effectiveVehicleIds.length === 0) {
        effectiveVehicleIds = ['__grupo_sin_vehiculos__'];
      }
    }

    return {
      vehicle_id: effectiveVehicleIds.length > 0 ? effectiveVehicleIds : undefined,
      motor_type: motorTypes.length > 0 ? motorTypes : undefined,
      date_from: effectiveRange.dateFrom || undefined,
      date_to: effectiveRange.dateTo || undefined,
    };
  }, [vehicleIds, groupVehicleIdSet, motorTypes, dateFrom, dateTo, granularity, currentDate]);

  // Filtros exactos con los que montará Calificación si el usuario la abre:
  // `selectTab` le reescribe el rango, así que precargarla con los filtros
  // vigentes daría una clave que su montaje nunca pediría.
  const calificacionFilters: ReportesFilters = React.useMemo(() => {
    const range = calificacionDateRange();
    return {
      ...filters,
      date_from: range.dateFrom || undefined,
      date_to: range.dateTo || undefined,
    };
  }, [filters]);

  const { tabPrefetchHandlers } = useReportesTabPrefetch({
    activeTab: tab,
    filters,
    granularity,
    fuelKind,
    calificacionFilters,
    availableTabs: visibleTabKeys,
  });

  function toggleVehicle(id: string) {
    setVehicleIds((prev) => (prev.includes(id) ? prev.filter((vid) => vid !== id) : [...prev, id]));
  }

  function toggleMotor(motor: string) {
    setMotorTypes((prev) =>
      prev.includes(motor) ? prev.filter((m) => m !== motor) : [...prev, motor],
    );
  }

  function changeDateFrom(nextDateFrom: string) {
    if (granularity !== 'daily' || !nextDateFrom || !dateTo) {
      setDateFrom(nextDateFrom);
      return;
    }

    const dailyRange = limitDailyDateRange(nextDateFrom, dateTo, currentDate, 'from');
    setDateFrom(dailyRange.dateFrom);
    setDateTo(dailyRange.dateTo);
  }

  function changeDateTo(nextDateTo: string) {
    if (granularity !== 'daily' || !dateFrom || !nextDateTo) {
      setDateTo(nextDateTo);
      return;
    }

    const dailyRange = limitDailyDateRange(dateFrom, nextDateTo, currentDate);
    setDateFrom(dailyRange.dateFrom);
    setDateTo(dailyRange.dateTo);
  }

  function changeGranularity(nextGranularity: Granularity) {
    setGranularity(nextGranularity);

    if (nextGranularity === 'daily') {
      const dailyRange = getDefaultDailyDateRange(currentDate);
      setDateFrom(dailyRange.dateFrom);
      setDateTo(dailyRange.dateTo);
    } else if (vehicleIds.length === 0) {
      setDateFrom(threeMonthsAgo());
    }
  }

  function selectTab(nextTab: TabKey) {
    setTab(nextTab);
    const url = new URL(window.location.href);
    url.searchParams.set('tab', nextTab);
    window.history.pushState({}, '', url);
    if (nextTab === 'calificacion') {
      // La calificación y sus evoluciones se calculan únicamente por mes.
      const range = calificacionDateRange();
      setGranularity('monthly');
      setDateFrom(range.dateFrom);
      setDateTo(range.dateTo);
    }
  }

  if (!datesReady) {
    return (
      <section aria-busy="true" aria-label="Cargando reportes">
        <header className="mb-6 flex items-center gap-3">
          <PageTitle
            icon={FileBarChart}
            title="Reportes"
            description="Indicadores analíticos de la flota."
          />
        </header>
        <div className="space-y-4">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-2/3" />
          <Skeleton className="h-64 w-full" />
        </div>
      </section>
    );
  }

  return (
    <section>
      <header className="mb-6 flex items-center gap-3">
        <PageTitle
          icon={FileBarChart}
          title="Reportes"
          description="Indicadores analíticos de la flota."
        />
      </header>

      <InformePersonalizadoDialog open={informeOpen} onOpenChange={setInformeOpen} />

      {/* Filtros globales: alimentan todas las pestañas */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <DropdownMenu
          open={open}
          onOpenChange={(nextOpen) => {
            setOpen(nextOpen);
            if (!nextOpen) setVehicleSearch('');
          }}
        >
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="border-border hover:bg-muted focus-visible:ring-ring flex w-56 items-center gap-2 rounded-md border bg-white px-3 py-1.5 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2"
            >
              <CarFront className="text-muted-foreground h-4 w-4 shrink-0" aria-hidden />
              <span className="flex-1 truncate text-left">{activeLabel}</span>
              <ChevronsUpDown className="text-muted-foreground h-4 w-4 shrink-0" aria-hidden />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-56 overflow-hidden">
            <DropdownMenuLabel>Filtrar por vehículo</DropdownMenuLabel>
            <div className="relative px-1 pb-1">
              <Search
                className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
                aria-hidden
              />
              <Input
                value={vehicleSearch}
                onChange={(event) => setVehicleSearch(event.target.value)}
                onKeyDown={(event) => event.stopPropagation()}
                placeholder="Buscar vehículo"
                className="h-9 pl-8"
                aria-label="Buscar vehículo"
              />
            </div>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => setVehicleIds([])}>
              <Check
                className={cn('h-4 w-4', count === 0 ? 'opacity-100' : 'opacity-0')}
                aria-hidden
              />
              <span>{ALL_VEHICLES_LABEL}</span>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <div className="max-h-[min(240px,var(--radix-dropdown-menu-content-available-height))] overflow-y-auto">
              {filteredVehiculos.length > 0 ? (
                filteredVehiculos.map((v) => (
                  <DropdownMenuItem
                    key={v.vehicle_id}
                    onSelect={(e) => {
                      e.preventDefault();
                      toggleVehicle(v.vehicle_id);
                    }}
                  >
                    <Check
                      className={cn(
                        'h-4 w-4',
                        vehicleIds.includes(v.vehicle_id) ? 'opacity-100' : 'opacity-0',
                      )}
                      aria-hidden
                    />
                    <span className="truncate">{v.vehicle_label ?? v.vehicle_id}</span>
                  </DropdownMenuItem>
                ))
              ) : (
                <p className="text-muted-foreground px-2 py-3 text-sm">
                  No hay vehículos que coincidan.
                </p>
              )}
            </div>
          </DropdownMenuContent>
        </DropdownMenu>

        <DropdownMenu
          open={motorOpen}
          onOpenChange={(nextOpen) => {
            setMotorOpen(nextOpen);
            if (!nextOpen) setMotorSearch('');
          }}
        >
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="border-border hover:bg-muted focus-visible:ring-ring flex w-56 items-center gap-2 rounded-md border bg-white px-3 py-1.5 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2"
            >
              <Cog className="text-muted-foreground h-4 w-4 shrink-0" aria-hidden />
              <span className="flex-1 truncate text-left">{motorLabel}</span>
              <ChevronsUpDown className="text-muted-foreground h-4 w-4 shrink-0" aria-hidden />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-56 overflow-hidden">
            <DropdownMenuLabel>Filtrar por tipo de motor</DropdownMenuLabel>
            <div className="relative px-1 pb-1">
              <Search
                className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
                aria-hidden
              />
              <Input
                value={motorSearch}
                onChange={(event) => setMotorSearch(event.target.value)}
                onKeyDown={(event) => event.stopPropagation()}
                placeholder="Buscar motor"
                className="h-9 pl-8"
                aria-label="Buscar tipo de motor"
              />
            </div>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => setMotorTypes([])}>
              <Check
                className={cn('h-4 w-4', motorCount === 0 ? 'opacity-100' : 'opacity-0')}
                aria-hidden
              />
              <span>{ALL_MOTORS_LABEL}</span>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <div className="max-h-[min(240px,var(--radix-dropdown-menu-content-available-height))] overflow-y-auto">
              {filteredMotors.length > 0 ? (
                filteredMotors.map((m) => (
                  <DropdownMenuItem
                    key={m.motor_type}
                    onSelect={(e) => {
                      e.preventDefault();
                      toggleMotor(m.motor_type);
                    }}
                  >
                    <Check
                      className={cn(
                        'h-4 w-4',
                        motorTypes.includes(m.motor_type) ? 'opacity-100' : 'opacity-0',
                      )}
                      aria-hidden
                    />
                    <span className="truncate">
                      {m.motor_type}
                      <span className="text-muted-foreground ml-2 text-xs">{m.n_vehiculos}</span>
                    </span>
                  </DropdownMenuItem>
                ))
              ) : (
                <p className="text-muted-foreground px-2 py-3 text-sm">
                  No hay motores que coincidan.
                </p>
              )}
            </div>
          </DropdownMenuContent>
        </DropdownMenu>

        <GroupFilter selected={groupIds} onChange={setGroupIds} />

        <Input
          type="date"
          className="h-9 w-40"
          value={dateFrom}
          min={granularity === 'daily' ? dailyBounds.minDate : undefined}
          max={granularity === 'daily' ? dailyBounds.maxDate : undefined}
          onChange={(e) => changeDateFrom(e.target.value)}
          aria-label="Fecha desde"
        />
        <Input
          type="date"
          className="h-9 w-40"
          value={dateTo}
          min={granularity === 'daily' ? dateFrom : undefined}
          max={granularity === 'daily' ? dailyDateToMax : undefined}
          onChange={(e) => changeDateTo(e.target.value)}
          aria-label="Fecha hasta"
        />

        <div className="bg-muted inline-flex h-9 items-center rounded-md p-0.5">
          {(['daily', 'monthly'] as Granularity[]).map((g) => (
            <button
              key={g}
              type="button"
              onClick={() => changeGranularity(g)}
              disabled={tab === 'calificacion' && g === 'daily'}
              className={`rounded px-3 py-1 text-xs font-medium transition ${
                granularity === g ? 'bg-card shadow-sm' : 'text-muted-foreground'
              } ${tab === 'calificacion' && g === 'daily' ? 'cursor-not-allowed opacity-50' : ''}`}
              title={
                tab === 'calificacion' && g === 'daily'
                  ? 'Calificación disponible solo por mes'
                  : undefined
              }
            >
              {g === 'daily' ? 'Diaria' : 'Mensual'}
            </button>
          ))}
        </div>

        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-9 gap-1.5 px-3 text-xs font-medium"
          onClick={() => setInformeOpen(true)}
        >
          <FileDown className="h-4 w-4" aria-hidden />
          <span>Informe personalizado</span>
        </Button>

        {/* Cross-filter activo (clic en gráficos) */}
        {(cross.vehicleId || cross.bucket || cross.category) && (
          <div
            role="status"
            className="border-accent-blue/30 bg-accent-blue/5 ml-auto flex min-h-9 flex-wrap items-center gap-2 rounded-md border px-3 py-1"
          >
            <Filter className="text-accent-blue h-4 w-4" aria-hidden />
            <span className="text-foreground text-xs font-semibold">Filtro activo:</span>
            {cross.vehicleId && (
              <CrossChip
                label={`Placa: ${cross.vehicleLabel ?? cross.vehicleId}`}
                onClear={() =>
                  setCross((c) => ({ ...c, vehicleId: undefined, vehicleLabel: undefined }))
                }
              />
            )}
            {cross.bucket && (
              <CrossChip
                label={`Fecha: ${cross.bucket.label}`}
                onClear={() => setCross((c) => ({ ...c, bucket: undefined }))}
              />
            )}
            {cross.category && (
              <CrossChip
                label={`${cross.category.key}: ${cross.category.value}`}
                onClear={() => setCross((c) => ({ ...c, category: undefined }))}
              />
            )}
            <button
              type="button"
              onClick={() => setCross({})}
              className="text-accent-blue hover:text-accent-blue/80 ml-auto text-xs font-semibold underline-offset-2 hover:underline"
            >
              Limpiar filtro
            </button>
          </div>
        )}
      </div>

      {/* Pestañas */}
      <div className="border-border mb-6 flex gap-1 border-b">
        {visibleTabs.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => selectTab(t.key)}
            {...tabPrefetchHandlers(t.key)}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition ${
              tab === t.key
                ? 'border-brand-red text-foreground'
                : 'text-muted-foreground hover:text-foreground border-transparent'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'calificacion' && (
        <CalificacionTab filters={filters} onSelectGroup={(id) => setGroupIds([id])} />
      )}
      {tab === 'combustible' && (
        <CombustibleTab
          filters={filters}
          granularity={granularity}
          fuelKind={fuelKind}
          hasGas={hasGas}
          hasLiquid={hasLiquid}
          onFuelKindChange={setFuelKind}
          cross={cross}
          onCross={setCross}
          onSelectGroup={(id) => setGroupIds([id])}
        />
      )}
      {tab === 'operativos' && <OperativosTab filters={filters} granularity={granularity} />}
      {tab === 'ralenti' && ralentiAvailable && (
        <RalentiTab filters={filters} granularity={granularity} />
      )}
      {tab === 'habitos' && (
        <HabitosTab
          filters={filters}
          granularity={granularity}
          onSelectGroup={(id) => setGroupIds([id])}
        />
      )}
      {tab === 'fallas' && (
        <FallasTab
          filters={filters}
          granularity={granularity}
          onSelectGroup={(id) => setGroupIds([id])}
        />
      )}
    </section>
  );
}
