'use client';

import * as React from 'react';
import { Check, ChevronsUpDown, Search } from 'lucide-react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
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
import { extractErrorMessage } from '@/lib/api-client';
import { DATASET_KEYS, DATASET_LABELS, useFleetBulkBackfill } from '@/lib/extraction';
import type { Vehicle } from '@/lib/types';
import { cn } from '@/lib/utils';

interface ReprocessDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  fleetId: string;
  vehicles: Vehicle[];
}

export function ReprocessDialog({ open, onOpenChange, fleetId, vehicles }: ReprocessDialogProps) {
  const backfill = useFleetBulkBackfill(fleetId);

  const [allPlates, setAllPlates] = React.useState(true);
  const [selectedIds, setSelectedIds] = React.useState<string[]>([]);
  const [plateSearch, setPlateSearch] = React.useState('');
  const [plateOpen, setPlateOpen] = React.useState(false);

  const [allDatasets, setAllDatasets] = React.useState(true);
  const [selectedDatasets, setSelectedDatasets] = React.useState<string[]>([]);

  const [fromDate, setFromDate] = React.useState('');

  // Reinicia el formulario cada vez que se abre.
  React.useEffect(() => {
    if (open) {
      setAllPlates(true);
      setSelectedIds([]);
      setPlateSearch('');
      setAllDatasets(true);
      setSelectedDatasets([]);
      setFromDate('');
    }
  }, [open]);

  const normalized = plateSearch.trim().toLowerCase();
  const filteredVehicles = React.useMemo(() => {
    if (!normalized) return vehicles;
    return vehicles.filter((v) =>
      [v.plate, v.nombre_vehiculo].filter(Boolean).some((x) =>
        String(x).toLowerCase().includes(normalized),
      ),
    );
  }, [vehicles, normalized]);

  function toggleId(id: string) {
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  function toggleDataset(key: string) {
    setSelectedDatasets((prev) =>
      prev.includes(key) ? prev.filter((x) => x !== key) : [...prev, key],
    );
  }

  const platesValid = allPlates || selectedIds.length > 0;
  const datasetsValid = allDatasets || selectedDatasets.length > 0;
  const canSubmit = Boolean(fromDate) && platesValid && datasetsValid && !backfill.isPending;

  const plateLabel = allPlates
    ? 'Todas las placas'
    : selectedIds.length > 0
      ? `${selectedIds.length} placa(s)`
      : 'Selecciona placas';

  const onSubmit = async () => {
    if (!fromDate) return;
    try {
      const result = await backfill.mutateAsync({
        from_date: new Date(`${fromDate}T00:00:00`).toISOString(),
        vehicle_ids: allPlates ? null : selectedIds,
        datasets: allDatasets ? null : selectedDatasets,
      });
      toast.success(
        `Reprocesamiento programado: ${result.vehicles} vehículo(s) × ${result.datasets} variable(s). ` +
          'El worker lo tomará en su próxima ejecución.',
      );
      onOpenChange(false);
    } catch (err) {
      toast.error(extractErrorMessage(err, 'No se pudo programar el reprocesamiento'));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Reprocesar vehículos</DialogTitle>
          <DialogDescription>
            Programa la re-extracción desde una fecha. No se ejecuta ahora: solo deja el estado
            listo para que el worker lo tome en su próxima corrida. El cargador es idempotente (no
            duplica datos ya extraídos).
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* Placas */}
          <div className="space-y-2">
            <label className="text-muted-foreground text-xs font-semibold uppercase tracking-wider">
              Placas
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={allPlates}
                onCheckedChange={(c) => setAllPlates(Boolean(c))}
                aria-label="Todas las placas"
              />
              Todas las placas de la flota
            </label>
            {!allPlates && (
              <DropdownMenu
                open={plateOpen}
                onOpenChange={(o) => {
                  setPlateOpen(o);
                  if (!o) setPlateSearch('');
                }}
              >
                <DropdownMenuTrigger asChild>
                  <button
                    type="button"
                    className="border-border hover:bg-muted focus-visible:ring-ring flex w-full items-center justify-between gap-2 rounded-md border bg-white px-3 py-2 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2"
                  >
                    <span className="truncate">{plateLabel}</span>
                    <ChevronsUpDown className="text-muted-foreground h-4 w-4" aria-hidden />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-[--radix-dropdown-menu-trigger-width] overflow-hidden">
                  <DropdownMenuLabel>Selecciona placas</DropdownMenuLabel>
                  <div className="relative px-1 pb-1">
                    <Search
                      className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
                      aria-hidden
                    />
                    <Input
                      value={plateSearch}
                      onChange={(e) => setPlateSearch(e.target.value)}
                      onKeyDown={(e) => e.stopPropagation()}
                      placeholder="Buscar placa"
                      className="h-9 pl-8"
                      aria-label="Buscar placa"
                    />
                  </div>
                  <DropdownMenuSeparator />
                  <div className="max-h-[min(240px,var(--radix-dropdown-menu-content-available-height))] overflow-y-auto">
                    {filteredVehicles.length > 0 ? (
                      filteredVehicles.map((v) => (
                        <DropdownMenuItem
                          key={v.id}
                          onSelect={(e) => {
                            e.preventDefault();
                            toggleId(v.id);
                          }}
                        >
                          <Check
                            className={cn(
                              'h-4 w-4',
                              selectedIds.includes(v.id) ? 'opacity-100' : 'opacity-0',
                            )}
                            aria-hidden
                          />
                          <span className="truncate font-mono">{v.plate}</span>
                        </DropdownMenuItem>
                      ))
                    ) : (
                      <p className="text-muted-foreground px-2 py-3 text-sm">
                        No hay placas que coincidan.
                      </p>
                    )}
                  </div>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </div>

          {/* Variables (datasets) */}
          <div className="space-y-2">
            <label className="text-muted-foreground text-xs font-semibold uppercase tracking-wider">
              Variables
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={allDatasets}
                onCheckedChange={(c) => setAllDatasets(Boolean(c))}
                aria-label="Todas las variables"
              />
              Todas las variables
            </label>
            {!allDatasets && (
              <div className="grid grid-cols-2 gap-2">
                {DATASET_KEYS.map((key) => (
                  <label key={key} className="flex items-center gap-2 text-sm">
                    <Checkbox
                      checked={selectedDatasets.includes(key)}
                      onCheckedChange={() => toggleDataset(key)}
                      aria-label={DATASET_LABELS[key]}
                    />
                    {DATASET_LABELS[key]}
                  </label>
                ))}
              </div>
            )}
          </div>

          {/* Fecha */}
          <div className="space-y-1">
            <label className="text-muted-foreground text-xs font-semibold uppercase tracking-wider">
              Extraer desde
            </label>
            <Input
              type="date"
              value={fromDate}
              onChange={(e) => setFromDate(e.target.value)}
              className="h-10 w-44"
            />
          </div>

          {!fromDate && (
            <Badge variant="outline" className="text-muted-foreground">
              Selecciona una fecha de inicio
            </Badge>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={backfill.isPending}>
            Cancelar
          </Button>
          <Button onClick={onSubmit} disabled={!canSubmit}>
            {backfill.isPending ? 'Programando…' : 'Programar reprocesamiento'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
