'use client';

import * as React from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { zodResolver } from '@hookform/resolvers/zod';
import { Eye, EyeOff, Loader2, LogIn } from 'lucide-react';
import { useForm } from 'react-hook-form';
import { toast } from 'sonner';
import { z } from 'zod';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useLogin } from '@/lib/auth';
import { landingPathFor, safeRedirectPath } from '@/lib/route-gate';
import { cn } from '@/lib/utils';

const loginSchema = z.object({
  // El login no debe revelar si el identificador tiene formato válido. El
  // backend trata cualquier valor no reconocido como credencial inválida.
  email: z.string().trim().min(1, 'Ingresa tu usuario'),
  password: z.string().min(1, 'Ingresa tu contraseña'),
});

type LoginValues = z.infer<typeof loginSchema>;

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const rawRedirect = params.get('redirect');
  // Sin `?redirect` el destino depende de los permisos de la sesión que
  // devuelve el login (ver `landingPathFor`), no de una constante.
  const redirect = safeRedirectPath(rawRedirect);

  const login = useLogin();
  const [showPassword, setShowPassword] = React.useState(false);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: '', password: '' },
  });

  const onSubmit = handleSubmit(async (values) => {
    try {
      const session = await login.mutateAsync(values);
      toast.success('Sesión iniciada');
      router.replace(
        redirect ??
          landingPathFor({
            permissions: session.permissions,
            isAdmin: session.user.roles.some((role) => role.code === 'admin'),
          }),
      );
      router.refresh();
    } catch (err) {
      const status = (err as { status?: number } | null)?.status;
      const message =
        status === 429
          ? 'Demasiados intentos. Espera un momento e inténtalo de nuevo.'
          : 'Usuario o contraseña incorrectos';
      toast.error(message);
    }
  });

  const disabled = isSubmitting || login.isPending;

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-5">
      <div className="space-y-2">
        <Label
          htmlFor="email"
          className="text-muted-foreground text-xs font-semibold uppercase tracking-wide"
        >
          Usuario o correo electrónico
        </Label>
        <Input
          id="email"
          type="text"
          autoComplete="username"
          placeholder="Ingresa tu usuario"
          aria-invalid={errors.email ? true : undefined}
          className={cn(errors.email && 'border-destructive focus-visible:border-destructive')}
          {...register('email')}
        />
        {errors.email && (
          <p role="alert" className="text-destructive text-xs font-medium">
            {errors.email.message}
          </p>
        )}
      </div>

      <div className="space-y-2">
        <Label
          htmlFor="password"
          className="text-muted-foreground text-xs font-semibold uppercase tracking-wide"
        >
          Contraseña
        </Label>
        <div className="relative">
          <Input
            id="password"
            type={showPassword ? 'text' : 'password'}
            autoComplete="current-password"
            placeholder="••••••••"
            aria-invalid={errors.password ? true : undefined}
            className={cn(
              'pr-10',
              errors.password && 'border-destructive focus-visible:border-destructive',
            )}
            {...register('password')}
          />
          <button
            type="button"
            onClick={() => setShowPassword((v) => !v)}
            aria-label={showPassword ? 'Ocultar contraseña' : 'Mostrar contraseña'}
            className="text-muted-foreground hover:text-foreground absolute right-3 top-1/2 -translate-y-1/2 transition-colors"
          >
            {showPassword ? (
              <EyeOff className="h-4 w-4" aria-hidden />
            ) : (
              <Eye className="h-4 w-4" aria-hidden />
            )}
          </button>
        </div>
        {errors.password && (
          <p role="alert" className="text-destructive text-xs font-medium">
            {errors.password.message}
          </p>
        )}
      </div>

      <Button type="submit" disabled={disabled} className="w-full" size="lg">
        {disabled ? (
          <>
            <Loader2 className="animate-spin" aria-hidden />
            Iniciando…
          </>
        ) : (
          <>
            <LogIn className="h-4 w-4" aria-hidden />
            Iniciar Sesión
          </>
        )}
      </Button>
    </form>
  );
}
