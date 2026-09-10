'use client';

import * as React from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { Eye, EyeOff, Loader2, Search } from 'lucide-react';
import { useForm } from 'react-hook-form';
import { toast } from 'sonner';
import { z } from 'zod';

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
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { extractErrorMessage } from '@/lib/api-client';
import { useFleets } from '@/lib/fleets';
import type { UserRead } from '@/lib/types';
import { useCreateUser, useRoles, useUpdateUser } from '@/lib/users';
import { cn } from '@/lib/utils';

const EMAIL_REGEX = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

const baseSchema = z.object({
  email: z.string().min(1, 'Ingresa el correo').regex(EMAIL_REGEX, 'Formato de correo inválido'),
  full_name: z.string().min(1, 'Ingresa el nombre').max(120, 'Máximo 120 caracteres'),
  role_codes: z.array(z.string()).min(1, 'Selecciona al menos un rol'),
  fleet_ids: z.array(z.string()),
  is_active: z.boolean(),
});

const createSchema = baseSchema.extend({
  password: z.string().min(8, 'Mínimo 8 caracteres').max(128),
});

const updateSchema = baseSchema.extend({
  password: z.string().optional(),
});

type CreateValues = z.infer<typeof createSchema>;
type UpdateValues = z.infer<typeof updateSchema>;

interface UserFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initial?: UserRead | null;
}

