'use client';

import * as React from 'react';
import Link from 'next/link';
import {
  AlertTriangle,
  CarFront,
  Check,
  ChevronsUpDown,
  ClipboardCheck,
  Clock,
  Filter,
  Radio,
  RotateCcw,
  Search,
  ShieldAlert,
  Wrench,
  X,
} from 'lucide-react';

const DEFAULT_PROTOCOLS: string[] = ['SourceJ1939Id', 'SourceObdSaId', 'SourceJ1708Id'];

const PROTOCOL_OPTIONS = [
  { value: 'SourceJ1939Id', label: 'SAE J1939', shortLabel: 'SAE J1939' },
  { value: 'SourceObdSaId', label: 'OBD-II', shortLabel: 'OBD-II' },
  { value: 'SourceJ1708Id', label: 'SAE J1708', shortLabel: 'SAE J1708' },
  { value: 'SourceGeotabGoId', label: 'Geotab GO', shortLabel: 'Geotab GO' },
] as const;

const MATCH_REVIEW_OPTIONS = [
  { value: 'direct', label: 'Match directo', shortLabel: 'Directo' },
  { value: 'ambiguous', label: 'Match ambiguo', shortLabel: 'Ambiguo' },
  { value: 'no_match', label: 'Sin match', shortLabel: 'Sin match' },
] as const;

type ManagementFilter = 'all' | NavifaultManagementState;

const MANAGEMENT_FILTER_OPTIONS: Array<{
  value: ManagementFilter;
  label: string;
  shortLabel: string;
}> = [
  { value: 'all', label: 'Todas las fallas', shortLabel: 'Todas' },
  { value: 'pending', label: 'Sin gestionar', shortLabel: 'Sin gestionar' },
  { value: 'escalada', label: 'Escaladas al taller', shortLabel: 'Escaladas' },
  {
    value: 'pendiente_registro',
    label: 'Cerradas en taller, sin desenlace',
    shortLabel: 'Sin desenlace',
  },
  { value: 'repeated', label: 'Repetidas', shortLabel: 'Repetidas' },
  { value: 'managed', label: 'Gestionadas', shortLabel: 'Gestionadas' },
];

const SEVERITY_OPTIONS = [
  {
    value: 'Nivel 1 - Urgente',
    label: 'Nivel 1 - Urgente',
    shortLabel: 'N.1 Urgente',
    colorClass: 'text-destructive',
    activeTrigger:
      'bg-destructive/15 text-destructive border-destructive/40 hover:bg-destructive/20 hover:text-destructive',
    dotClass: 'bg-destructive ring-2 ring-destructive/25',
  },
  {
    value: 'Nivel 2 - Prioritaria',
    label: 'Nivel 2 - Prioritaria',
    shortLabel: 'N.2 Prioritaria',
    colorClass: 'text-[#7A5300]',
    activeTrigger:
      'bg-accent-yellow/25 text-[#7A5300] border-accent-yellow/40 hover:bg-accent-yellow/30 hover:text-[#7A5300]',
    dotClass: 'bg-accent-yellow ring-2 ring-accent-yellow/25',
  },
  {
    value: 'Nivel 3 - Pronta',
    label: 'Nivel 3 - Pronta',
    shortLabel: 'N.3 Pronta',
    colorClass: 'text-accent-blue',
    activeTrigger:
      'bg-accent-blue/15 text-accent-blue border-accent-blue/40 hover:bg-accent-blue/20 hover:text-accent-blue',
    dotClass: 'bg-accent-blue ring-2 ring-accent-blue/25',
  },
] as const;

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { UserPagination } from '@/components/users/user-pagination';
import { useFleetFilter } from '@/components/fleet/fleet-provider';
import { useCanEdit } from '@/lib/auth';
import {
  type NavifaultManagementState,
  useNavifaultFaultManagementStates,
  useNavifaultManagementSummary,
} from '@/lib/navifault-management';
import {
  type FaultFilters,
  type FaultMatchReview,
  useFaultEvents,
  useFaultSummary,
  useVehiculos,
} from '@/lib/reportes';
import type { FaultEvent } from '@/lib/types';
import { cn } from '@/lib/utils';

import { fmtInt, KpiCard } from '../reportes/shared';
import {
  createNavifaultDateState,
  getBogotaDateString,
  rolloverNavifaultDateState,
} from './navifault-date';
import { FaultDetailDialog } from './fault-detail-dialog';

const PAGE_SIZE = 25;
const NAVIFAULT_REFETCH_INTERVAL_MS = 300_000;
const NAVIFAULT_DAY_CHECK_INTERVAL_MS = 60_000;


