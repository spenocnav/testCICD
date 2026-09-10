'use client';

import * as React from 'react';

import { useHasPermission } from '@/lib/auth';

interface CanProps {
  permission: string | string[];
  mode?: 'any' | 'all';
  fallback?: React.ReactNode;
  children: React.ReactNode;
}

/**
 * Renderiza `children` solo si el usuario tiene el permiso (o todos/uno de ellos).
 */
export function Can({ permission, mode = 'all', fallback = null, children }: CanProps) {
  const hasPermission = useHasPermission();
  const required = Array.isArray(permission) ? permission : [permission];
  const ok =
    mode === 'all'
      ? required.every((p) => hasPermission(p))
      : required.some((p) => hasPermission(p));
  return <>{ok ? children : fallback}</>;
}
