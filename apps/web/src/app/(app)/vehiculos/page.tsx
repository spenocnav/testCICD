'use client';

import * as React from 'react';
import Link from 'next/link';
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Bell,
  CarFront,
  ChevronDown,
  Eye,
  Search,
} from 'lucide-react';

import { VehicleDetailDialog } from '@/components/fleets/vehicle-detail-dialog';
import { MotorCurvesSection } from '@/components/vehicles/motor-curves-section';
import { PageTitle } from '@/components/layout/page-title';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { UserPagination } from '@/components/users/user-pagination';
import { GroupFilter, useGroupFilterAvailable } from '@/components/vehicles/group-filter';
import { useMe } from '@/lib/auth';
import type { Vehicle } from '@/lib/types';
import { expandGroupIds, groupPath, useVehicleGroups } from '@/lib/vehicle-groups';
import {
  useAccessibleVehicles,
  useAccessibleVehiclesPaginated,
  type VehicleSortKey,
} from '@/lib/vehicles';

const PAGE_SIZE = 20;
type SortOrder = 'asc' | 'desc';

function SortableHeader({
  label,
  column,
  sortBy,
  sortOrder,
  onSort,
}: {
  label: string;
  column: VehicleSortKey;
  sortBy: VehicleSortKey | null;
  sortOrder: SortOrder;
  onSort: (column: VehicleSortKey) => void;
}) {
  const active = sortBy === column;
  return (
    <TableHead>
      <button
        type="button"
        className="hover:text-foreground inline-flex items-center gap-1 font-semibold"
        onClick={() => onSort(column)}
        aria-label={`Ordenar por ${label}`}
      >
        {active ? (
          sortOrder === 'asc' ? (
            <ArrowUp className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <ArrowDown className="h-3.5 w-3.5" aria-hidden />
          )
        ) : (
          <ArrowUpDown className="text-muted-foreground h-3.5 w-3.5" aria-hidden />
        )}
        {label}
      </button>
    </TableHead>
  );
}

function valueOrDash(value: string | null | undefined): string {
  return value && value.trim() ? value : '—';
}

