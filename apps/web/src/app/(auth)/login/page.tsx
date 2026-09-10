import { Suspense } from 'react';

import { LoginForm } from '@/components/auth/login-form';

export const metadata = { title: 'Iniciar sesión · Portal Clientes' };

export default function LoginPage() {
  return (
    <div className="bg-card shadow-soft border-border w-full max-w-md rounded-2xl border p-8 sm:p-10">
      <div className="mb-8 flex flex-col items-center text-center">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/images/logo.svg" alt="Navitrans" className="h-14 w-auto" />
        <p className="text-muted-foreground mt-4 text-sm">Tu punto de acceso a la información</p>
      </div>

      <Suspense fallback={null}>
        <LoginForm />
      </Suspense>

      <p className="text-muted-foreground mt-8 text-center text-xs">
        Navitrans S.A.S. © {new Date().getFullYear()}
      </p>
    </div>
  );
}
