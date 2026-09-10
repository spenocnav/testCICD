'use client';

import * as React from 'react';
import {
  Activity,
  AlertTriangle,
  Bell,
  CarFront,
  ClipboardList,
  DatabaseZap,
  FileBarChart,
  Home,
  ShieldCheck,
  Truck,
  Users,
} from 'lucide-react';

import { Can } from '@/components/auth/can';
import { NavLink } from '@/components/layout/nav-link';
import { cn } from '@/lib/utils';

interface SidebarItem {
  href: string;
  icon: typeof Home;
  label: string;
  /** Permiso requerido para mostrar el item (opcional). */
  permission?: string;
}

interface SidebarSection {
  title: string;
  items: SidebarItem[];
  /** Permisos que habilitan la sección; basta con uno. */
  permissions?: string[];
}

export const SECTIONS: SidebarSection[] = [
  {
    title: 'Generales',
    items: [{ href: '/inicio', icon: Home, label: 'Inicio' }],
  },
  {
    title: 'Operación',
    items: [
      { href: '/reportes', icon: FileBarChart, label: 'Reportes', permission: 'reportes.view' },
      { href: '/navifault', icon: AlertTriangle, label: 'Navifault', permission: 'navifault.view' },
      { href: '/vehiculos', icon: CarFront, label: 'Vehículos', permission: 'reportes.view' },
    ],
  },
  {
    title: 'Mantenimiento',
    items: [
      { href: '/novedades', icon: Bell, label: 'Novedades', permission: 'novedades.view' },
      {
        href: '/mantenimiento/informe',
        icon: ClipboardList,
        label: 'Informe Mtto',
        permission: 'mantenimiento.view',
      },
    ],
  },
  {
    title: 'Gestión',
    permissions: ['calidad_datos.view', 'users.view', 'roles.view', 'flotas.view'],
    items: [
      {
        href: '/gestion/calidad-datos',
        icon: DatabaseZap,
        label: 'Calidad de datos',
        permission: 'calidad_datos.view',
      },
      { href: '/gestion/usuarios', icon: Users, label: 'Usuarios', permission: 'users.view' },
      {
        href: '/gestion/roles',
        icon: ShieldCheck,
        label: 'Roles y permisos',
        permission: 'roles.view',
      },
      { href: '/gestion/flotas', icon: Truck, label: 'Flotas', permission: 'flotas.view' },
      {
        href: '/gestion/uso',
        icon: Activity,
        label: 'Uso del portal',
        // 'admin' no existe en el catálogo de permisos: solo lo satisface el
        // bypass del rol admin en useHasPermission. La pantalla es auditoría
        // de actividad de personas y el backend exige require_platform_admin.
        permission: 'admin',
      },
    ],
  },
];

interface SidebarNavProps {
  collapsed: boolean;
  /** Se dispara al tocar un enlace; el menú móvil lo usa para cerrarse. */
  onNavigate?: () => void;
  className?: string;
}

/** Secciones y enlaces del menú. Lo comparten la barra lateral de escritorio y
 *  el panel móvil, para que ambos muestren exactamente lo mismo. */
export function SidebarNav({ collapsed, onNavigate, className }: SidebarNavProps) {
  return (
    <nav className={cn('chat-scroll flex-1 overflow-y-auto px-2 py-4', className)}>
      {SECTIONS.map((section) => {
        const content = (
          <div className="mb-6">
            {!collapsed && (
              <p className="text-[var(--sidebar-section)]/70 mb-2 px-3 text-[11px] font-semibold uppercase tracking-wider">
                {section.title}
              </p>
            )}
            <div className="flex flex-col gap-1">
              {section.items.map((item) => {
                const { permission, ...navProps } = item;
                const link = (
                  <NavLink
                    key={item.href}
                    {...navProps}
                    collapsed={collapsed}
                    onClick={onNavigate}
                  />
                );
                return permission ? (
                  <Can key={item.href} permission={permission}>
                    {link}
                  </Can>
                ) : (
                  link
                );
              })}
            </div>
          </div>
        );

        return section.permissions ? (
          <Can key={section.title} permission={section.permissions} mode="any">
            {content}
          </Can>
        ) : (
          <React.Fragment key={section.title}>{content}</React.Fragment>
        );
      })}
    </nav>
  );
}