function formatShortDateTime(isoString: string | null | undefined): string {
  if (!isoString) return '—';
  try {
    const d = new Date(isoString);
    if (Number.isNaN(d.getTime())) return isoString;
    return new Intl.DateTimeFormat(undefined, {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(d);
  } catch {
    return isoString;
  }
}

function formatManagementDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—';
  const totalMinutes = Math.max(0, Math.round(seconds / 60));
  if (totalMinutes < 1) return '< 1 min';
  if (totalMinutes < 60) return `${totalMinutes} min`;

  const days = Math.floor(totalMinutes / (60 * 24));
  const hours = Math.floor((totalMinutes % (60 * 24)) / 60);
  const minutes = totalMinutes % 60;
  if (days > 0) return hours > 0 ? `${days} d ${hours} h` : `${days} d`;
  return minutes > 0 ? `${hours} h ${minutes} min` : `${hours} h`;
}

function severityBadge(
  sev: string | null,
  redLamp?: boolean | null,
  amberLamp?: boolean | null,
): React.ReactNode {
  const s = (sev || '').toLowerCase();
  if (s.includes('urgente') || s.includes('nivel 1') || redLamp) {
    return (
      <Badge variant="destructive" className="font-semibold">
        Urgente
      </Badge>
    );
  }
  if (s.includes('prioritaria') || s.includes('nivel 2') || amberLamp) {
    return (
      <Badge variant="warning" className="font-semibold">
        Prioritaria
      </Badge>
    );
  }
  if (s.includes('pronta') || s.includes('nivel 3')) {
    return (
      <Badge variant="info" className="font-semibold">
        Pronta
      </Badge>
    );
  }
  return <Badge variant="outline">{sev || 'Estándar'}</Badge>;
}

export function NavifaultClient() {
  const { currentFleets, scopeGlobal } = useFleetFilter();
  const canEditNavifault = useCanEdit();
  const canManageFaults = canEditNavifault('navifault');
  const { data: vehiculos = [], isLoading: vehiculosLoading } = useVehiculos();

  const [dateState, setDateState] = React.useState(createNavifaultDateState);
  const { dateFrom, dateTo } = dateState;

  const [selectedVehicle, setSelectedVehicle] = React.useState<string>('');
  const [severityFilter, setSeverityFilter] = React.useState<string[]>([]);
  const [sourceFilter, setSourceFilter] = React.useState<string[]>(DEFAULT_PROTOCOLS);
  const [matchReviewFilter, setMatchReviewFilter] = React.useState<FaultMatchReview>();
  const [managementFilter, setManagementFilter] = React.useState<ManagementFilter>('all');
  const [managementComment, setManagementComment] = React.useState<{
    fault: FaultEvent;
    note: string;
  } | null>(null);
  const [searchQuery, setSearchQuery] = React.useState<string>('');
  const [offset, setOffset] = React.useState<number>(0);
  const tablaRef = React.useRef<HTMLDivElement>(null);

  /**
   * Cambiar de página dejaba la vista abajo, sobre el pie de la tabla, y
   * había que subir a mano para leer la primera fila de la página nueva.
   *
   * Sube la TABLA a la vista, no la ventana: el shell tiene su propio
   * contenedor de desplazamiento, así que `window.scrollTo` no es fiable
   * aquí mientras que `scrollIntoView` funciona sea cual sea el elemento
   * que desplaza. Con `prefers-reduced-motion` el salto es instantáneo.
   *
   * El `scroll-mt-20` de la tarjeta no es decorativo: la barra superior es
   * `sticky top-0` y sin ese margen el salto deja la cabecera del recuadro
   * debajo de ella, aterrizando sobre los encabezados de columna.
   */
  const cambiarPagina = (nuevoOffset: number) => {
    setOffset(nuevoOffset);
    const menosMovimiento = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    tablaRef.current?.scrollIntoView({
      block: 'start',
      behavior: menosMovimiento ? 'auto' : 'smooth',
    });
  };

  const [selectedFault, setSelectedFault] = React.useState<FaultEvent | null>(null);
  // Gestionar aterriza en el registro de la orden: gestionar es confirmar
  // lo que el taller registro, y buscarlo a mano sobra.
  const [openOrderPanel, setOpenOrderPanel] = React.useState(false);
  const [detailOpen, setDetailOpen] = React.useState<boolean>(false);

  React.useEffect(() => {
    const syncBogotaDay = () => {
      const nextToday = getBogotaDateString();
      setDateState((current) => rolloverNavifaultDateState(current, nextToday));
    };
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') syncBogotaDay();
    };

    const intervalId = window.setInterval(syncBogotaDay, NAVIFAULT_DAY_CHECK_INTERVAL_MS);
    window.addEventListener('focus', syncBogotaDay);
    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      window.clearInterval(intervalId);
      window.removeEventListener('focus', syncBogotaDay);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, []);

  const toggleSeverity = (val: string) => {
    setSeverityFilter((prev) =>
      prev.includes(val) ? prev.filter((s) => s !== val) : [...prev, val],
    );
  };

  const toggleProtocol = (val: string) => {
    setSourceFilter((prev) =>
      prev.includes(val) ? prev.filter((s) => s !== val) : [...prev, val],
    );
  };

  const isAllProtocols = sourceFilter.length === PROTOCOL_OPTIONS.length;
  const isDefaultProtocols =
    sourceFilter.length === DEFAULT_PROTOCOLS.length &&
    DEFAULT_PROTOCOLS.every((p) => sourceFilter.includes(p));

  // Reset offset cuando cambian los filtros principales
  React.useEffect(() => {
    setOffset(0);
  }, [
    dateFrom,
    dateTo,
    selectedVehicle,
    severityFilter,
    sourceFilter,
    matchReviewFilter,
    managementFilter,
    searchQuery,
    currentFleets,
  ]);

  const activeFilters: FaultFilters = React.useMemo(
    () => ({
      date_from: dateFrom,
      date_to: dateTo,
      vehicle_id: selectedVehicle ? [selectedVehicle] : undefined,
      severity:
        severityFilter.length > 0
          ? severityFilter.length === 1
            ? severityFilter[0]
            : severityFilter
          : undefined,
      fault_dimension: !isAllProtocols && sourceFilter.length > 0 ? 'fuente' : undefined,
      fault_value:
        !isAllProtocols && sourceFilter.length > 0
          ? sourceFilter.length === 1
            ? sourceFilter[0]
            : sourceFilter
          : undefined,
      match_review: matchReviewFilter,
      management_state: canManageFaults && managementFilter !== 'all' ? managementFilter : undefined,
    }),
    [
      dateFrom,
      dateTo,
      selectedVehicle,
      severityFilter,
      sourceFilter,
      isAllProtocols,
      matchReviewFilter,
      canManageFaults,
      managementFilter,
    ],
  );

  const { data: summary, isLoading: summaryLoading } = useFaultSummary(
    activeFilters,
    NAVIFAULT_REFETCH_INTERVAL_MS,
  );
  const { data: eventsData, isLoading: eventsLoading } = useFaultEvents(
    activeFilters,
    false,
    PAGE_SIZE,
    offset,
    true,
    NAVIFAULT_REFETCH_INTERVAL_MS,
  );
  const managementScopeKey = React.useMemo(
    () =>
      scopeGlobal
        ? 'global'
        : currentFleets
            .map((fleet) => fleet.id)
            .sort()
            .join(','),
    [currentFleets, scopeGlobal],
  );
  const managementSummaryQuery = useNavifaultManagementSummary(
    managementScopeKey,
    canManageFaults,
  );
  const visibleFaultRowIds = React.useMemo(
    () => eventsData?.items.map((fault) => fault.row_id).filter(Boolean) ?? [],
    [eventsData?.items],
  );
  const managementStatesQuery = useNavifaultFaultManagementStates(
    visibleFaultRowIds,
    canManageFaults,
  );
  const managementInfoByRowId = React.useMemo(
    () =>
      new Map(
        (managementStatesQuery.data ?? []).map((item) => [item.fault_row_id, item]),
      ),
    [managementStatesQuery.data],
  );

  // Filtrado de búsqueda local sobre la página actual si hay texto
  const displayedItems = React.useMemo(() => {
    if (!eventsData?.items) return [];
    const items = eventsData.items;
    const q = searchQuery.trim().toLowerCase();
    if (!q) return items;

    return items.filter((item) => {
      const placa = (item.movil || '').toLowerCase();
      const diag = (item.diagnostico || '').toLowerCase();
      const code = item.codigo_diagnostico != null ? String(item.codigo_diagnostico) : '';
      const ctrl = (item.nombre_de_controlador || '').toLowerCase();
      const fmode = (item.modo_de_falla || '').toLowerCase();
      const src = (item.nombre_fuente_diagnostico || '').toLowerCase();
      return (
        placa.includes(q) ||
        diag.includes(q) ||
        code.includes(q) ||
        ctrl.includes(q) ||
        fmode.includes(q) ||
        src.includes(q)
      );
    });
  }, [eventsData?.items, searchQuery]);

  const hasActiveCustomFilters =
    Boolean(selectedVehicle) ||
    severityFilter.length > 0 ||
    !isDefaultProtocols ||
    Boolean(matchReviewFilter) ||
    (canManageFaults && managementFilter !== 'all') ||
    Boolean(searchQuery);

  const handleResetFilters = () => {
    setSelectedVehicle('');
    setSeverityFilter([]);
    setSourceFilter(DEFAULT_PROTOCOLS);
    setMatchReviewFilter(undefined);
    setManagementFilter('all');
    setSearchQuery('');
    setOffset(0);
  };

  const handleOpenDetail = (fault: FaultEvent, options?: { orderPanel?: boolean }) => {
    setSelectedFault(fault);
    setOpenOrderPanel(options?.orderPanel ?? false);
    setDetailOpen(true);
  };

  const openManagementComment = (event: React.MouseEvent, fault: FaultEvent, note: string) => {
    event.stopPropagation();
    setManagementComment({ fault, note });
  };

  const selectedVehicleLabel = React.useMemo(() => {
    if (!selectedVehicle) return 'Todos los vehículos';
    const found = vehiculos.find((v) => v.vehicle_id === selectedVehicle);
    return found
      ? `${found.vehicle_label || found.vehicle_id} (${found.motor_type || 'Motor'})`
      : selectedVehicle;
  }, [selectedVehicle, vehiculos]);

  const fleetScopeLabel = React.useMemo(() => {
    if (scopeGlobal) return null;
    if (currentFleets.length === 1) return `Flota: ${currentFleets[0]?.name || 'Activa'}`;
    if (currentFleets.length > 1) return `${currentFleets.length} flotas seleccionadas`;
    return null;
  }, [scopeGlobal, currentFleets]);

  return (
    <div className="space-y-6">
      {/* Encabezado Principal */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <PageTitle
            icon={AlertTriangle}
            title="Navifault"
            description="Monitoreo de eventos y códigos de diagnóstico (DTC) de la flota en tiempo real"
          />
        </div>
        <div className="flex items-center gap-2">
          {fleetScopeLabel && (
            <Badge variant="outline" className="text-xs">
              {fleetScopeLabel}
            </Badge>
          )}
          <Badge variant="outline" className="bg-muted/50 gap-1 text-xs font-medium">
            <Clock className="text-muted-foreground h-3 w-3" />
            Últimas 24 horas
          </Badge>
          {/* Esta pantalla sólo puede mostrar 24 horas, así que lo ya gestionado
              y lo que reincidió desaparecen de la vista. La bandeja es su
              contraparte sin ventana; el enlace vive acá y no en el menú
              lateral porque es la continuación de este módulo. */}
          {canManageFaults && (
            <Button asChild variant="outline" size="sm" className="h-9 gap-1.5 text-xs">
              <Link href="/navifault/gestion">
                <ClipboardCheck className="h-3.5 w-3.5" />
                Bandeja de gestión
              </Link>
            </Button>
          )}
        </div>
      </div>

      {/* Tarjetas KPI de Resumen Operativo */}
      <div
        className={cn(
          'grid grid-cols-1 gap-3 sm:grid-cols-2',
          canManageFaults ? 'lg:grid-cols-4 2xl:grid-cols-7' : 'lg:grid-cols-4',
        )}
      >
        {summaryLoading ? (
          Array.from({ length: canManageFaults ? 7 : 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full rounded-lg" />
          ))
        ) : (
          <>
            <KpiCard
              label="Total Fallas"
              value={fmtInt(summary?.n_fallas ?? 0)}
              hint={`${fmtInt(summary?.n_eventos ?? 0)} eventos totales`}
            />
            <KpiCard
              label="Vehículos con Alertas"
              value={fmtInt(summary?.n_vehiculos ?? 0)}
              hint="Móviles que presentaron fallas"
            />
            <KpiCard
              label="Alertas Críticas"
              value={fmtInt(summary?.n_urgentes ?? 0)}
              hint="Luz de Parada Roja activa"
            />
            <KpiCard
              label="Diagnósticos Únicos"
              value={fmtInt(summary?.n_diagnosticos ?? 0)}
              hint="Tipos de códigos detectados"
            />
            {canManageFaults && (
              <>
                <KpiCard
                  label="Fallas Gestionadas"
                  value={
                    managementSummaryQuery.isLoading
                      ? '—'
                      : fmtInt(managementSummaryQuery.data?.managed_faults ?? 0)
                  }
                  hint="Firmas únicas cerradas"
                />
                <KpiCard
                  label="Fallas Escaladas"
                  value={
                    managementSummaryQuery.isLoading
                      ? '—'
                      : fmtInt(managementSummaryQuery.data?.escalated_faults ?? 0)
                  }
                  hint="Con novedad en el taller"
                />
                <KpiCard
                  label="Fallas Repetidas"
                  value={
                    managementSummaryQuery.isLoading
                      ? '—'
                      : fmtInt(managementSummaryQuery.data?.repeated_faults ?? 0)
                  }
                  hint="Reaparecieron tras gestión"
                />
                <KpiCard
                  label="Tiempo Prom. Gestión"
                  value={
                    managementSummaryQuery.isLoading
                      ? '—'
                      : formatManagementDuration(
                          managementSummaryQuery.data?.average_management_seconds,
                        )
                  }
                  hint={
                    managementSummaryQuery.data?.management_time_sample_size
                      ? `${fmtInt(managementSummaryQuery.data.management_time_sample_size)} ciclos cerrados · últimos ${managementSummaryQuery.data.management_time_window_days} días`
                      : 'Aún no hay ciclos cerrados'
                  }
                />
              </>
            )}
          </>
        )}
      </div>

      {/* Barra de Filtros y Búsqueda */}
      <div className="bg-card flex flex-wrap items-center gap-2 rounded-lg border p-3 shadow-sm">
        {/* Buscador */}
        <div className="relative w-56 shrink-0">
          <Search className="text-muted-foreground pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2" />
          <Input
            placeholder="Buscar por placa, código o falla..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="h-9 pl-8 pr-7 text-[11px] placeholder:text-[11px]"
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => setSearchQuery('')}
              className="text-muted-foreground hover:text-foreground absolute right-2 top-1/2 -translate-y-1/2"
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </div>

        {/* Selector de Vehículo */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" className="h-9 gap-1.5 px-2.5">
              <CarFront className="text-muted-foreground h-3.5 w-3.5" />
              <span className="max-w-[130px] truncate text-xs">{selectedVehicleLabel}</span>
              <ChevronsUpDown className="text-muted-foreground h-3.5 w-3.5" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="max-h-72 w-64 overflow-y-auto">
            <DropdownMenuLabel className="text-xs">Vehículo</DropdownMenuLabel>
            <DropdownMenuItem onClick={() => setSelectedVehicle('')}>
              <Check
                className={cn('mr-2 h-4 w-4', !selectedVehicle ? 'opacity-100' : 'opacity-0')}
              />
              Todos los vehículos
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            {vehiculosLoading ? (
              <div className="text-muted-foreground p-2 text-xs">Cargando vehículos...</div>
            ) : (
              vehiculos.map((v) => (
                <DropdownMenuItem
                  key={v.vehicle_id}
                  onClick={() => setSelectedVehicle(v.vehicle_id)}
                >
                  <Check
                    className={cn(
                      'mr-2 h-4 w-4',
                      selectedVehicle === v.vehicle_id ? 'opacity-100' : 'opacity-0',
                    )}
                  />
                  <span className="font-semibold">{v.vehicle_label || v.vehicle_id}</span>
                  {v.motor_type && (
                    <span className="text-muted-foreground ml-1 text-xs">({v.motor_type})</span>
                  )}
                </DropdownMenuItem>
              ))
            )}
          </DropdownMenuContent>
        </DropdownMenu>

        {/* Selector de Nivel de Atención (Multiselección y Colores Heredados) */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              className={cn(
                'h-9 gap-1.5 px-2.5 transition-colors',
                severityFilter.length === 1 &&
                  SEVERITY_OPTIONS.find((s) => s.value === severityFilter[0])?.activeTrigger,
                severityFilter.length > 1 && 'bg-muted text-foreground border-border font-semibold',
              )}
            >
              <Filter
                className={cn(
                  'h-3.5 w-3.5',
                  severityFilter.length === 1
                    ? SEVERITY_OPTIONS.find((s) => s.value === severityFilter[0])?.colorClass
                    : 'text-muted-foreground',
                )}
              />
              <span className="text-xs">
                {severityFilter.length === 0
                  ? 'Nivel: Todos'
                  : severityFilter.length === 1
                    ? SEVERITY_OPTIONS.find((s) => s.value === severityFilter[0])?.shortLabel
                    : `Niveles (${severityFilter.length})`}
              </span>
              <ChevronsUpDown className="text-muted-foreground h-3.5 w-3.5" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-56">
            <DropdownMenuLabel className="text-xs">Nivel de Atención</DropdownMenuLabel>
            <DropdownMenuItem
              onClick={() => setSeverityFilter([])}
              className={cn(
                'cursor-pointer text-xs',
                severityFilter.length === 0 && 'bg-muted font-semibold',
              )}
            >
              Todos los niveles
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            {SEVERITY_OPTIONS.map((opt) => {
              const isSelected = severityFilter.includes(opt.value);
              return (
                <DropdownMenuItem
                  key={opt.value}
                  onSelect={(e) => {
                    e.preventDefault();
                    toggleSeverity(opt.value);
                  }}
                  className={cn(
                    'flex cursor-pointer items-center justify-between text-xs',
                    isSelected && 'bg-muted/80 font-bold',
                  )}
                >
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        'inline-flex h-2.5 w-2.5 rounded-full',
                        opt.dotClass,
                        !isSelected && 'opacity-30',
                      )}
                    />
                    <span className={opt.colorClass}>{opt.label}</span>
                  </div>
                  {isSelected && (
                    <span className="text-muted-foreground text-[10px] font-bold uppercase">
                      Activo
                    </span>
                  )}
                </DropdownMenuItem>
              );
            })}
          </DropdownMenuContent>
        </DropdownMenu>

        {/* Selector de Protocolo / Fuente (Multiselección sin colores) */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              className={cn(
                'h-9 gap-1.5 px-2.5 transition-colors',
                !isDefaultProtocols && 'bg-muted text-foreground border-border font-semibold',
              )}
            >
              <Radio className="text-muted-foreground h-3.5 w-3.5" />
              <span className="text-xs">
                {isAllProtocols
                  ? 'Protocolo: Todos'
                  : isDefaultProtocols
                    ? 'Protocolo: Estándar (3)'
                    : sourceFilter.length === 1
                      ? PROTOCOL_OPTIONS.find((p) => p.value === sourceFilter[0])?.shortLabel ||
                        '1 seleccionado'
                      : sourceFilter.length === 0
                        ? 'Sin protocolo'
                        : `Protocolos (${sourceFilter.length})`}
              </span>
              <ChevronsUpDown className="text-muted-foreground h-3.5 w-3.5" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-52">
            <DropdownMenuLabel className="text-xs">Protocolo / Fuente</DropdownMenuLabel>
            <DropdownMenuItem
              onClick={() => setSourceFilter(PROTOCOL_OPTIONS.map((p) => p.value))}
              className={cn('cursor-pointer text-xs', isAllProtocols && 'bg-muted font-semibold')}
            >
              Todos los protocolos
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            {PROTOCOL_OPTIONS.map((opt) => {
              const isSelected = sourceFilter.includes(opt.value);
              return (
                <DropdownMenuItem
                  key={opt.value}
                  onSelect={(e) => {
                    e.preventDefault();
                    toggleProtocol(opt.value);
                  }}
                  className={cn(
                    'flex cursor-pointer items-center justify-between text-xs',
                    isSelected && 'bg-muted/80 font-bold',
                  )}
                >
                  <span>{opt.label}</span>
                  {isSelected && (
                    <span className="text-muted-foreground text-[10px] font-bold uppercase">
                      Activo
                    </span>
                  )}
                </DropdownMenuItem>
              );
            })}
          </DropdownMenuContent>
        </DropdownMenu>

        {/* Revisión exacta de la llave Cummins; se filtra en servidor. */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              className={cn(
                'h-9 gap-1.5 px-2.5 transition-colors',
                matchReviewFilter && 'bg-muted text-foreground border-border font-semibold',
              )}
            >
              <Filter className="text-muted-foreground h-3.5 w-3.5" />
              <span className="text-xs">
                {matchReviewFilter
                  ? `Match: ${MATCH_REVIEW_OPTIONS.find((option) => option.value === matchReviewFilter)?.shortLabel}`
                  : 'Match: Todos'}
              </span>
              <ChevronsUpDown className="text-muted-foreground h-3.5 w-3.5" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-52">
            <DropdownMenuLabel className="text-xs">Revisión de match Cummins</DropdownMenuLabel>
            <DropdownMenuItem
              onClick={() => setMatchReviewFilter(undefined)}
              className={cn('cursor-pointer text-xs', !matchReviewFilter && 'bg-muted font-semibold')}
            >
              <Check
                className={cn(
                  'mr-2 h-4 w-4',
                  !matchReviewFilter ? 'opacity-100' : 'opacity-0',
                )}
              />
              Todos los resultados
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            {MATCH_REVIEW_OPTIONS.map((option) => (
              <DropdownMenuItem
                key={option.value}
                onClick={() => setMatchReviewFilter(option.value)}
                className={cn(
                  'cursor-pointer text-xs',
                  matchReviewFilter === option.value && 'bg-muted font-semibold',
                )}
              >
                <Check
                  className={cn(
                    'mr-2 h-4 w-4',
                    matchReviewFilter === option.value ? 'opacity-100' : 'opacity-0',
                  )}
                />
                {option.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        {canManageFaults && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                className={cn(
                  'h-9 gap-1.5 px-2.5 transition-colors',
                  managementFilter !== 'all' &&
                    'bg-muted text-foreground border-border font-semibold',
                )}
              >
                <ClipboardCheck className="text-muted-foreground h-3.5 w-3.5" />
                <span className="text-xs">
                  Gestión:{' '}
                  {MANAGEMENT_FILTER_OPTIONS.find((option) => option.value === managementFilter)
                    ?.shortLabel || 'Todas'}
                </span>
                <ChevronsUpDown className="text-muted-foreground h-3.5 w-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-60">
              <DropdownMenuLabel className="text-xs">Estado de gestión</DropdownMenuLabel>
              {MANAGEMENT_FILTER_OPTIONS.map((option) => (
                <DropdownMenuItem
                  key={option.value}
                  onClick={() => setManagementFilter(option.value)}
                  className={cn(
                    'cursor-pointer text-xs',
                    managementFilter === option.value && 'bg-muted font-semibold',
                  )}
                >
                  <Check
                    className={cn(
                      'mr-2 h-4 w-4',
                      managementFilter === option.value ? 'opacity-100' : 'opacity-0',
                    )}
                  />
                  {option.label}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        )}

        {/* Limpiar Filtros */}
        {hasActiveCustomFilters && (
          <Button
            variant="ghost"
            size="sm"
            onClick={handleResetFilters}
            className="text-muted-foreground hover:text-foreground h-9 gap-1 text-xs"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            Limpiar
          </Button>
        )}
      </div>

      {/* Tabla de Eventos de Falla */}
      <div ref={tablaRef} className="bg-card scroll-mt-20 rounded-lg border shadow-sm">
        <div className="flex items-center justify-between border-b px-4 py-3">
          <div className="flex items-center gap-2">
            <ShieldAlert className="text-brand-red h-4 w-4" />
            <h2 className="text-foreground text-sm font-bold">Registro de Eventos de Falla</h2>
          </div>
          <span className="text-muted-foreground text-xs">
            {eventsData?.total ?? 0} evento{(eventsData?.total ?? 0) === 1 ? '' : 's'} en total
          </span>
        </div>

        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="w-[120px]">Atención</TableHead>
                <TableHead className="w-[110px]">Vehículo</TableHead>
                <TableHead className="w-[100px]">Código</TableHead>
                <TableHead className="min-w-[240px]">Diagnóstico Geotab</TableHead>
                <TableHead className="min-w-[130px]">Controlador</TableHead>
                <TableHead className="w-[100px] text-center">Lámparas</TableHead>
                <TableHead className="w-[140px]">Última Ocurrencia</TableHead>
                <TableHead className="w-[80px] text-center">Ocurr.</TableHead>
                {canManageFaults && <TableHead className="w-[150px] text-center">Gestión</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {eventsLoading ? (
                Array.from({ length: 8 }).map((_, i) => (
                  <TableRow key={i}>
                    <TableCell>
                      <Skeleton className="h-5 w-16" />
                    </TableCell>
                    <TableCell>
                      <Skeleton className="h-5 w-20" />
                    </TableCell>
                    <TableCell>
                      <Skeleton className="h-5 w-14" />
                    </TableCell>
                    <TableCell>
                      <Skeleton className="h-5 w-48" />
                    </TableCell>
                    <TableCell>
                      <Skeleton className="h-5 w-24" />
                    </TableCell>
                    <TableCell>
                      <Skeleton className="mx-auto h-5 w-12" />
                    </TableCell>
                    <TableCell>
                      <Skeleton className="h-5 w-24" />
                    </TableCell>
                    <TableCell>
                      <Skeleton className="mx-auto h-5 w-8" />
                    </TableCell>
                    {canManageFaults && (
                      <TableCell>
                        <Skeleton className="mx-auto h-7 w-24" />
                      </TableCell>
                    )}
                  </TableRow>
                ))
              ) : displayedItems.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={canManageFaults ? 9 : 8}
                    className="text-muted-foreground py-12 text-center"
                  >
                    <div className="flex flex-col items-center justify-center gap-2">
                      <Wrench className="text-muted-foreground/50 h-8 w-8" />
                      <p className="text-sm font-semibold">No se encontraron eventos de falla</p>
                      <p className="text-xs">
                        {hasActiveCustomFilters
                          ? 'Prueba ajustando los filtros seleccionados.'
                          : 'No hay registros de fallas para la flota en las últimas 24 horas.'}
                      </p>
                      {hasActiveCustomFilters && (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={handleResetFilters}
                          className="mt-2 text-xs"
                        >
                          Restablecer filtros
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ) : (
                displayedItems.map((fault) => {
                  const managementInfo = managementInfoByRowId.get(fault.row_id);
                  const managementState = managementInfo?.status;
                  const managementNote = managementInfo?.last_note;
                  return (
                    <TableRow
                    key={fault.row_id}
                    onClick={() => handleOpenDetail(fault)}
                    title="Haz clic para ver la ficha técnica completa"
                    className="hover:bg-muted/60 cursor-pointer transition-colors"
                  >
                    {/* Nivel de Atención */}
                    <TableCell>
                      {severityBadge(
                        fault.tipo_de_atencion,
                        fault.luz_de_parada_roja,
                        fault.luz_de_parada_amber,
                      )}
                    </TableCell>

                    {/* Vehículo / Placa */}
                    <TableCell className="text-foreground font-bold">
                      <span>{fault.movil || '—'}</span>
                    </TableCell>

                    {/* Código de Diagnóstico + FMI (Número) */}
                    <TableCell>
                      <span className="text-foreground font-mono text-xs font-bold">
                        {fault.codigo_diagnostico != null ? fault.codigo_diagnostico : '—'}
                      </span>
                      {fault.codigo_modo_de_falla != null && (
                        <p className="text-muted-foreground mt-0.5 font-mono text-[11px]">
                          FMI {fault.codigo_modo_de_falla}
                        </p>
                      )}
                    </TableCell>

                    {/* Descripción de Diagnóstico + Descripción de FMI (Modo de Falla) */}
                    <TableCell>
                      <p className="text-foreground line-clamp-2 text-xs font-medium">
                        {fault.diagnostico || 'Diagnóstico no especificado'}
                      </p>
                      {fault.modo_de_falla && (
                        <p
                          className="text-muted-foreground mt-0.5 line-clamp-2 text-[11px]"
                          title={fault.modo_de_falla}
                        >
                          {fault.modo_de_falla}
                        </p>
                      )}
                    </TableCell>

                    {/* Controlador */}
                    <TableCell className="text-muted-foreground text-xs">
                      <span
                        className="block max-w-[140px] truncate"
                        title={fault.nombre_de_controlador || ''}
                      >
                        {fault.nombre_de_controlador || '—'}
                      </span>
                    </TableCell>

                    {/* Lámparas de Tablero */}
                    <TableCell className="text-center">
                      <div className="flex items-center justify-center gap-1">
                        {fault.luz_de_parada_roja && (
                          <span
                            title="Luz Roja de Parada (Stop)"
                            className="bg-destructive ring-destructive/25 inline-flex h-3 w-3 rounded-full ring-2"
                          />
                        )}
                        {fault.luz_de_parada_amber && (
                          <span
                            title="Luz Ámbar de Advertencia"
                            className="bg-accent-yellow ring-accent-yellow/25 inline-flex h-3 w-3 rounded-full ring-2"
                          />
                        )}
                        {fault.lampara_de_averia && (
                          <span
                            title="Lámpara MIL (Avería de Motor)"
                            className="ring-accent-yellow/25 inline-flex h-3 w-3 rounded-full bg-[#ffb301] ring-2"
                          />
                        )}
                        {!fault.luz_de_parada_roja &&
                          !fault.luz_de_parada_amber &&
                          !fault.lampara_de_averia && (
                            <span className="text-muted-foreground text-[11px]">—</span>
                          )}
                      </div>
                    </TableCell>

                    {/* Fecha y Hora */}
                    <TableCell className="text-muted-foreground whitespace-nowrap text-xs tabular-nums">
                      <div className="flex items-center gap-1.5">
                        <Clock className="h-3 w-3 shrink-0" />
                        <span>{formatShortDateTime(fault.fecha_de_falla || fault.fecha)}</span>
                      </div>
                    </TableCell>

                    {/* Recuento */}
                    <TableCell className="text-center text-xs tabular-nums">
                      <span className="bg-muted text-foreground inline-flex min-w-[28px] items-center justify-center rounded-md px-1.5 py-0.5 text-xs font-bold">
                        {fault.recuento_de_fallos ?? 1}
                      </span>
                    </TableCell>
                    {canManageFaults && (
                      <TableCell className="text-center" onClick={(event) => event.stopPropagation()}>
                        {managementStatesQuery.isLoading ? (
                          <Skeleton className="mx-auto h-7 w-24" />
                        ) : managementState === 'escalada' ? (
                          <Badge variant="outline" className="border-sky-300 bg-sky-50 text-sky-800">
                            Escalada
                          </Badge>
                        ) : managementState === 'pendiente_registro' ? (
                          // El taller cerró su orden; falta declarar el desenlace
                          // aquí. Se distingue de "Escalada", que espera al taller
                          // y no a nosotros: ésta es trabajo de un clic.
                          <Badge
                            variant="outline"
                            className="border-amber-300 bg-amber-50 text-amber-900"
                          >
                            Falta el desenlace
                          </Badge>
                        ) : managementState === 'managed' ? (
                          <div className="flex flex-col items-center gap-1.5">
                            <Badge
                              variant="outline"
                              className="border-emerald-300 bg-emerald-50 text-emerald-800"
                            >
                              Gestionada
                            </Badge>
                            {managementNote && (
                              <Button
                                size="sm"
                                variant="link"
                                className="h-auto px-0 text-xs"
                                onClick={(event) => openManagementComment(event, fault, managementNote)}
                              >
                                Ver nota
                              </Button>
                            )}
                          </div>
                        ) : managementState === 'repeated' ? (
                          <div className="flex flex-col items-center gap-1.5">
                            <Badge variant="destructive">Repetida</Badge>
                            {managementNote && (
                              <Button
                                size="sm"
                                variant="link"
                                className="h-auto px-0 text-xs"
                                onClick={(event) => openManagementComment(event, fault, managementNote)}
                              >
                                Ver nota
                              </Button>
                            )}
                            <Button
                              size="sm"
                              className="h-7 px-2 text-xs"
                              onClick={(event) => {
                                event.stopPropagation();
                                handleOpenDetail(fault, { orderPanel: true });
                              }}
                            >
                              Gestionar
                            </Button>
                          </div>
                        ) : managementState === 'pending' ? (
                          <Button
                            size="sm"
                            className="h-7 px-2 text-xs"
                            onClick={(event) => {
                              event.stopPropagation();
                              handleOpenDetail(fault, { orderPanel: true });
                            }}
                          >
                            <ClipboardCheck className="mr-1 h-3.5 w-3.5" />
                            Gestionar
                          </Button>
                        ) : (
                          <span className="text-muted-foreground text-xs">No disponible</span>
                        )}
                      </TableCell>
                    )}
                    </TableRow>
                  );
                })
              )}
            </TableBody>
          </Table>
        </div>

        {/* Paginación */}
        {eventsData && eventsData.total > 0 && (
          <div className="border-t px-4 py-3">
            <UserPagination
              total={eventsData.total}
              limit={PAGE_SIZE}
              offset={offset}
              onOffsetChange={cambiarPagina}
              showPageSelect
            />
          </div>
        )}
      </div>

      <Dialog
        open={managementComment !== null}
        onOpenChange={(open) => {
          if (!open) setManagementComment(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Nota registrada en la gestión</DialogTitle>
            <DialogDescription>
              {managementComment
                ? `${managementComment.fault.movil || 'Vehículo'} · código ${managementComment.fault.codigo_diagnostico ?? '—'} · FMI ${managementComment.fault.codigo_modo_de_falla ?? '—'}`
                : 'Nota histórica registrada durante la gestión de la falla.'}
            </DialogDescription>
          </DialogHeader>
          <p className="bg-muted whitespace-pre-wrap rounded-md p-3 text-sm leading-6">
            {managementComment?.note}
          </p>
          <DialogFooter>
            <Button onClick={() => setManagementComment(null)}>Cerrar</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Modal / Diálogo de Ficha Técnica Detallada con Timeline */}
      <FaultDetailDialog
        fault={selectedFault}
        open={detailOpen}
        onOpenChange={setDetailOpen}
        // El estado ya viene con la pagina: sin pasarlo, la ficha ofrecia
        // escalar una falla ya escalada y el error solo aparecia tras enviar.
        managementState={
          selectedFault ? managementInfoByRowId.get(selectedFault.row_id) : undefined
        }
        openOrderPanel={openOrderPanel}
      />
    </div>
  );
}
