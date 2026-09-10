'use client';

import { ChevronDown, ChevronLeft, ChevronRight } from 'lucide-react';

import { Button } from '@/components/ui/button';

interface UserPaginationProps {
  total: number;
  limit: number;
  offset: number;
  onOffsetChange: (offset: number) => void;
  showPageSelect?: boolean;
}

export function UserPagination({
  total,
  limit,
  offset,
  onOffsetChange,
  showPageSelect = false,
}: UserPaginationProps) {
  const page = Math.floor(offset / limit) + 1;
  const totalPages = Math.max(1, Math.ceil(total / limit));
  const hasPrev = offset > 0;
  const hasNext = offset + limit < total;

  if (total === 0) return null;

  return (
    <div className="mt-4 flex flex-col gap-3 text-sm sm:flex-row sm:items-center sm:justify-between">
      <p className="text-muted-foreground">
        {total} resultado{total === 1 ? '' : 's'} · página {page} de {totalPages}
      </p>
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          className="h-11 flex-1 sm:h-9 sm:flex-none"
          disabled={!hasPrev}
          onClick={() => onOffsetChange(Math.max(0, offset - limit))}
        >
          <ChevronLeft />
          Anterior
        </Button>
        {showPageSelect && totalPages > 1 && (
          <label className="relative">
            <span className="sr-only">Seleccionar página</span>
            <select
              value={page}
              onChange={(event) => onOffsetChange((Number(event.target.value) - 1) * limit)}
              className="border-input bg-background h-11 w-14 appearance-none rounded-md border px-2 pr-6 text-center text-base sm:h-9 sm:w-12 sm:text-sm"
              aria-label="Seleccionar página"
            >
              {Array.from({ length: totalPages }, (_, index) => (
                <option key={index + 1} value={index + 1}>
                  {index + 1}
                </option>
              ))}
            </select>
            <ChevronDown
              className="text-muted-foreground pointer-events-none absolute right-1.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2"
              aria-hidden
            />
          </label>
        )}
        <Button
          variant="outline"
          size="sm"
          className="h-11 flex-1 sm:h-9 sm:flex-none"
          disabled={!hasNext}
          onClick={() => onOffsetChange(offset + limit)}
        >
          Siguiente
          <ChevronRight />
        </Button>
      </div>
    </div>
  );
}
