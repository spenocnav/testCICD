'use client';

import { useEffect, useMemo, useState } from 'react';
import { Check, ChevronsUpDown, FolderTree, Search } from 'lucide-react';

import { Input } from '@/components/ui/input';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useFleetFilter } from '@/components/fleet/fleet-provider';
import {
  groupFilterAvailability,
  orderGroupTree,
  useVehicleGroups,
} from '@/lib/vehicle-groups';
import { cn } from '@/lib/utils';

/**
 * ¿Puede ofrecerse el filtro de grupos con el alcance de flota actual?
 * Solo con UNA flota efectiva en alcance y con grupos activos; ver
 * `groupFilterAvailability`. Las pantallas lo usan para ocultar también sus
 * envoltorios (label, columna) y no dejar celdas muertas.
 */
export function useGroupFilterAvailable(): boolean {
  const { fleets, selectedFleetIds } = useFleetFilter();
  const groupsQuery = useVehicleGroups();
  const effectiveFleetCount =
    selectedFleetIds.length > 0 ? selectedFleetIds.length : fleets.length;
  return groupFilterAvailability(effectiveFleetCount, groupsQuery.data ?? []);
}

interface GroupFilterProps {
  selected: string[];
  onChange: (groupIds: string[]) => void;
  /** Ancho del trigger; por defecto compagina con los demás filtros. */
  className?: string;
}

/**
 * Multiselect jerárquico de grupos internos del cliente (categoría/subcategoría).
 * Se monta solo cuando el alcance tiene grupos: si el catálogo llega vacío no
 * pinta nada, así las flotas sin organización interna no ven un filtro muerto.
 *
 * La selección guarda SOLO los nodos elegidos; la expansión a descendientes la
 * hace cada pantalla con `expandGroupIds` al armar su filtro.
 */
export function GroupFilter({ selected, onChange, className }: GroupFilterProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const available = useGroupFilterAvailable();
  const groupsQuery = useVehicleGroups();
  const groups = useMemo(() => groupsQuery.data ?? [], [groupsQuery.data]);

  const ordered = useMemo(
    () => orderGroupTree(groups).filter((g) => g.is_active || selected.includes(g.id)),
    [groups, selected],
  );
  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return ordered;
    return ordered.filter((g) => g.name.toLowerCase().includes(term));
  }, [ordered, search]);

  // Si el filtro deja de estar disponible (se amplió el alcance a varias
  // flotas), la selección se limpia sola: un grupo elegido no puede seguir
  // filtrando invisible.
  useEffect(() => {
    if (!available && selected.length > 0) {
      onChange([]);
    }
  }, [available, selected, onChange]);

  if (!available || !ordered.length) {
    return null;
  }

  const count = selected.length;
  const byId = new Map(ordered.map((g) => [g.id, g]));
  const firstSelected = selected[0] as string | undefined;
  const label =
    count === 0
      ? 'Todos los grupos'
      : count === 1 && firstSelected
        ? (byId.get(firstSelected)?.name ?? 'Grupo')
        : `${count} grupos`;

  const toggle = (id: string) => {
    onChange(selected.includes(id) ? selected.filter((g) => g !== id) : [...selected, id]);
  };

  return (
    <DropdownMenu
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen);
        if (!nextOpen) setSearch('');
      }}
    >
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className={cn(
            'border-border hover:bg-muted focus-visible:ring-ring flex w-56 items-center gap-2 rounded-md border bg-white px-3 py-1.5 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2',
            className,
          )}
        >
          <FolderTree className="text-muted-foreground h-4 w-4 shrink-0" aria-hidden />
          <span className="flex-1 truncate text-left">{label}</span>
          <ChevronsUpDown className="text-muted-foreground h-4 w-4 shrink-0" aria-hidden />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-64 overflow-hidden">
        <DropdownMenuLabel>Filtrar por grupo</DropdownMenuLabel>
        <div className="relative px-1 pb-1">
          <Search
            className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
            aria-hidden
          />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            onKeyDown={(event) => event.stopPropagation()}
            placeholder="Buscar grupo"
            className="h-9 pl-8"
            aria-label="Buscar grupo"
          />
        </div>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => onChange([])}>
          <Check className={cn('h-4 w-4', count === 0 ? 'opacity-100' : 'opacity-0')} aria-hidden />
          <span>Todos los grupos</span>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <div className="max-h-[min(240px,var(--radix-dropdown-menu-content-available-height))] overflow-y-auto">
          {filtered.length > 0 ? (
            filtered.map((group) => (
              <DropdownMenuItem
                key={group.id}
                onSelect={(event) => {
                  event.preventDefault();
                  toggle(group.id);
                }}
              >
                <Check
                  className={cn(
                    'h-4 w-4',
                    selected.includes(group.id) ? 'opacity-100' : 'opacity-0',
                  )}
                  aria-hidden
                />
                <span
                  className={cn('truncate', !group.is_active && 'text-muted-foreground line-through')}
                  style={{ paddingLeft: `${group.depth * 12}px` }}
                >
                  {group.name}
                </span>
              </DropdownMenuItem>
            ))
          ) : (
            <p className="text-muted-foreground px-2 py-3 text-sm">
              No hay grupos que coincidan.
            </p>
          )}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