export function UserFormDialog({ open, onOpenChange, initial }: UserFormDialogProps) {
  const editing = !!initial;
  const { data: roles, isLoading: rolesLoading } = useRoles(open);
  const { data: fleets, isLoading: fleetsLoading } = useFleets(true, open);
  const createMutation = useCreateUser();
  const updateMutation = useUpdateUser();
  const [fleetSearch, setFleetSearch] = React.useState('');
  const [showPassword, setShowPassword] = React.useState(false);

  const form = useForm<CreateValues>({
    resolver: zodResolver(editing ? updateSchema : createSchema),
    defaultValues: {
      email: '',
      full_name: '',
      password: '',
      role_codes: [],
      fleet_ids: [],
      is_active: true,
    },
  });

  // Resetear cuando cambia `initial` o se abre
  React.useEffect(() => {
    if (!open) return;
    if (initial) {
      form.reset({
        email: initial.email,
        full_name: initial.full_name,
        password: '',
        role_codes: initial.roles.map((r) => r.code),
        fleet_ids: initial.fleets.map((f) => f.id),
        is_active: initial.is_active,
      });
    } else {
      form.reset({
        email: '',
        full_name: '',
        password: '',
        role_codes: [],
        fleet_ids: [],
        is_active: true,
      });
    }
    setFleetSearch('');
    setShowPassword(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initial?.id]);

  const selectedRoles = form.watch('role_codes');
  const selectedFleets = form.watch('fleet_ids');
  const errors = form.formState.errors;
  const normalizedFleetSearch = fleetSearch.trim().toLowerCase();
  const filteredFleets = React.useMemo(() => {
    if (!fleets) return [];
    if (!normalizedFleetSearch) return fleets;
    return fleets.filter((fleet) => fleet.name.toLowerCase().includes(normalizedFleetSearch));
  }, [fleets, normalizedFleetSearch]);
  const allFleetsSelected =
    (fleets?.length ?? 0) > 0 &&
    (fleets?.every((fleet) => selectedFleets.includes(fleet.id)) ?? false);

  const submitting = createMutation.isPending || updateMutation.isPending;

  const onSubmit = form.handleSubmit(async (values) => {
    try {
      if (editing && initial) {
        const patch: UpdateValues = {
          email: values.email,
          full_name: values.full_name,
          role_codes: values.role_codes,
          fleet_ids: values.fleet_ids,
          is_active: values.is_active,
          ...(values.password ? { password: values.password } : {}),
        };
        await updateMutation.mutateAsync({
          id: initial.id,
          full_name: patch.full_name,
          role_codes: patch.role_codes,
          fleet_ids: values.fleet_ids,
          is_active: patch.is_active,
          password: patch.password,
        });
        toast.success('Usuario actualizado');
      } else {
        await createMutation.mutateAsync({
          email: values.email,
          password: values.password!,
          full_name: values.full_name,
          role_codes: values.role_codes,
          fleet_ids: values.fleet_ids,
          is_active: values.is_active,
        });
        toast.success('Usuario creado');
      }
      onOpenChange(false);
    } catch (err) {
      toast.error(extractErrorMessage(err, 'No se pudo guardar'));
    }
  });

  const toggleRole = (code: string, checked: boolean) => {
    const current = form.getValues('role_codes');
    const next = checked
      ? Array.from(new Set([...current, code]))
      : current.filter((c) => c !== code);
    form.setValue('role_codes', next, { shouldValidate: true });

    // Si es admin, seleccionar todas las flotas automáticamente
    if (code === 'admin' && checked && fleets) {
      form.setValue(
        'fleet_ids',
        fleets.map((f) => f.id),
        { shouldValidate: true },
      );
    }
  };

  const toggleFleet = (id: string, checked: boolean) => {
    const current = form.getValues('fleet_ids');
    const next = checked ? Array.from(new Set([...current, id])) : current.filter((f) => f !== id);
    form.setValue('fleet_ids', next, { shouldValidate: true });
  };

  const toggleAllFleets = (checked: boolean) => {
    form.setValue('fleet_ids', checked ? (fleets ?? []).map((fleet) => fleet.id) : [], {
      shouldValidate: true,
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{editing ? 'Editar usuario' : 'Nuevo usuario'}</DialogTitle>
          <DialogDescription>
            {editing
              ? 'Actualiza datos y roles. El correo no se modifica.'
              : 'Crea una cuenta. La contraseña se entrega al usuario por canal seguro.'}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={onSubmit} noValidate className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="full_name">
              Nombre completo <span className="text-destructive">*</span>
            </Label>
            <Input
              id="full_name"
              autoFocus
              aria-invalid={errors.full_name ? true : undefined}
              className={cn(errors.full_name && 'border-destructive')}
              {...form.register('full_name')}
            />
            {errors.full_name && (
              <p role="alert" className="text-destructive text-xs font-medium">
                {errors.full_name.message}
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="email">
              Correo <span className="text-destructive">*</span>
            </Label>
            <Input
              id="email"
              type="email"
              autoComplete="off"
              disabled={editing}
              aria-invalid={errors.email ? true : undefined}
              className={cn(errors.email && 'border-destructive')}
              {...form.register('email')}
            />
            {errors.email && (
              <p role="alert" className="text-destructive text-xs font-medium">
                {errors.email.message}
              </p>
            )}
            {editing && (
              <p className="text-muted-foreground text-xs">El correo no se puede modificar.</p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="password">
              {editing ? 'Nueva contraseña' : 'Contraseña'}{' '}
              {!editing && <span className="text-destructive">*</span>}
            </Label>
            <div className="relative">
              <Input
                id="password"
                type={showPassword ? 'text' : 'password'}
                autoComplete="new-password"
                placeholder={editing ? 'Déjala vacía para conservarla' : undefined}
                aria-invalid={errors.password ? true : undefined}
                className={cn('pr-11', errors.password && 'border-destructive')}
                {...form.register('password')}
              />
              <button
                type="button"
                aria-label={showPassword ? 'Ocultar contraseña' : 'Mostrar contraseña'}
                onClick={() => setShowPassword((value) => !value)}
                className="text-muted-foreground hover:text-foreground absolute right-0 top-0 flex h-11 w-11 items-center justify-center"
              >
                {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
            {errors.password && (
              <p role="alert" className="text-destructive text-xs font-medium">
                {errors.password.message}
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label>
              Roles <span className="text-destructive">*</span>
            </Label>
            {rolesLoading ? (
              <p className="text-muted-foreground text-sm">Cargando roles…</p>
            ) : (
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {roles?.map((role) => {
                  const checked = selectedRoles.includes(role.code);
                  return (
                    <label
                      key={role.code}
                      className={cn(
                        'border-border flex cursor-pointer items-start gap-2 rounded-md border p-3 transition-colors',
                        checked && 'border-brand-red bg-[var(--sidebar-item-active-bg)]',
                      )}
                    >
                      <Checkbox
                        checked={checked}
                        onCheckedChange={(v) => toggleRole(role.code, v === true)}
                      />
                      <div className="leading-tight">
                        <p className="text-foreground text-sm font-semibold">{role.name}</p>
                        {role.description && (
                          <p className="text-muted-foreground text-xs">{role.description}</p>
                        )}
                      </div>
                    </label>
                  );
                })}
              </div>
            )}
            {errors.role_codes && (
              <p role="alert" className="text-destructive text-xs font-medium">
                {errors.role_codes.message as string}
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label>Flotas asignadas</Label>
            <p className="text-muted-foreground text-xs">
              Define qué flotas puede ver el usuario. Vacío = sin acceso a flotas.
            </p>
            {fleetsLoading ? (
              <p className="text-muted-foreground text-sm">Cargando flotas…</p>
            ) : fleets && fleets.length > 0 ? (
              <div className="space-y-2">
                <label
                  htmlFor="all-fleets"
                  className={cn(
                    'border-border flex cursor-pointer items-center gap-3 rounded-md border p-3 transition-colors',
                    allFleetsSelected && 'border-brand-red bg-[var(--sidebar-item-active-bg)]',
                  )}
                >
                  <Checkbox
                    id="all-fleets"
                    checked={allFleetsSelected}
                    onCheckedChange={(value) => toggleAllFleets(value === true)}
                  />
                  <span className="text-foreground text-sm font-semibold">Todas las flotas</span>
                  <span className="text-muted-foreground ml-auto text-xs">
                    {selectedFleets.length} de {fleets.length}
                  </span>
                </label>
                <div className="relative">
                  <Search
                    className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
                    aria-hidden
                  />
                  <Input
                    value={fleetSearch}
                    onChange={(event) => setFleetSearch(event.target.value)}
                    placeholder="Buscar flota"
                    className="pl-9"
                    aria-label="Buscar flota"
                  />
                </div>
                {filteredFleets.length > 0 ? (
                  <div className="max-h-64 overflow-y-auto rounded-md border p-2">
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      {filteredFleets.map((fleet) => {
                        const checked = selectedFleets.includes(fleet.id);
                        return (
                          <label
                            key={fleet.id}
                            className={cn(
                              'border-border flex cursor-pointer items-center gap-2 rounded-md border p-3 transition-colors',
                              checked && 'border-brand-red bg-[var(--sidebar-item-active-bg)]',
                            )}
                          >
                            <Checkbox
                              checked={checked}
                              onCheckedChange={(v) => toggleFleet(fleet.id, v === true)}
                            />
                            <span className="text-foreground truncate text-sm font-semibold">
                              {fleet.name}
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  </div>
                ) : (
                  <p className="text-muted-foreground rounded-md border px-3 py-4 text-sm">
                    No hay flotas que coincidan con la búsqueda.
                  </p>
                )}
              </div>
            ) : (
              <p className="text-muted-foreground text-sm">No hay flotas registradas.</p>
            )}
          </div>

          <div className="flex items-center gap-2">
            <Checkbox
              id="is_active"
              checked={form.watch('is_active')}
              onCheckedChange={(v) => form.setValue('is_active', v === true)}
            />
            <Label htmlFor="is_active" className="cursor-pointer text-sm">
              Cuenta activa
            </Label>
          </div>

          <DialogFooter className="mt-1">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={submitting}
            >
              Cancelar
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting && <Loader2 className="animate-spin" aria-hidden />}
              {editing ? 'Guardar cambios' : 'Crear usuario'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
