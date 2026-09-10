'use client';

import * as React from 'react';
import { Check, ChevronsUpDown, Search, Truck } from 'lucide-react';

import { useFleetFilter } from '@/components/fleet/fleet-provider';
import { Input } from '@/components/ui/input';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { cn } from '@/lib/utils';

const ALL_LABEL = 'Todas las flotas';

export function FleetFilter() {
  const { fleets, selectedFleetIds, currentFleets, toggleFleet, setFleetIds, canChoose } =
    useFleetFilter();
  const [open, setOpen] = React.useState(false);
  const [fleetSearch, setFleetSearch] = React.useState('');
  // Radix dispara `onSelect` con un CustomEvent sin el evento original, así que
  // el modificador (Ctrl/Cmd) se captura del pointer/teclado justo antes.
  const additiveRef = React.useRef(false);

  const normalizedFleetSearch = fleetSearch.trim().toLowerCase();
  const filteredFleets = React.useMemo(() => {
    if (!normalizedFleetSearch) return fleets;
    return fleets.filter((fleet) => fleet.name.toLowerCase().includes(normalizedFleetSearch));
  }, [fleets, normalizedFleetSearch]);

  // Sin flotas reales no hay ningún alcance que el usuario pueda seleccionar.
  if (fleets.length === 0) return null;

  const count = selectedFleetIds.length;
  const activeLabel =
    count === 0
      ? ALL_LABEL
      : count === 1
        ? currentFleets[0]!.name
        : `${count} flotas seleccionadas`;

  // Usuario con una sola flota fija → label estática, sin selector.
  if (!canChoose) {
    return (
      <div className="text-foreground flex min-w-0 items-center gap-2 text-sm font-medium">
        <Truck className="text-muted-foreground h-4 w-4 shrink-0" aria-hidden />
        <span className="truncate">{activeLabel}</span>
      </div>
    );
  }

  return (
    <DropdownMenu
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen);
        if (!nextOpen) {
          setFleetSearch('');
          additiveRef.current = false;
        }
      }}
    >
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="border-border hover:bg-muted focus-visible:ring-ring flex min-h-11 max-w-full items-center gap-2 rounded-md border bg-white px-3 py-1.5 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 lg:min-h-0"
        >
          <Truck className="text-muted-foreground h-4 w-4 shrink-0" aria-hidden />
          <span className="max-w-[9rem] truncate sm:max-w-[180px]">{activeLabel}</span>
          <ChevronsUpDown className="text-muted-foreground h-4 w-4 shrink-0" aria-hidden />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="w-64 overflow-hidden"
        onPointerDownCapture={(event) => {
          additiveRef.current = event.ctrlKey || event.metaKey;
        }}
        onKeyDownCapture={(event) => {
          additiveRef.current = event.ctrlKey || event.metaKey;
        }}
      >
        <DropdownMenuLabel>Filtrar por flota</DropdownMenuLabel>
        <div className="relative px-1 pb-1">
          <Search
            className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
            aria-hidden
          />
          <Input
            value={fleetSearch}
            onChange={(event) => setFleetSearch(event.target.value)}
            onKeyDown={(event) => event.stopPropagation()}
            placeholder="Buscar flota"
            className="h-9 pl-8"
            aria-label="Buscar flota"
          />
        </div>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => setFleetIds([])}>
          <Check className={cn('h-4 w-4', count === 0 ? 'opacity-100' : 'opacity-0')} aria-hidden />
          <span>{ALL_LABEL}</span>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <div className="max-h-[min(240px,var(--radix-dropdown-menu-content-available-height))] overflow-y-auto">
          {filteredFleets.length > 0 ? (
            filteredFleets.map((fleet) => (
              <DropdownMenuItem
                key={fleet.id}
                onSelect={(e) => {
                  if (additiveRef.current) {
                    // Ctrl/Cmd → multiselección; el menú queda abierto.
                    e.preventDefault();
                    toggleFleet(fleet.id);
                    return;
                  }
                  // Clic simple → reemplaza la selección y cierra.
                  setFleetIds([fleet.id]);
                }}
              >
                <Check
                  className={cn(
                    'h-4 w-4',
                    selectedFleetIds.includes(fleet.id) ? 'opacity-100' : 'opacity-0',
                  )}
                  aria-hidden
                />
                <span className="truncate">{fleet.name}</span>
              </DropdownMenuItem>
            ))
          ) : (
            <p className="text-muted-foreground px-2 py-3 text-sm">No hay flotas que coincidan.</p>
          )}
        </div>
        <DropdownMenuSeparator />
        <p className="text-muted-foreground hidden px-2 pb-1 text-xs md:block">
          Ctrl (⌘ en Mac) + clic para seleccionar varias flotas.
        </p>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
