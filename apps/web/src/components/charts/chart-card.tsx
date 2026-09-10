'use client';

import * as React from 'react';

import { Skeleton } from '@/components/ui/skeleton';

interface ChartCardProps {
  title: string;
  subtitle?: string;
  isLoading?: boolean;
  isError?: boolean;
  isEmpty?: boolean;
  action?: React.ReactNode;
  children: React.ReactNode;
  /** Altura del área del gráfico en px. */
  height?: number;
}

/** Contenedor visual consistente para un gráfico (mismo estilo que las cards). */
export function ChartCard({
  title,
  subtitle,
  isLoading,
  isError,
  isEmpty,
  action,
  children,
  height = 260,
}: ChartCardProps) {
  return (
    <div className="bg-card rounded-lg border p-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h3 className="font-heading text-sm font-bold tracking-tight">{title}</h3>
          {subtitle && <p className="text-muted-foreground text-xs">{subtitle}</p>}
        </div>
        {action}
      </div>
      <div style={{ height }}>
        {isLoading ? (
          <Skeleton className="h-full w-full" />
        ) : isError ? (
          <div className="text-destructive flex h-full items-center justify-center text-sm">
            No se pudieron cargar los datos del gráfico.
          </div>
        ) : isEmpty ? (
          <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
            Sin datos para los filtros seleccionados.
          </div>
        ) : (
          children
        )}
      </div>
    </div>
  );
}
