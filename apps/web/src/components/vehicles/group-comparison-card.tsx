'use client';

import * as React from 'react';
import { FolderTree } from 'lucide-react';

import { useGroupFilterAvailable } from '@/components/vehicles/group-filter';
import {
  groupPath,
  rootAncestorId,
  useVehicleGroups,
} from '@/lib/vehicle-groups';
import { cn } from '@/lib/utils';

/**
 * Comparativo por grupo interno del cliente (categorías/subcategorías).
 *
 * Recibe filas con componentes ADITIVOS (sumas/conteos) por grupo HOJA y hace
 * el rollup al nivel elegido (Categorías = ancestro raíz, Subcategorías = el
 * nodo hoja) sumando componentes ANTES de derivar la métrica: así un promedio
 * o una razón (km/gal) sale ponderado y no como promedio de promedios.
 *
 * Reglas heredadas del filtro de grupos:
 * - solo se pinta con UNA flota efectiva en alcance y con grupos
 *   (`useGroupFilterAvailable`); en cualquier otro caso devuelve null;
 * - las unidades sin grupo entran como bucket "Sin grupo" (si se ocultaran,
 *   los totales no cuadrarían con los KPIs de la pestaña); ese bucket no es
 *   clicable porque el filtro no puede expresar "sin grupo".
 */

export interface GroupComparisonRow {
  /** Grupo HOJA de la unidad (vehículo/registro); null = sin grupo. */
  groupId: string | null;
  /** Componentes aditivos: sumas y conteos, nunca razones. */
  values: Record<string, number>;
}

interface GroupComparisonCardProps {
  title: string;
  subtitle?: string;
  rows: GroupComparisonRow[];
  /** Deriva el valor de la barra desde los componentes ya sumados. */
  derive: (totals: Record<string, number>) => number | null;
  format: (value: number) => string;
  /** Texto secundario por bucket (ej. "5 placas · 2 en rojo"). */
  detail?: (totals: Record<string, number>) => string | null;
  /** Línea de referencia (ej. promedio de la flota). */
  reference?: { label: string; value: number } | null;
  /** Color de la barra según el valor (ej. umbrales de calificación). */
  colorFor?: (value: number) => string;
  /** Clic en un bucket → aplicar ese grupo al filtro de la pantalla. */
  onSelectGroup?: (groupId: string) => void;
  /** true = barras más largas arriba; false = orden ascendente. */
  sortDesc?: boolean;
  isLoading?: boolean;
  emptyText?: string;
}

const SIN_GRUPO = '__sin_grupo__';

