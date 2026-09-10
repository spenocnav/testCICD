'use client';

import * as React from 'react';
import Link from 'next/link';
import { toast } from 'sonner';
import {
  Bell,
  CheckCircle2,
  Eye,
  ImageIcon,
  Loader2,
  Plus,
  Search,
  SlidersHorizontal,
  Trash2,
  X,
} from 'lucide-react';

import { Can } from '@/components/auth/can';
import { PageTitle } from '@/components/layout/page-title';
import { NovedadCard } from '@/components/novedades/novedad-card';
import { GroupFilter, useGroupFilterAvailable } from '@/components/vehicles/group-filter';
import {
  PRIORITY_LABEL,
  formatNovedadDate,
  lifecycle,
} from '@/components/novedades/novedad-status';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
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
import { useMe } from '@/lib/auth';
import { useDeleteNovedad, useNovedades, useNovedadesPorGrupo } from '@/lib/novedades';
import { expandGroupIds, useVehicleGroups } from '@/lib/vehicle-groups';
import {
  GroupComparisonCard,
  type GroupComparisonRow,
} from '@/components/vehicles/group-comparison-card';
import { extractErrorMessage } from '@/lib/api-client';
import { cn } from '@/lib/utils';

const PAGE_SIZE = 20;

type ResolutionFilter = 'open' | 'done' | 'all';

/** Abiertas primero y por defecto: es lo que hay que atender. */
const RESOLUTION_SEGMENTS: { value: ResolutionFilter; label: string }[] = [
  { value: 'open', label: 'Abiertas' },
  { value: 'done', label: 'Resueltas' },
  { value: 'all', label: 'Todas' },
];

