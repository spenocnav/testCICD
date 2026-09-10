'use client';

import * as React from 'react';
import { Plus, Users } from 'lucide-react';

import { Can } from '@/components/auth/can';
import { PageTitle } from '@/components/layout/page-title';
import { Button } from '@/components/ui/button';
import { UserFilters, type UserFiltersState } from '@/components/users/user-filters';
import { UserFormDialog } from '@/components/users/user-form-dialog';
import { UserPagination } from '@/components/users/user-pagination';
import { UserTable } from '@/components/users/user-table';
import type { UserRead } from '@/lib/types';
import { useUsers } from '@/lib/users';

const PAGE_SIZE = 20;

export default function UsuariosPage() {
  const [filters, setFilters] = React.useState<UserFiltersState>({
    search: '',
    role: '',
    onlyActive: false,
    includeArchived: false,
  });
  const [offset, setOffset] = React.useState(0);
  const [editing, setEditing] = React.useState<UserRead | null>(null);
  const [dialogOpen, setDialogOpen] = React.useState(false);

  // Reset offset al cambiar filtros
  React.useEffect(() => {
    setOffset(0);
  }, [filters.search, filters.role, filters.onlyActive, filters.includeArchived]);

  const { data, isLoading, isError } = useUsers({
    search: filters.search || undefined,
    role: filters.role || undefined,
    only_active: filters.onlyActive ? true : undefined,
    include_archived: filters.includeArchived ? true : undefined,
    limit: PAGE_SIZE,
    offset,
  });

  const openNew = () => {
    setEditing(null);
    setDialogOpen(true);
  };

  const openEdit = (user: UserRead) => {
    setEditing(user);
    setDialogOpen(true);
  };

  return (
    <section>
      <header className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <PageTitle
          icon={Users}
          title="Usuarios"
          description="Gestión de cuentas, roles y estado de acceso."
        />
        <Can permission="users.edit">
          <Button onClick={openNew}>
            <Plus />
            Nuevo usuario
          </Button>
        </Can>
      </header>

      <UserFilters value={filters} onChange={setFilters} />

      <UserTable data={data} isLoading={isLoading} isError={isError} onEdit={openEdit} />

      {data && (
        <UserPagination
          total={data.total}
          limit={PAGE_SIZE}
          offset={offset}
          onOffsetChange={setOffset}
        />
      )}

      <UserFormDialog open={dialogOpen} onOpenChange={setDialogOpen} initial={editing} />
    </section>
  );
}
