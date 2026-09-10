'use client';

import * as React from 'react';
import Link from 'next/link';
import { Pencil, Power, RefreshCw, Search, Truck, X } from 'lucide-react';
import { toast } from 'sonner';

import { FleetFormDialog } from '@/components/fleets/fleet-form-dialog';
import { PageTitle } from '@/components/layout/page-title';
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
import { extractErrorMessage } from '@/lib/api-client';
import { useCanEdit } from '@/lib/auth';
import { useFleetFilter } from '@/components/fleet/fleet-provider';
import { useDeactivateFleet, useFleets, useSyncMasterData } from '@/lib/fleets';
import type { Fleet } from '@/lib/types';

export default function FlotasPage() {
  const { data: fleets, isLoading, isError } = useFleets();
  const deactivate = useDeactivateFleet();
  const sync = useSyncMasterData();
  const canEdit = useCanEdit()('flotas');
  const { scopeGlobal } = useFleetFilter();
  const canSync = canEdit && scopeGlobal;

  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<Fleet | null>(null);
  const [search, setSearch] = React.useState('');
  const [status, setStatus] = React.useState<'all' | 'active' | 'inactive'>('all');

  const filteredFleets = React.useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase();
    return (fleets ?? []).filter((fleet) => {
      const matchesSearch =
        !normalizedSearch ||
        fleet.name.toLowerCase().includes(normalizedSearch) ||
        fleet.code.toLowerCase().includes(normalizedSearch);
      const matchesStatus =
        status === 'all' || (status === 'active' ? fleet.is_active : !fleet.is_active);
      return matchesSearch && matchesStatus;
    });
  }, [fleets, search, status]);

  const openEdit = (fleet: Fleet) => {
    setEditing(fleet);
    setDialogOpen(true);
  };

  const onSync = async () => {
    try {
      const result = await sync.mutateAsync(true);
      toast.success(
        `Sincronización completa: ${result.fleets} flotas, ${result.databases} bases, ` +
          `${result.rules} reglas, ${result.vehicles} vehículos.`,
      );
    } catch (err) {
      toast.error(extractErrorMessage(err, 'No se pudo sincronizar con Navi Vehículos'));
    }
  };

  const onDeactivate = async (fleet: Fleet) => {
    try {
      await deactivate.mutateAsync(fleet.id);
      toast.success(`Flota "${fleet.name}" desactivada`);
    } catch (err) {
      toast.error(extractErrorMessage(err, 'No se pudo desactivar'));
    }
  };

  return (
    <section className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <PageTitle
          icon={Truck}
          title="Flotas"
          description="Gestiona las flotas y asígnalas a usuarios."
          iconClassName="bg-accent-cream text-accent-yellow"
        />
        {canSync && (
          <Button onClick={onSync} disabled={sync.isPending}>
            <RefreshCw className={sync.isPending ? 'animate-spin' : undefined} aria-hidden />
            {sync.isPending ? 'Sincronizando…' : 'Sincronizar con Navi'}
          </Button>
        )}
      </div>

      <div className="flex flex-col gap-3 md:flex-row md:items-end">
        <div className="flex-1">
          <Label htmlFor="fleet-search" className="mb-1.5 block">
            Buscar flota
          </Label>
          <div className="relative">
            <Search
              aria-hidden
              className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
            />
            <Input
              id="fleet-search"
              placeholder="Nombre o código…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              className="pl-10 pr-10"
            />
            {search && (
              <button
                type="button"
                aria-label="Limpiar búsqueda"
                onClick={() => setSearch('')}
                className="text-muted-foreground hover:text-foreground absolute right-2 top-1/2 -translate-y-1/2 rounded-sm p-1"
              >
                <X className="h-3.5 w-3.5" aria-hidden />
              </button>
            )}
          </div>
        </div>
        <div className="w-full md:w-52">
          <Label htmlFor="fleet-status" className="mb-1.5 block">
            Estado
          </Label>
          <select
            id="fleet-status"
            value={status}
            onChange={(event) => setStatus(event.target.value as typeof status)}
            className="border-input bg-background text-foreground focus-visible:ring-ring focus-visible:border-accent-blue h-10 w-full appearance-none rounded-md border px-3 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1"
          >
            <option value="all">Todas</option>
            <option value="active">Activas</option>
            <option value="inactive">Inactivas</option>
          </select>
        </div>
      </div>

      <div className="rounded-md border bg-white">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Nombre</TableHead>
              <TableHead>Código</TableHead>
              <TableHead className="text-center">Estado</TableHead>
              {canEdit && <TableHead className="text-right">Acciones</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              Array.from({ length: 3 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell colSpan={canEdit ? 4 : 3}>
                    <Skeleton className="h-6 w-full" />
                  </TableCell>
                </TableRow>
              ))
            ) : isError ? (
              <TableRow>
                <TableCell colSpan={canEdit ? 4 : 3} className="text-destructive text-center">
                  No se pudieron cargar las flotas.
                </TableCell>
              </TableRow>
            ) : filteredFleets.length > 0 ? (
              filteredFleets.map((fleet) => (
                <TableRow key={fleet.id}>
                  <TableCell className="font-semibold">
                    <Link
                      href={`/gestion/flotas/${fleet.id}`}
                      className="hover:text-accent-yellow inline-flex items-center gap-1.5 hover:underline"
                    >
                      <Truck className="h-4 w-4 shrink-0" aria-hidden />
                      {fleet.name}
                    </Link>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{fleet.code}</TableCell>
                  <TableCell className="text-center">
                    <Badge variant={fleet.is_active ? 'success' : 'outline'}>
                      {fleet.is_active ? 'Activa' : 'Inactiva'}
                    </Badge>
                  </TableCell>
                  {canEdit && (
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`Editar ${fleet.name}`}
                          onClick={() => openEdit(fleet)}
                        >
                          <Pencil aria-hidden />
                        </Button>
                        {fleet.is_active && (
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={`Desactivar ${fleet.name}`}
                            className="text-destructive"
                            onClick={() => onDeactivate(fleet)}
                          >
                            <Power aria-hidden />
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  )}
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell colSpan={canEdit ? 4 : 3} className="text-muted-foreground text-center">
                  {fleets && fleets.length > 0
                    ? 'No hay flotas que coincidan con los filtros.'
                    : 'No hay flotas registradas.'}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <FleetFormDialog open={dialogOpen} onOpenChange={setDialogOpen} initial={editing} />
    </section>
  );
}