export default function VehiculosPage() {
  const { data: me } = useMe();
  const [search, setSearch] = React.useState('');
  const [searchFilter, setSearchFilter] = React.useState('');
  const [selectedMarca, setSelectedMarca] = React.useState('');
  const [selectedMotor, setSelectedMotor] = React.useState('');
  const [selectedStatus, setSelectedStatus] = React.useState<'active' | 'inactive' | 'all'>('all');
  const [sortBy, setSortBy] = React.useState<VehicleSortKey | null>(null);
  const [sortOrder, setSortOrder] = React.useState<SortOrder>('asc');
  const [offset, setOffset] = React.useState(0);
  const [selectedVehicle, setSelectedVehicle] = React.useState<Vehicle | null>(null);
  const [groupIds, setGroupIds] = React.useState<string[]>([]);
  const { data: accessibleVehicles } = useAccessibleVehicles(true);
  const { data: groupCatalog } = useVehicleGroups();
  // Filtro y columna solo con UNA flota en alcance: los grupos son la
  // organización interna de un cliente y mezclarlos entre flotas confunde.
  const hasGroups = useGroupFilterAvailable();
  // El backend filtra por nodo exacto; la expansión a subcategorías es del
  // cliente. Sin disponibilidad (alcance multi-flota) la traducción se apaga.
  const expandedGroupIds = React.useMemo(
    () => (hasGroups && groupIds.length ? expandGroupIds(groupCatalog ?? [], groupIds) : undefined),
    [hasGroups, groupIds, groupCatalog],
  );
  const { data, isLoading, isError } = useAccessibleVehiclesPaginated({
    search: searchFilter || undefined,
    marca: selectedMarca || undefined,
    motor_type: selectedMotor || undefined,
    group_id: expandedGroupIds,
    is_active: selectedStatus === 'all' ? undefined : selectedStatus === 'active',
    sort_by: sortBy ?? undefined,
    sort_order: sortBy ? sortOrder : undefined,
    limit: PAGE_SIZE,
    offset,
  });

  const fleetsById = React.useMemo(() => {
    return new Map((me?.fleets ?? []).map((fleet) => [fleet.id, fleet]));
  }, [me?.fleets]);

  React.useEffect(() => {
    const timer = window.setTimeout(() => setSearchFilter(search.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  React.useEffect(() => {
    setOffset(0);
  }, [searchFilter, selectedMarca, selectedMotor, selectedStatus, groupIds]);

  const vehicles = data?.items ?? [];
  const totalVehicles = data?.total_all ?? 0;
  const activeVehiclesCount = data?.active_total ?? 0;
  const handleSort = (column: VehicleSortKey) => {
    if (sortBy !== column) {
      setSortBy(column);
      setSortOrder('asc');
    } else if (sortOrder === 'asc') {
      setSortOrder('desc');
    } else {
      setSortBy(null);
      setSortOrder('asc');
    }
  };
  const brandOptions = React.useMemo(
    () =>
      Array.from(
        new Set(
          (accessibleVehicles ?? [])
            .map((vehicle) => vehicle.marca?.trim())
            .filter((marca): marca is string => Boolean(marca)),
        ),
      ).sort((a, b) => a.localeCompare(b)),
    [accessibleVehicles],
  );
  const motorOptions = React.useMemo(
    () =>
      Array.from(
        new Set(
          (accessibleVehicles ?? [])
            .map((vehicle) => vehicle.motor_type?.trim())
            .filter((motor): motor is string => Boolean(motor)),
        ),
      ).sort((a, b) => a.localeCompare(b)),
    [accessibleVehicles],
  );

  React.useEffect(() => {
    if (selectedMarca && !brandOptions.includes(selectedMarca)) setSelectedMarca('');
  }, [brandOptions, selectedMarca]);

  React.useEffect(() => {
    if (selectedMotor && !motorOptions.includes(selectedMotor)) setSelectedMotor('');
  }, [motorOptions, selectedMotor]);

  return (
    <section className="space-y-6">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <PageTitle
          icon={CarFront}
          title="Vehículos"
          description="Placas disponibles para tus flotas asignadas."
          iconClassName="bg-accent-blue/10 text-accent-blue"
        />

        <div className="grid grid-cols-2 gap-3 sm:w-auto">
          <div className="rounded-md border bg-white px-4 py-2">
            <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
              Vehículos
            </p>
            <p className="font-heading text-xl font-extrabold">{totalVehicles}</p>
          </div>
          <div className="rounded-md border bg-white px-4 py-2">
            <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
              Activos
            </p>
            <p className="font-heading text-xl font-extrabold">{activeVehiclesCount}</p>
          </div>
        </div>
      </header>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="relative sm:max-w-sm sm:flex-1">
          <Search
            className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
            aria-hidden
          />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Buscar por placa, marca, motor o flota"
            className="pl-9"
            aria-label="Buscar vehículo"
          />
        </div>
        <div className={hasGroups ? 'grid gap-3 sm:grid-cols-4' : 'grid gap-3 sm:grid-cols-3'}>
          {hasGroups && (
            <span className="grid gap-1 text-sm font-medium">
              Grupo
              <GroupFilter selected={groupIds} onChange={setGroupIds} className="h-10 w-full" />
            </span>
          )}
          <label className="grid gap-1 text-sm font-medium">
            Marca
            <span className="relative">
              <select
                value={selectedMarca}
                onChange={(event) => setSelectedMarca(event.target.value)}
                disabled={brandOptions.length === 0}
                className="border-input bg-background h-10 w-full appearance-none rounded-md border px-3 pr-10 text-sm disabled:cursor-not-allowed disabled:opacity-70"
              >
                <option value="">Todas</option>
                {brandOptions.map((marca) => (
                  <option key={marca} value={marca}>
                    {marca}
                  </option>
                ))}
              </select>
              <ChevronDown
                className="text-muted-foreground pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2"
                aria-hidden
              />
            </span>
          </label>
          <label className="grid gap-1 text-sm font-medium">
            Motor
            <span className="relative">
              <select
                value={selectedMotor}
                onChange={(event) => setSelectedMotor(event.target.value)}
                disabled={motorOptions.length === 0}
                className="border-input bg-background h-10 w-full appearance-none rounded-md border px-3 pr-10 text-sm disabled:cursor-not-allowed disabled:opacity-70"
              >
                <option value="">Todos</option>
                {motorOptions.map((motor) => (
                  <option key={motor} value={motor}>
                    {motor}
                  </option>
                ))}
              </select>
              <ChevronDown
                className="text-muted-foreground pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2"
                aria-hidden
              />
            </span>
          </label>
          <label className="grid gap-1 text-sm font-medium">
            Estado
            <span className="relative">
              <select
                value={selectedStatus}
                onChange={(event) =>
                  setSelectedStatus(event.target.value as 'active' | 'inactive' | 'all')
                }
                className="border-input bg-background h-10 w-full appearance-none rounded-md border px-3 pr-10 text-sm"
              >
                <option value="all">Todos</option>
                <option value="active">Activo</option>
                <option value="inactive">Inactivo</option>
              </select>
              <ChevronDown
                className="text-muted-foreground pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2"
                aria-hidden
              />
            </span>
          </label>
        </div>
      </div>

      <div className="rounded-md border bg-white">
        <TooltipProvider delayDuration={150}>
          <Table>
            <TableHeader>
              <TableRow>
                <SortableHeader
                  label="Placa"
                  column="plate"
                  sortBy={sortBy}
                  sortOrder={sortOrder}
                  onSort={handleSort}
                />
                <SortableHeader
                  label="Flota"
                  column="fleet"
                  sortBy={sortBy}
                  sortOrder={sortOrder}
                  onSort={handleSort}
                />
                {hasGroups && <TableHead>Grupo</TableHead>}
                <SortableHeader
                  label="Marca"
                  column="marca"
                  sortBy={sortBy}
                  sortOrder={sortOrder}
                  onSort={handleSort}
                />
                <SortableHeader
                  label="Motor"
                  column="motor_type"
                  sortBy={sortBy}
                  sortOrder={sortOrder}
                  onSort={handleSort}
                />
                <SortableHeader
                  label="Año"
                  column="ano_modelo"
                  sortBy={sortBy}
                  sortOrder={sortOrder}
                  onSort={handleSort}
                />
                <TableHead className="w-24 text-right">Acciones</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading ? (
                Array.from({ length: 5 }).map((_, index) => (
                  <TableRow key={index}>
                    <TableCell colSpan={hasGroups ? 7 : 6}>
                      <Skeleton className="h-6 w-full" />
                    </TableCell>
                  </TableRow>
                ))
              ) : isError ? (
                <TableRow>
                  <TableCell colSpan={hasGroups ? 7 : 6} className="text-destructive text-center">
                    No se pudieron cargar los vehículos.
                  </TableCell>
                </TableRow>
              ) : vehicles.length > 0 ? (
                vehicles.map((vehicle: Vehicle) => {
                  const fleet = vehicle.fleet_id ? fleetsById.get(vehicle.fleet_id) : null;
                  return (
                    <TableRow key={vehicle.id}>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <span
                            className={`h-2.5 w-2.5 shrink-0 rounded-full ${
                              vehicle.is_active ? 'bg-emerald-500' : 'bg-red-500'
                            }`}
                            title={vehicle.is_active ? 'Activo' : 'Inactivo'}
                            aria-label={vehicle.is_active ? 'Activo' : 'Inactivo'}
                          />
                          <p className="font-mono font-semibold">{vehicle.plate}</p>
                        </div>
                        {vehicle.nombre_vehiculo && (
                          <p className="text-muted-foreground text-xs">{vehicle.nombre_vehiculo}</p>
                        )}
                      </TableCell>
                      <TableCell>
                        <p className="text-sm font-medium">
                          {vehicle.fleet_name ?? fleet?.name ?? '—'}
                        </p>
                      </TableCell>
                      {hasGroups && (
                        <TableCell className="text-muted-foreground text-sm">
                          {groupPath(groupCatalog ?? [], vehicle.vehicle_group_id) ?? '—'}
                        </TableCell>
                      )}
                      <TableCell>{valueOrDash(vehicle.marca)}</TableCell>
                      <TableCell>
                        {/* La columna es el TIPO DE MOTOR, no el modelo
                            comercial: es lo que define curvas, bandas de RPM y
                            límites de revoluciones del vehículo. El modelo
                            comercial sigue en el detalle. */}
                        <p className="font-medium">{valueOrDash(vehicle.motor_type)}</p>
                        {vehicle.cpl && (
                          <p className="text-muted-foreground font-mono text-xs">
                            CPL {vehicle.cpl}
                          </p>
                        )}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {valueOrDash(vehicle.ano_modelo)}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-1">
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                className="h-9 w-9"
                                onClick={() => setSelectedVehicle(vehicle)}
                                aria-label={`Ver detalles de ${vehicle.plate}`}
                              >
                                <Eye aria-hidden />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>Detalles del vehículo</TooltipContent>
                          </Tooltip>

                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button asChild variant="ghost" size="icon" className="h-9 w-9">
                                <Link
                                  href={{
                                    pathname: '/novedades/nuevo',
                                    query: { vehicleId: vehicle.id },
                                  }}
                                  aria-label={`Crear novedad para ${vehicle.plate}`}
                                >
                                  <Bell aria-hidden />
                                </Link>
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>Novedades</TooltipContent>
                          </Tooltip>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })
              ) : (
                <TableRow>
                  <TableCell colSpan={hasGroups ? 7 : 6} className="text-muted-foreground text-center">
                    {search ? 'No hay vehículos que coincidan.' : 'No hay vehículos disponibles.'}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TooltipProvider>
      </div>

      {data && (
        <UserPagination
          total={data.total}
          limit={PAGE_SIZE}
          offset={offset}
          onOffsetChange={setOffset}
        />
      )}

      <MotorCurvesSection />

      <VehicleDetailDialog
        vehicle={selectedVehicle}
        fleetId={selectedVehicle?.fleet_id ?? undefined}
        onOpenChange={(open) => {
          if (!open) setSelectedVehicle(null);
        }}
      />
    </section>
  );
}
