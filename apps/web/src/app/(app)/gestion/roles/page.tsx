'use client';

import * as React from 'react';
import { Pencil, Plus, ShieldCheck, Trash2 } from 'lucide-react';
import { toast } from 'sonner';

import { PageTitle } from '@/components/layout/page-title';
import { RoleFormDialog } from '@/components/roles/role-form-dialog';
import { RolePermissionsMatrix } from '@/components/roles/role-permissions-matrix';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { extractErrorMessage } from '@/lib/api-client';
import { useMe } from '@/lib/auth';
import { useDeleteRole, useModules, useRolesDetail } from '@/lib/roles';
import type { RoleDetail } from '@/lib/types';
import { cn } from '@/lib/utils';

export default function RolesPage() {
  const { data: roles, isLoading: rolesLoading, isError: rolesError } = useRolesDetail();
  const { data: modules, isLoading: modulesLoading } = useModules();
  const deleteRole = useDeleteRole();
  const { data: me } = useMe();
  const canEdit = me?.user.roles.some((role) => role.code === 'admin') ?? false;

  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [editingRole, setEditingRole] = React.useState<RoleDetail | null>(null);

  // Seleccionar el primer rol por defecto.
  React.useEffect(() => {
    const first = roles?.[0];
    if (!selectedId && first) {
      setSelectedId(first.id);
    }
  }, [roles, selectedId]);

  const selected = roles?.find((r) => r.id === selectedId) ?? null;

  const openNew = () => {
    setEditingRole(null);
    setDialogOpen(true);
  };

  const openEdit = (role: RoleDetail) => {
    setEditingRole(role);
    setDialogOpen(true);
  };

  const onDelete = async (role: RoleDetail) => {
    if (!window.confirm(`¿Eliminar el rol "${role.name}"? Esta acción no se puede deshacer.`)) {
      return;
    }
    try {
      await deleteRole.mutateAsync(role.id);
      toast.success('Rol eliminado');
      if (selectedId === role.id) setSelectedId(null);
    } catch (err) {
      toast.error(extractErrorMessage(err, 'No se pudo eliminar el rol'));
    }
  };

  return (
    <section>
      <header className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <PageTitle
          icon={ShieldCheck}
          title="Roles y permisos"
          description="Define qué módulos puede ver o editar cada rol."
        />
        {canEdit && (
          <Button onClick={openNew}>
            <Plus />
            Nuevo rol
          </Button>
        )}
      </header>

      {rolesError && <p className="text-destructive text-sm">No se pudieron cargar los roles.</p>}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[280px_1fr]">
        {/* Lista de roles */}
        <div className="space-y-2">
          {rolesLoading
            ? Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-16 w-full rounded-md" />
              ))
            : roles?.map((role) => (
                <button
                  key={role.id}
                  type="button"
                  onClick={() => setSelectedId(role.id)}
                  className={cn(
                    'border-border w-full rounded-md border bg-white p-3 text-left transition-colors',
                    'hover:bg-[var(--sidebar-item-active-bg)]',
                    selectedId === role.id && 'border-brand-red bg-[var(--sidebar-item-active-bg)]',
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-foreground text-sm font-semibold">{role.name}</span>
                    {role.is_system ? (
                      <Badge variant="outline">Sistema</Badge>
                    ) : (
                      <Badge variant="info">Custom</Badge>
                    )}
                  </div>
                  {role.description && (
                    <p className="text-muted-foreground mt-1 line-clamp-2 text-xs">
                      {role.description}
                    </p>
                  )}
                </button>
              ))}
        </div>

        {/* Matriz del rol seleccionado */}
        <div>
          {selected && modules ? (
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <h2 className="font-heading text-lg font-bold">{selected.name}</h2>
                  <p className="text-muted-foreground text-xs">
                    Código: <code className="font-mono">{selected.code}</code>
                  </p>
                </div>
                {canEdit && (
                  <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={() => openEdit(selected)}>
                      <Pencil className="h-4 w-4" aria-hidden />
                      Editar
                    </Button>
                    {!selected.is_system && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => onDelete(selected)}
                        disabled={deleteRole.isPending}
                      >
                        <Trash2 className="h-4 w-4" aria-hidden />
                        Eliminar
                      </Button>
                    )}
                  </div>
                )}
              </div>

              <RolePermissionsMatrix role={selected} modules={modules} editable={canEdit} />
            </div>
          ) : modulesLoading || rolesLoading ? (
            <Skeleton className="h-64 w-full rounded-md" />
          ) : (
            <p className="text-muted-foreground text-sm">
              Selecciona un rol para ver sus permisos.
            </p>
          )}
        </div>
      </div>

      <RoleFormDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        initial={editingRole}
        onCreated={(role) => setSelectedId(role.id)}
      />
    </section>
  );
}