function toDateInputValue(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function defaultDateRange(): { from: string; to: string } {
  const to = new Date();
  const from = new Date(to.getFullYear(), to.getMonth() - 1, 1);

  return { from: toDateInputValue(from), to: toDateInputValue(to) };
}

function ResolutionSegments({
  value,
  onChange,
}: {
  value: ResolutionFilter;
  onChange: (value: ResolutionFilter) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Resolución"
      className="bg-muted inline-flex w-full rounded-md p-1 sm:w-auto"
    >
      {RESOLUTION_SEGMENTS.map((segment) => {
        const active = segment.value === value;
        return (
          <button
            key={segment.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(segment.value)}
            className={cn(
              'focus-visible:ring-ring flex-1 rounded-md px-3 py-2 text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 sm:min-h-9 sm:flex-none sm:px-4 sm:text-sm',
              'min-h-11',
              active
                ? 'text-foreground shadow-soft bg-white'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {segment.label}
          </button>
        );
      })}
    </div>
  );
}

export default function NovedadesPage() {
  const [initialDateRange] = React.useState(defaultDateRange);
  const [plate, setPlate] = React.useState('');
  const [plateFilter, setPlateFilter] = React.useState('');
  const [groupIds, setGroupIds] = React.useState<string[]>([]);
  const [dateFrom, setDateFrom] = React.useState(initialDateRange.from);
  const [dateTo, setDateTo] = React.useState(initialDateRange.to);
  const [resolution, setResolution] = React.useState<ResolutionFilter>('open');
  const [offset, setOffset] = React.useState(0);
  const [filtersOpen, setFiltersOpen] = React.useState(false);
  const { data: me } = useMe();
  const { data: groupCatalog } = useVehicleGroups();
  const groupFilterAvailable = useGroupFilterAvailable();
  const remove = useDeleteNovedad();
  const isAdmin = me?.user.roles.some((role) => role.code === 'admin') ?? false;

  React.useEffect(() => {
    const timer = window.setTimeout(() => setPlateFilter(plate.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [plate]);

  React.useEffect(() => {
    setOffset(0);
  }, [plateFilter, groupIds, dateFrom, dateTo, resolution]);

  // El backend filtra por nodo exacto; la expansión a subcategorías es del
  // cliente. Sin disponibilidad (alcance multi-flota) la traducción se apaga.
  const expandedGroupIds = React.useMemo(
    () =>
      groupFilterAvailable && groupIds.length
        ? expandGroupIds(groupCatalog ?? [], groupIds)
        : undefined,
    [groupFilterAvailable, groupIds, groupCatalog],
  );

  const { data, isLoading, isError } = useNovedades({
    plate: plateFilter || undefined,
    group_id: expandedGroupIds,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    external_done: resolution === 'all' ? undefined : resolution === 'done',
    limit: PAGE_SIZE,
    offset,
  });
  const novedades = data?.items ?? [];

  // Comparativo por grupo interno: solo consulta cuando aplica (una flota con
  // grupos). Sigue el rango de fechas pero NO el filtro de placa/resolución:
  // compara el panorama del periodo, no la página filtrada.
  const porGrupo = useNovedadesPorGrupo(
    { date_from: dateFrom || undefined, date_to: dateTo || undefined },
    groupFilterAvailable,
  );
  const groupRows: GroupComparisonRow[] = React.useMemo(
    () =>
      (porGrupo.data ?? []).map((bucket) => ({
        groupId: bucket.group_id,
        values: {
          total: bucket.total,
          abiertas: bucket.abiertas,
          resueltas: bucket.resueltas,
        },
      })),
    [porGrupo.data],
  );

  const deleteNovedad = async (id: string) => {
    if (
      !window.confirm(
        '¿Eliminar esta novedad y sus evidencias del portal? Esta acción no se puede deshacer.',
      )
    )
      return;
    try {
      await remove.mutateAsync(id);
    } catch (err) {
      toast.error(extractErrorMessage(err, 'No se pudo eliminar la novedad'));
    }
  };

  const activeFilters =
    (plateFilter ? 1 : 0) +
    (groupIds.length > 0 ? 1 : 0) +
    (dateFrom !== initialDateRange.from || dateTo !== initialDateRange.to ? 1 : 0);

  const emptyMessage =
    resolution === 'open'
      ? 'No hay novedades abiertas en este periodo.'
      : resolution === 'done'
        ? 'No hay novedades resueltas en este periodo.'
        : 'No hay novedades registradas.';

  return (
    <section className="space-y-6">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <PageTitle
          icon={Bell}
          title="Novedades"
          description="Registro de novedades de mantenimiento y evidencias."
          iconClassName="bg-accent-blue/10 text-accent-blue"
        />

        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="outline"
            className="h-11 flex-1 sm:flex-none md:hidden"
            aria-expanded={filtersOpen}
            aria-controls="novedades-filtros"
            onClick={() => setFiltersOpen((open) => !open)}
          >
            <SlidersHorizontal className="h-4 w-4" aria-hidden />
            Filtros
            {activeFilters > 0 && (
              <span className="bg-accent-blue/15 text-accent-blue rounded-pill px-2 text-xs font-semibold">
                {activeFilters}
              </span>
            )}
          </Button>
          <Can permission="novedades.edit">
            <Button asChild className="h-11 flex-1 sm:flex-none">
              <Link href="/novedades/nuevo">
                <Plus className="h-4 w-4" />
                Registrar nueva novedad
              </Link>
            </Button>
          </Can>
        </div>
      </header>

      <div
        id="novedades-filtros"
        className={cn(
          'gap-3 rounded-md border bg-white p-4 md:grid md:grid-cols-[minmax(0,1fr)_auto_180px_180px] md:items-end',
          filtersOpen ? 'grid' : 'hidden',
        )}
      >
        <div>
          <Label htmlFor="novedades-plate" className="mb-1.5 block">
            Placa
          </Label>
          <div className="relative">
            <Search
              aria-hidden
              className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
            />
            <Input
              id="novedades-plate"
              value={plate}
              onChange={(event) => setPlate(event.target.value)}
              placeholder="Buscar por placa"
              inputMode="search"
              autoCapitalize="characters"
              autoCorrect="off"
              className="pl-10 pr-11"
            />
            {plate && (
              <button
                type="button"
                aria-label="Limpiar placa"
                onClick={() => setPlate('')}
                className="text-muted-foreground hover:text-foreground absolute right-0 top-0 flex h-11 w-11 items-center justify-center rounded-sm"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>

        {groupFilterAvailable && (
          <div>
            <Label className="mb-1.5 block">Grupo</Label>
            <GroupFilter selected={groupIds} onChange={setGroupIds} />
          </div>
        )}

        {/* Las dos fechas van juntas en una fila también en el teléfono; en
            `md` el `contents` las devuelve a sus columnas de la rejilla. */}
        <div className="grid grid-cols-2 gap-3 md:contents">
          <div>
            <Label htmlFor="novedades-date-from" className="mb-1.5 block">
              Desde
            </Label>
            <div className="relative">
              <Input
                id="novedades-date-from"
                type="date"
                value={dateFrom}
                onChange={(event) => setDateFrom(event.target.value)}
                className="min-w-0 pr-2 text-xs sm:text-sm"
              />
            </div>
          </div>

          <div>
            <Label htmlFor="novedades-date-to" className="mb-1.5 block">
              Hasta
            </Label>
            <div className="relative">
              <Input
                id="novedades-date-to"
                type="date"
                value={dateTo}
                onChange={(event) => setDateTo(event.target.value)}
                className="min-w-0 pr-2 text-xs sm:text-sm"
              />
            </div>
          </div>
        </div>
      </div>

      {/* Comparativo por grupo interno del cliente (se oculta solo si no
          aplica). Sigue el rango de fechas, no el filtro de placa/resolución. */}
      <GroupComparisonCard
        title="Novedades abiertas por grupo"
        subtitle="Abiertas (incluye sin verificar) en el periodo; clic en una barra filtra la lista."
        rows={groupRows}
        derive={(t) => t.abiertas ?? 0}
        format={(value) => String(Math.round(value))}
        detail={(t) => `${Math.round(t.total ?? 0)} en total · ${Math.round(t.resueltas ?? 0)} resueltas`}
        onSelectGroup={(id) => setGroupIds([id])}
        isLoading={porGrupo.isLoading}
        emptyText="Sin novedades en el periodo para comparar por grupo."
      />

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <ResolutionSegments value={resolution} onChange={setResolution} />
        {data && (
          <p className="text-muted-foreground text-sm">
            {data.total} {data.total === 1 ? 'novedad' : 'novedades'}
          </p>
        )}
      </div>

      {/* Teléfono: tarjetas. */}
      <div className="space-y-3 md:hidden" aria-busy={isLoading}>
        {isLoading ? (
          [0, 1, 2].map((i) => <Skeleton key={i} className="h-28 rounded-md" />)
        ) : isError ? (
          <p className="text-destructive rounded-md border bg-white p-6 text-center text-sm">
            No se pudieron cargar las novedades.
          </p>
        ) : novedades.length === 0 ? (
          <p className="text-muted-foreground rounded-md border bg-white p-8 text-center text-sm">
            {emptyMessage}
          </p>
        ) : (
          novedades.map((novedad) => <NovedadCard key={novedad.id} novedad={novedad} />)
        )}
      </div>

      {/* Escritorio: tabla. */}
      <div className="hidden rounded-md border bg-white md:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Fecha</TableHead>
              <TableHead>Placa</TableHead>
              <TableHead>Resumen</TableHead>
              <TableHead>Prioridad</TableHead>
              <TableHead>Estado</TableHead>
              <TableHead>Evidencias</TableHead>
              <TableHead className="text-right">Acción</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <TableRow>
                <TableCell colSpan={7} className="text-muted-foreground h-32 text-center">
                  <span className="inline-flex items-center gap-2">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Cargando novedades...
                  </span>
                </TableCell>
              </TableRow>
            ) : isError ? (
              <TableRow>
                <TableCell colSpan={7} className="text-destructive h-32 text-center">
                  No se pudieron cargar las novedades.
                </TableCell>
              </TableRow>
            ) : novedades.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="text-muted-foreground h-32 text-center">
                  {emptyMessage}
                </TableCell>
              </TableRow>
            ) : (
              novedades.map((novedad) => {
                const state = lifecycle(novedad);
                const resolved = state.code === 'resolved';
                return (
                  <TableRow
                    key={novedad.id}
                    data-state={state.code}
                    className={cn(resolved && 'text-muted-foreground bg-muted/30')}
                  >
                    <TableCell className="font-medium">
                      {formatNovedadDate(novedad.reported_at)}
                    </TableCell>
                    <TableCell className="font-mono text-xs font-semibold">
                      {novedad.vehicle_code}
                    </TableCell>
                    <TableCell className="max-w-xs truncate">
                      {novedad.comment || 'Sin comentario'}
                    </TableCell>
                    <TableCell>{PRIORITY_LABEL[novedad.priority]}</TableCell>
                    <TableCell>
                      <Badge variant={state.variant}>
                        {resolved && <CheckCircle2 className="mr-1 h-3 w-3" aria-hidden />}
                        {state.label}
                      </Badge>
                      {state.detail && (
                        <p
                          className={cn(
                            'mt-1 text-xs',
                            resolved ? 'font-medium text-[#3F5E00]' : 'text-muted-foreground',
                          )}
                        >
                          {state.detail}
                        </p>
                      )}
                    </TableCell>
                    <TableCell>
                      <span className="text-muted-foreground inline-flex items-center gap-1 text-sm">
                        <ImageIcon className="h-4 w-4" />
                        {novedad.attachment_count}
                      </span>
                    </TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="sm" asChild>
                        <Link href={`/novedades/${novedad.id}`}>
                          <Eye className="mr-1 h-4 w-4" />
                          Ver
                        </Link>
                      </Button>
                      {isAdmin && (
                        <Button
                          variant="ghost"
                          size="icon"
                          className="text-destructive"
                          aria-label="Eliminar novedad"
                          onClick={() => void deleteNovedad(novedad.id)}
                          disabled={remove.isPending}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </div>

      {data && (
        <UserPagination
          total={data.total}
          limit={PAGE_SIZE}
          offset={offset}
          onOffsetChange={setOffset}
        />
      )}
    </section>
  );
}
