'use client';

import Image from 'next/image';
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react';

import { SidebarNav } from '@/components/layout/sidebar-nav';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface AppSidebarProps {
  collapsed: boolean;
  onToggle: () => void;
}

/** Barra lateral de escritorio. Bajo `lg` no se pinta: ahí el menú vive en el
 *  panel móvil (`MobileNav`), abierto desde la barra superior. */
export function AppSidebar({ collapsed, onToggle }: AppSidebarProps) {
  return (
    <aside
      data-collapsed={collapsed}
      className={cn(
        'bg-sidebar sticky top-0 hidden h-dvh shrink-0 flex-col border-r border-[var(--sidebar-border)] transition-[width] duration-200 lg:flex',
        collapsed ? 'w-[64px]' : 'w-[240px]',
      )}
    >
      <div
        className={cn(
          'flex h-16 items-center border-b border-[var(--sidebar-border)] px-4',
          collapsed && 'justify-center px-0',
        )}
      >
        {!collapsed ? (
          <>
            <Image
              src="/images/logo.svg"
              alt="Portal Clientes"
              width={146}
              height={45}
              priority
              className="h-full max-h-[45px] w-auto"
            />
            <div className="ml-auto">
              <Button variant="ghost" size="icon" aria-label="Colapsar sidebar" onClick={onToggle}>
                <PanelLeftClose />
              </Button>
            </div>
          </>
        ) : (
          <button
            onClick={onToggle}
            className="group flex h-10 w-10 items-center justify-center rounded-md transition-colors hover:bg-[var(--sidebar-item-hover-bg)]"
            aria-label="Expandir sidebar"
          >
            <Image
              src="/favicon.png"
              alt="Portal Clientes"
              width={32}
              height={32}
              priority
              className="h-8 w-8 brightness-[0.35] transition-opacity group-hover:opacity-0"
            />
            <PanelLeftOpen className="absolute h-5 w-5 text-[var(--sidebar-text)] opacity-0 transition-opacity group-hover:opacity-100" />
          </button>
        )}
      </div>

      <SidebarNav collapsed={collapsed} />
    </aside>
  );
}
