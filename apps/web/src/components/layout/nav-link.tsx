'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { LucideIcon } from 'lucide-react';

import { cn } from '@/lib/utils';

export interface NavLinkProps {
  href: string;
  icon: LucideIcon;
  label: string;
  collapsed?: boolean;
  onClick?: () => void;
}

export function NavLink({ href, icon: Icon, label, collapsed, onClick }: NavLinkProps) {
  const pathname = usePathname();
  const active = pathname === href || pathname.startsWith(`${href}/`);

  return (
    <Link
      href={href}
      onClick={onClick}
      aria-current={active ? 'page' : undefined}
      className={cn(
        // `min-h-11` da 44 px de área táctil en el menú móvil; en escritorio
        // (`lg`) la fila conserva su altura compacta.
        'group flex min-h-11 items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors lg:min-h-0',
        'text-brand-gray hover:bg-[var(--sidebar-item-hover)]',
        active &&
          'bg-[var(--sidebar-item-active-bg)] text-[var(--sidebar-item-active-fg)] hover:bg-[var(--sidebar-item-active-bg)]',
        collapsed && 'justify-center px-2',
      )}
      title={collapsed ? label : undefined}
    >
      <Icon
        className={cn('h-4 w-4 shrink-0', active && 'text-[var(--sidebar-item-active-fg)]')}
        aria-hidden
      />
      {!collapsed && <span className="truncate">{label}</span>}
    </Link>
  );
}
