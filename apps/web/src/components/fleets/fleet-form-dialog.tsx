'use client';

import * as React from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
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
import { useCreateFleet, useUpdateFleet } from '@/lib/fleets';
import type { Fleet } from '@/lib/types';
import { cn } from '@/lib/utils';

const schema = z.object({
  code: z.string().min(1, 'Ingresa el código').max(64),
  name: z.string().min(1, 'Ingresa el nombre').max(160),
  is_active: z.boolean(),
  ralenti_analysis_enabled: z.boolean(),
});

type Values = z.infer<typeof schema>;

interface FleetFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initial?: Fleet | null;
}

export function FleetFormDialog({ open, onOpenChange, initial }: FleetFormDialogProps) {
  const editing = !!initial;
  const createMutation = useCreateFleet();
  const updateMutation = useUpdateFleet();

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { code: '', name: '', is_active: true, ralenti_analysis_enabled: false },
  });

  React.useEffect(() => {
    if (!open) return;
    form.reset(
      initial
        ? {
            code: initial.code,
            name: initial.name,
            is_active: initial.is_active,
            ralenti_analysis_enabled: initial.ralenti_analysis_enabled ?? false,
          }
        : { code: '', name: '', is_active: true, ralenti_analysis_enabled: false },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initial?.id]);

  const errors = form.formState.errors;
  const submitting = createMutation.isPending || updateMutation.isPending;

  const onSubmit = form.handleSubmit(async (values) => {
    try {
      if (editing && initial) {
        await updateMutation.mutateAsync({ id: initial.id, ...values });
        toast.success('Flota actualizada');
      } else {
        // El alta no acepta el flag: se crea la flota y después se edita. Así el
        // contrato de creación sigue siendo el de la réplica de Navi Vehículos.
        const { ralenti_analysis_enabled, ...createValues } = values;
        const created = await createMutation.mutateAsync(createValues);
        if (ralenti_analysis_enabled) {
          await updateMutation.mutateAsync({ id: created.id, ralenti_analysis_enabled: true });
        }
        toast.success('Flota creada');
      }
      onOpenChange(false);
    } catch (err) {
      toast.error(extractErrorMessage(err, 'No se pudo guardar'));
    }
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{editing ? 'Editar flota' : 'Nueva flota'}</DialogTitle>
          <DialogDescription>
            {editing ? 'Actualiza los datos de la flota.' : 'Crea una flota asignable a usuarios.'}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={onSubmit} noValidate className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="code">Código</Label>
            <Input
              id="code"
              autoFocus
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

          <div className="flex items-center gap-2">
            <Checkbox
              id="fleet_active"
              checked={form.watch('is_active')}
              onCheckedChange={(v) => form.setValue('is_active', v === true)}
            />
            <Label htmlFor="fleet_active" className="cursor-pointer text-sm">
              Flota activa
            </Label>
          </div>

          <div className="flex items-start gap-2">
            <Checkbox
              id="fleet_ralenti"
              className="mt-0.5"
              checked={form.watch('ralenti_analysis_enabled')}
              onCheckedChange={(v) => form.setValue('ralenti_analysis_enabled', v === true)}
            />
            <div className="space-y-0.5">
              <Label htmlFor="fleet_ralenti" className="cursor-pointer text-sm">
                Análisis de Ralentí
              </Label>
              <p className="text-muted-foreground text-xs">
                Extrae los episodios de ralentí de la flota desde Geotab y habilita la pestaña
                &quot;Análisis Ralentí&quot; en Reportes.
              </p>
            </div>
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
              {editing ? 'Guardar cambios' : 'Crear flota'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
