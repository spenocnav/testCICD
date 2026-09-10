'use client';

import * as React from 'react';
import { Archive, MoreHorizontal, Pencil, Power, PowerOff, RotateCcw } from 'lucide-react';
import { toast } from 'sonner';

import { Can } from '@/components/auth/can';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
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
import type { PaginatedUsers, UserRead } from '@/lib/types';
import { useDeactivateUser, useUpdateUser } from '@/lib/users';

interface UserTableProps {
  data: PaginatedUsers | undefined;
  isLoading: boolean;
  isError: boolean;
  onEdit: (user: UserRead) => void;
}

function formatDate(value: string | null): string {
  if (!value) return '—';
  try {
    return new Date(value).toLocaleString('es-CO', {
      dateStyle: 'short',
      timeStyle: 'short',
    });
  } catch {
    return value;
  }
}

export function UserTable({ data, isLoading, isError, onEdit }: UserTableProps) {
  const deactivate = useDeactivateUser();
  const update = useUpdateUser();

  if (isLoading && !data) {
    return (
      <div className="border-border bg-card shadow-soft rounded-lg border">
        <div className="space-y-2 p-4">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="border-destructive/40 bg-destructive/5 text-destructive rounded-lg border p-6 text-sm">
        No se pudieron cargar los usuarios. Reintenta más tarde.
      </div>
    );
  }

  if (!data || data.items.length === 0) {
    return (
      <div className="border-border bg-card text-muted-foreground shadow-soft rounded-lg border p-10 text-center text-sm">
        Sin resultados.
      </div>
    );
  }

  const handleDeactivate = async (user: UserRead) => {
    try {
      await deactivate.mutateAsync(user.id);
      toast.success(`${user.full_name} fue desactivado`);
    } catch (err) {
      toast.error(extractErrorMessage(err, 'No se pudo desactivar'));
    }
  };

  const handleReactivate = async (user: UserRead) => {
    try {
      await update.mutateAsync({ id: user.id, is_active: true });
      toast.success(`${user.full_name} fue reactivado`);
    } catch (err) {
      toast.error(extractErrorMessage(err, 'No se pudo reactivar'));
    }
  };

  const handleArchive = async (user: UserRead, archived: boolean) => {
    try {
      await update.mutateAsync({ id: user.id, is_archived: archived });
      toast.success(
        archived ? user.full_name + ' fue archivado' : user.full_name + ' fue restaurado',
      );
    } catch (err) {
      toast.error(
        extractErrorMessage(err, archived ? 'No se pudo archivar' : 'No se pudo restaurar'),
      );
    }
  };

  return (
    <div className="border-border bg-card shadow-soft rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Nombre</TableHead>
            <TableHead>Correo</TableHead>
            <TableHead>Roles</TableHead>
            <TableHead>Estado</TableHead>
            <TableHead>Último acceso</TableHead>
            <TableHead className="w-12 text-right">Acciones</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.items.map((user) => (
            <TableRow key={user.id}>
              <TableCell className="font-semibold">{user.full_name}</TableCell>
              <TableCell className="text-muted-foreground">{user.email}</TableCell>
              <TableCell>
                <div className="flex flex-wrap gap-1">
                  {user.roles.length === 0 && (
                    <span className="text-muted-foreground text-xs">—</span>
                  )}
                  {user.roles.map((r) => (
                    <Badge key={r.code} variant="info">
                      {r.name}
                    </Badge>
                  ))}
                </div>
              </TableCell>
              <TableCell>
                {user.is_archived ? (
                  <Badge variant="outline">Archivado</Badge>
                ) : user.is_active ? (
                  <Badge variant="success">Activo</Badge>
                ) : (
                  <Badge variant="outline">Inactivo</Badge>
                )}
              </TableCell>
              <TableCell className="text-muted-foreground">
                {formatDate(user.last_login_at)}
              </TableCell>
              <TableCell className="text-right">
                <Can
                  permission="users.edit"
                  mode="any"
                  fallback={<span className="text-muted-foreground text-xs">—</span>}
                >
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon" aria-label="Acciones">
                        <MoreHorizontal />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <Can permission="users.edit">
                        <DropdownMenuItem onSelect={() => handleArchive(user, !user.is_archived)}>
                          {user.is_archived ? <RotateCcw /> : <Archive />}
                          <span>{user.is_archived ? 'Restaurar' : 'Archivar'}</span>
                        </DropdownMenuItem>
                      </Can>
                      <Can permission="users.edit">
                        <DropdownMenuItem onSelect={() => onEdit(user)}>
                          <Pencil />
                          <span>Editar</span>
                        </DropdownMenuItem>
                      </Can>
                      <Can permission="users.edit">
                        {user.is_active ? (
                          <DropdownMenuItem
                            className="text-destructive"
                            onSelect={() => handleDeactivate(user)}
                          >
                            <PowerOff />
                            <span>Desactivar</span>
                          </DropdownMenuItem>
                        ) : (
                          <Can permission="users.edit">
                            <DropdownMenuItem onSelect={() => handleReactivate(user)}>
                              <Power />
                              <span>Reactivar</span>
                            </DropdownMenuItem>
                          </Can>
                        )}
                      </Can>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </Can>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
