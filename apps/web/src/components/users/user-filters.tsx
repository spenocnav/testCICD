'use client';

import * as React from 'react';
import { ChevronDown, Search, X } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useHasPermission } from '@/lib/auth';
import { useRoles } from '@/lib/users';

export interface UserFiltersState {
  search: string;
  role: string;
  onlyActive: boolean;
  includeArchived: boolean;
}

interface UserFiltersProps {
  value: UserFiltersState;
  onChange: (next: UserFiltersState) => void;
}

export function UserFilters({ value, onChange }: UserFiltersProps) {
  const [search, setSearch] = React.useState(value.search);
  const canViewRoles = useHasPermission()('roles.view');
  const { data: roles } = useRoles(canViewRoles);

  // Debounce search
  React.useEffect(() => {
    if (search === value.search) return;
    const t = window.setTimeout(() => {
      onChange({ ...value, search });
    }, 300);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  return (
    <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-end">
      <div className="flex-1">
        <Label htmlFor="user-search" className="mb-1.5 block">
          Buscar
        </Label>
        <div className="relative">
          <Search
            aria-hidden
            className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
          />
          <Input
            id="user-search"
            placeholder="Nombre o correo…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-10"
          />
          {search && (
            <button
              type="button"
              aria-label="Limpiar búsqueda"
              onClick={() => setSearch('')}
              className="text-muted-foreground hover:text-foreground absolute right-2 top-1/2 -translate-y-1/2 rounded-sm p-1"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>

      {canViewRoles && (
        <div className="w-full md:w-56">
          <Label htmlFor="user-role" className="mb-1.5 block">
            Rol
          </Label>
          <div className="relative">
            <select
              id="user-role"
              value={value.role}
              onChange={(e) => onChange({ ...value, role: e.target.value })}
              className="border-input bg-background text-foreground focus-visible:ring-ring focus-visible:border-accent-blue h-11 w-full appearance-none rounded-md border px-3 pr-8 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1 md:text-sm"
            >
              <option value="">Todos los roles</option>
              {roles?.map((r) => (
                <option key={r.code} value={r.code}>
                  {r.name}
                </option>
              ))}
            </select>
            <ChevronDown
              className="text-muted-foreground pointer-events-none absolute right-2.5 top-1/2 h-4 w-4 -translate-y-1/2"
              aria-hidden
            />
          </div>
        </div>
      )}

      <div className="flex items-center gap-2 pb-2 md:pb-3">
        <Checkbox
          id="only-active"
          checked={value.onlyActive}
          onCheckedChange={(checked) => onChange({ ...value, onlyActive: checked === true })}
        />
        <Label htmlFor="only-active" className="cursor-pointer text-sm">
          Solo activos
        </Label>
      </div>

      <div className="flex items-center gap-2 pb-2 md:pb-3">
        <Checkbox
          id="include-archived"
          checked={value.includeArchived}
          onCheckedChange={(checked) => onChange({ ...value, includeArchived: checked === true })}
        />
        <Label htmlFor="include-archived" className="cursor-pointer text-sm">
          Mostrar archivados
        </Label>
      </div>

      {search && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setSearch('');
            onChange({ search: '', role: '', onlyActive: false, includeArchived: false });
          }}
        >
          Limpiar
        </Button>
      )}
    </div>
  );
}
