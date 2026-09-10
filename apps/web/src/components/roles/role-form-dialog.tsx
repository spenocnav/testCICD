'use client';

import * as React from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import { useForm } from 'react-hook-form';
import { toast } from 'sonner';
import { z } from 'zod';

import { Button } from '@/components/ui/button';
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
import { useCreateRole, useUpdateRole } from '@/lib/roles';
import type { RoleDetail } from '@/lib/types';
import { cn } from '@/lib/utils';

const createSchema = z.object({
  code: z
    .string()
    .min(2, 'Mínimo 2 caracteres')
    .max(64)
    .regex(/^[a-z][a-z0-9_]*$/, 'Solo minúsculas, números y guion bajo; empieza con letra'),
  name: z.string().min(2, 'Ingresa el nombre').max(120),
  description: z.string().max(255).optional(),
});

const updateSchema = createSchema.partial({ code: true });

type FormValues = z.infer<typeof createSchema>;

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initial?: RoleDetail | null;
  onCreated?: (role: RoleDetail) => void;
}

export function RoleFormDialog({ open, onOpenChange, initial, onCreated }: Props) {
  const editing = !!initial;
  const createMutation = useCreateRole();
  const updateMutation = useUpdateRole();

  const form = useForm<FormValues>({
    resolver: zodResolver(editing ? updateSchema : createSchema),
    defaultValues: { code: '', name: '', description: '' },
  });

  React.useEffect(() => {
    if (!open) return;
    form.reset({
      code: initial?.code ?? '',
      name: initial?.name ?? '',
      description: initial?.description ?? '',
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initial?.id]);

  const errors = form.formState.errors;
  const submitting = createMutation.isPending || updateMutation.isPending;

  const onSubmit = form.handleSubmit(async (values) => {
    try {
      if (editing && initial) {
        await updateMutation.mutateAsync({
          id: initial.id,
          name: values.name,
          description: values.description || null,
        });
        toast.success('Rol actualizado');
      } else {
        const role = await createMutation.mutateAsync({
          code: values.code,
          name: values.name,
          description: values.description || null,
          permission_codes: [],
        });
        toast.success('Rol creado');
        onCreated?.(role);
      }
      onOpenChange(false);
    } catch (err) {
      toast.error(extractErrorMessage(err, 'No se pudo guardar el rol'));
    }
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{editing ? 'Editar rol' : 'Nuevo rol'}</DialogTitle>
          <DialogDescription>
            {editing
              ? 'Actualiza el nombre y la descripción. El código no se modifica.'
              : 'Crea un rol personalizado. Luego asigna sus permisos en la matriz.'}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={onSubmit} noValidate className="space-y-4">
          {!editing && (
            <div className="space-y-2">
              <Label htmlFor="code">Código</Label>
              <Input
                id="code"
                autoFocus
                placeholder="ej. supervisor_taller"
                aria-invalid={errors.code ? true : undefined}
                className={cn(errors.code && 'border-destructive')}
                {...form.register('code')}
              />
              {errors.code && (
                <p role="alert" className="text-destructive text-xs font-medium">
                  {errors.code.message}
                </p>
              )}
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="name">Nombre</Label>
            <Input
              id="name"
              aria-invalid={errors.name ? true : undefined}
              className={cn(errors.name && 'border-destructive')}
              {...form.register('name')}
            />
            {errors.name && (
              <p role="alert" className="text-destructive text-xs font-medium">
                {errors.name.message}
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="description">Descripción</Label>
            <Input id="description" {...form.register('description')} />
          </div>

          <DialogFooter>
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
              {editing ? 'Guardar' : 'Crear rol'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