export function GroupComparisonCard({
  title,
  subtitle,
  rows,
  derive,
  format,
  detail,
  reference = null,
  colorFor,
  onSelectGroup,
  sortDesc = true,
  isLoading = false,
  emptyText = 'Sin datos en el rango para comparar por grupo.',
}: GroupComparisonCardProps) {
  const available = useGroupFilterAvailable();
  const groupsQuery = useVehicleGroups();
  const groups = React.useMemo(() => groupsQuery.data ?? [], [groupsQuery.data]);
  const hasSubcategories = React.useMemo(
    () => groups.some((g) => g.parent_id != null),
    [groups],
  );
  const [level, setLevel] = React.useState<'raiz' | 'hoja'>('raiz');
  const effectiveLevel = hasSubcategories ? level : 'hoja';

  const buckets = React.useMemo(() => {
    const acc = new Map<string, Record<string, number>>();
    for (const row of rows) {
      let key: string = SIN_GRUPO;
      if (row.groupId != null) {
        key =
          effectiveLevel === 'raiz'
            ? (rootAncestorId(groups, row.groupId) ?? SIN_GRUPO)
            : row.groupId;
      }
      const totals = acc.get(key) ?? {};
      for (const [k, v] of Object.entries(row.values)) {
        totals[k] = (totals[k] ?? 0) + v;
      }
      acc.set(key, totals);
    }
    const items = [...acc.entries()].map(([key, totals]) => ({
      key,
      isSinGrupo: key === SIN_GRUPO,
      label:
        key === SIN_GRUPO
          ? 'Sin grupo'
          : effectiveLevel === 'hoja'
            ? (groupPath(groups, key) ?? 'Grupo')
            : (groups.find((g) => g.id === key)?.name ?? 'Grupo'),
      totals,
      value: derive(totals),
    }));
    items.sort((a, b) => {
      // "Sin grupo" siempre al final; el resto por valor.
      if (a.isSinGrupo !== b.isSinGrupo) return a.isSinGrupo ? 1 : -1;
      const av = a.value ?? Number.NEGATIVE_INFINITY;
      const bv = b.value ?? Number.NEGATIVE_INFINITY;
      return sortDesc ? bv - av : av - bv;
    });
    return items;
  }, [rows, groups, effectiveLevel, derive, sortDesc]);

  if (!available) {
    return null;
  }

  const values = buckets.map((b) => b.value).filter((v): v is number => v != null);
  const max = Math.max(...values, reference?.value ?? 0, 0.0001);

  return (
    <div className="bg-card rounded-lg border p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="font-heading flex items-center gap-1.5 text-sm font-bold tracking-tight">
            <FolderTree className="text-muted-foreground h-4 w-4" aria-hidden />
            {title}
          </h3>
          {subtitle ? <p className="text-muted-foreground text-xs">{subtitle}</p> : null}
        </div>
        {hasSubcategories ? (
          <div className="bg-muted inline-flex rounded-md p-0.5">
            {(
              [
                ['raiz', 'Categorías'],
                ['hoja', 'Subcategorías'],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setLevel(key)}
                className={cn(
                  'rounded px-2.5 py-1 text-xs font-medium transition',
                  level === key ? 'bg-white shadow-sm' : 'hover:bg-white/60',
                )}
              >
                {label}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      {isLoading ? (
        <p className="text-muted-foreground text-sm">Cargando…</p>
      ) : buckets.length === 0 ? (
        <p className="text-muted-foreground text-sm">{emptyText}</p>
      ) : (
        <ul className="space-y-2">
          {buckets.map((bucket) => {
            const pct =
              bucket.value != null ? Math.max(3, (Math.max(bucket.value, 0) / max) * 100) : 0;
            const clickable = !bucket.isSinGrupo && Boolean(onSelectGroup);
            const rowDetail = detail?.(bucket.totals) ?? null;
            const barColor =
              bucket.value != null && colorFor ? colorFor(bucket.value) : 'bg-accent-blue';
            const content = (
              <>
                <span
                  className={cn(
                    'w-40 shrink-0 truncate text-left',
                    bucket.isSinGrupo && 'text-muted-foreground italic',
                  )}
                  title={bucket.label}
                >
                  {bucket.label}
                </span>
                <span className="bg-muted rounded-pill relative h-2.5 flex-1 overflow-hidden">
                  {bucket.value != null ? (
                    <span
                      className={cn('rounded-pill absolute inset-y-0 left-0', barColor)}
                      style={{ width: `${pct}%` }}
                    />
                  ) : null}
                  {reference && reference.value <= max ? (
                    <span
                      className="bg-foreground/50 absolute inset-y-0 w-px"
                      style={{ left: `${(reference.value / max) * 100}%` }}
                      title={`${reference.label}: ${format(reference.value)}`}
                    />
                  ) : null}
                </span>
                <span className="w-20 shrink-0 text-right font-mono text-sm tabular-nums">
                  {bucket.value != null ? format(bucket.value) : '—'}
                </span>
              </>
            );
            return (
              <li key={bucket.key}>
                {clickable ? (
                  <button
                    type="button"
                    onClick={() => onSelectGroup?.(bucket.key)}
                    title="Filtrar la pestaña por este grupo"
                    className="hover:bg-muted/60 flex w-full items-center gap-2 rounded-md px-1 py-0.5 text-sm transition"
                  >
                    {content}
                  </button>
                ) : (
                  <div className="flex items-center gap-2 px-1 py-0.5 text-sm">{content}</div>
                )}
                {rowDetail ? (
                  <p className="text-muted-foreground pl-1 text-xs">{rowDetail}</p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}

      {reference ? (
        <p className="text-muted-foreground mt-2 text-xs">
          | {reference.label}: {format(reference.value)}
        </p>
      ) : null}
    </div>
  );
}
