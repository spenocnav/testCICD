'use client';

import Image from 'next/image';
import * as React from 'react';
import { usePathname } from 'next/navigation';

import { SidebarNav } from '@/components/layout/sidebar-nav';
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet';
import { useMediaQuery } from '@/lib/use-media-query';

export const MOBILE_NAV_ID = 'mobile-nav';

interface MobileNavProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Menú para pantallas angostas: el mismo `SidebarNav` de escritorio dentro de
 * un panel lateral. Se cierra al tocar un enlace, al cambiar la ruta por
 * cualquier otro camino y al cruzar a escritorio (giro de tablet), donde la
 * barra lateral fija vuelve a existir y dos menús serían uno de más.
 */
export function MobileNav({ open, onOpenChange }: MobileNavProps) {
  const pathname = usePathname();
  const isDesktop = useMediaQuery('(min-width: 1024px)');
  const close = React.useCallback(() => onOpenChange(false), [onOpenChange]);

  React.useEffect(() => {
    close();
  }, [pathname, close]);

  React.useEffect(() => {
    if (isDesktop) close();
  }, [isDesktop, close]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="left"
        id={MOBILE_NAV_ID}
        aria-label="Menú principal"
        className="bg-sidebar p-0 lg:hidden"
      >
        <SheetTitle className="sr-only">Menú</SheetTitle>
        <SheetDescription className="sr-only">Navegación principal del portal.</SheetDescription>
        <div className="flex h-16 shrink-0 items-center border-b border-[var(--sidebar-border)] px-4">
          <Image
            src="/images/logo.svg"
            alt="Portal Clientes"
            width={146}
            height={45}
            priority
            className="h-full max-h-[45px] w-auto"
          />
        </div>
        <SidebarNav
          collapsed={false}
          onNavigate={close}
          className="pb-[env(safe-area-inset-bottom)]"
        />
      </SheetContent>
    </Sheet>
  );
}
