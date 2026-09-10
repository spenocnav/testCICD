'use client';

import * as React from 'react';
import { usePathname, useRouter } from 'next/navigation';

import { FleetProvider } from '@/components/fleet/fleet-provider';
import { AppSidebar } from '@/components/layout/app-sidebar';
import { AppTopbar } from '@/components/layout/app-topbar';
import { MobileNav } from '@/components/layout/mobile-nav';
import { PageLoadingSkeleton } from '@/components/layout/page-loading-skeleton';
import { useMe } from '@/lib/auth';
import { requiredPermission, routeGateState } from '@/lib/route-gate';

const STORAGE_KEY = 'portal-clientes.sidebar.collapsed';

function RoutePermissionGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { data: me, isPending } = useMe();
  const permission = requiredPermission(pathname);
  const isAdmin = me?.user.roles.some((role) => role.code === 'admin') ?? false;
  const allowed = isAdmin || (permission ? (me?.permissions.includes(permission) ?? false) : true);

  React.useEffect(() => {
    if (me && permission && !allowed) router.replace('/inicio');
  }, [allowed, me, permission, router]);

  const state = routeGateState({ permission, isPending, hasSession: !!me, allowed });

  // Antes esta rama devolvía `null`: con la barra lateral y la superior ya
  // pintadas, el área de contenido quedaba vacía mientras `/me` estaba en
  // vuelo, sin nada que indicara que algo estaba cargando.
  if (state === 'loading') return <PageLoadingSkeleton />;
  if (state === 'denied') {
    return (
      <section className="border-destructive/30 bg-destructive/5 rounded-lg border p-8 text-center">
        <h1 className="text-lg font-bold">Sin permiso para acceder</h1>
        <p className="text-muted-foreground mt-1 text-sm">Volviendo al inicio…</p>
      </section>
    );
  }
  return children;
}

export function AppShell({ children }: { children: React.ReactNode }) {
  // `sidebarCollapsed` es sólo de escritorio y se persiste; el menú móvil es
  // un panel efímero que siempre abre expandido y no toca esa preferencia.
  const [sidebarCollapsed, setSidebarCollapsed] = React.useState(false);
  const [mobileNavOpen, setMobileNavOpen] = React.useState(false);
  const openMobileNav = React.useCallback(() => setMobileNavOpen(true), []);

  React.useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === '1') setSidebarCollapsed(true);
  }, []);

  const toggleSidebar = React.useCallback(() => {
    setSidebarCollapsed((collapsed) => {
      const next = !collapsed;
      window.localStorage.setItem(STORAGE_KEY, next ? '1' : '0');
      return next;
    });
  }, []);

  return (
    <FleetProvider>
      <div className="flex min-h-dvh">
        <AppSidebar collapsed={sidebarCollapsed} onToggle={toggleSidebar} />
        <MobileNav open={mobileNavOpen} onOpenChange={setMobileNavOpen} />
        <div className="flex min-w-0 flex-1 flex-col">
          <AppTopbar onOpenMenu={openMobileNav} menuOpen={mobileNavOpen} />
          <main
            className="flex-1 px-4 pb-[calc(1.5rem+env(safe-area-inset-bottom))] md:px-8 md:pb-8"
            style={{ paddingTop: 18 }}
          >
            <RoutePermissionGate>{children}</RoutePermissionGate>
          </main>
        </div>
      </div>
    </FleetProvider>
  );
}
