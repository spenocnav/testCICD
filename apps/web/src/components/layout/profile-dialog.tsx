'use client';

import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { MeResponse } from '@/lib/types';

interface ProfileDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  data: MeResponse;
}

function formatDate(value: string | null): string {
  if (!value) return 'Sin registro';
  return new Intl.DateTimeFormat('es-CO', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value));
}

export function ProfileDialog({ open, onOpenChange, data }: ProfileDialogProps) {
  const { user, fleet_scope_global: fleetScopeGlobal } = data;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Mi perfil</DialogTitle>
          <DialogDescription>Datos de tu cuenta y acceso actual.</DialogDescription>
        </DialogHeader>

        <div className="space-y-5">
          <section className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
                  Nombre
                </p>
                <p className="text-foreground text-sm font-semibold">{user.full_name}</p>
              </div>
              <div>
                <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
                  Correo
                </p>
                <p className="text-foreground break-all text-sm font-semibold">{user.email}</p>
              </div>
              <div>
                <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
                  Estado
                </p>
                <Badge variant={user.is_active ? 'success' : 'outline'}>
                  {user.is_active ? 'Activa' : 'Inactiva'}
                </Badge>
              </div>
              <div>
                <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
                  Último ingreso
                </p>
                <p className="text-foreground text-sm font-semibold">
                  {formatDate(user.last_login_at)}
                </p>
              </div>
            </div>
          </section>

          <section className="space-y-2">
            <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
              Roles
            </p>
            <div className="flex flex-wrap gap-2">
              {user.roles.length > 0 ? (
                user.roles.map((role) => (
                  <Badge key={role.id} variant="info">
                    {role.name}
                  </Badge>
                ))
              ) : (
                <p className="text-muted-foreground text-sm">Sin roles asignados.</p>
              )}
            </div>
          </section>

          <section className="space-y-2">
            <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
              Flotas
            </p>
            {fleetScopeGlobal ? (
              <Badge variant="success">Acceso a todas las flotas</Badge>
            ) : user.fleets.length > 0 ? (
              <div className="grid max-h-48 grid-cols-1 gap-2 overflow-y-auto rounded-md border p-2 sm:grid-cols-2">
                {user.fleets.map((fleet) => (
                  <div key={fleet.id} className="rounded-md border px-3 py-2">
                    <p className="text-foreground truncate text-sm font-semibold">{fleet.name}</p>
                    <p className="text-muted-foreground truncate text-xs">{fleet.code}</p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-muted-foreground text-sm">Sin flotas asignadas.</p>
            )}
          </section>
        </div>
      </DialogContent>
    </Dialog>
  );
}
