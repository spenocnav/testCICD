import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/lib/api-client';
import type { ModuleRead, Permission, RoleDetail } from '@/lib/types';

const ROLES_KEY = ['roles'] as const;
const MODULES_KEY = ['modules'] as const;
const PERMISSIONS_KEY = ['permissions'] as const;

export interface CreateRoleInput {
  code: string;
  name: string;
  description?: string | null;
  permission_codes: string[];
}

export interface UpdateRoleInput {
  name?: string;
  description?: string | null;
}

/** Roles con su matriz de permisos. Comparte key ['roles'] con useRoles(). */
export function useRolesDetail() {
  return useQuery({
    queryKey: ROLES_KEY,
    queryFn: () => api.get<RoleDetail[]>('/api/v1/roles'),
    staleTime: 60_000,
  });
}

export function useModules() {
  return useQuery({
    queryKey: MODULES_KEY,
    queryFn: () => api.get<ModuleRead[]>('/api/v1/modules'),
    staleTime: 5 * 60_000,
  });
}

export function usePermissions() {
  return useQuery({
    queryKey: PERMISSIONS_KEY,
    queryFn: () => api.get<Permission[]>('/api/v1/permissions'),
    staleTime: 5 * 60_000,
  });
}

function invalidateRoles(qc: ReturnType<typeof useQueryClient>) {
  return qc.invalidateQueries({ queryKey: ROLES_KEY });
}

export function useCreateRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateRoleInput) => api.post<RoleDetail>('/api/v1/roles', input),
    onSuccess: () => invalidateRoles(qc),
  });
}

export function useUpdateRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...patch }: { id: string } & UpdateRoleInput) =>
      api.patch<RoleDetail>(`/api/v1/roles/${id}`, patch),
    onSuccess: () => invalidateRoles(qc),
  });
}

export function useSetRolePermissions() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, permission_codes }: { id: string; permission_codes: string[] }) =>
      api.put<RoleDetail>(`/api/v1/roles/${id}/permissions`, { permission_codes }),
    onSuccess: () => invalidateRoles(qc),
  });
}

export function useDeleteRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/api/v1/roles/${id}`),
    onSuccess: () => invalidateRoles(qc),
  });
}
