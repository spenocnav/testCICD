import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/lib/api-client';
import type { PaginatedUsers, RoleSummary, UserRead } from '@/lib/types';

interface RoleRead extends RoleSummary {
  description: string | null;
  is_system: boolean;
}

export interface UsersListParams {
  search?: string;
  role?: string;
  only_active?: boolean;
  include_archived?: boolean;
  limit?: number;
  offset?: number;
}

export interface CreateUserInput {
  email: string;
  password: string;
  full_name: string;
  role_codes: string[];
  fleet_ids?: string[];
  is_active?: boolean;
}

export interface UpdateUserInput {
  full_name?: string;
  is_active?: boolean;
  role_codes?: string[];
  fleet_ids?: string[];
  password?: string;
  is_archived?: boolean;
}

const USERS_KEY = ['users'] as const;
const ROLES_KEY = ['roles'] as const;

export function useUsers(params: UsersListParams) {
  return useQuery({
    queryKey: [...USERS_KEY, 'list', params],
    queryFn: () =>
      api.get<PaginatedUsers>('/api/v1/users', {
        query: {
          search: params.search,
          role: params.role,
          only_active: params.only_active,
          include_archived: params.include_archived,
          limit: params.limit ?? 20,
          offset: params.offset ?? 0,
        },
      }),
    placeholderData: (previous) => previous,
  });
}

export function useRoles(enabled = true) {
  return useQuery({
    queryKey: ROLES_KEY,
    queryFn: () => api.get<RoleRead[]>('/api/v1/roles'),
    staleTime: 5 * 60_000,
    enabled,
  });
}

export function useCreateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateUserInput) => api.post<UserRead>('/api/v1/users', input),
    onSuccess: () => qc.invalidateQueries({ queryKey: USERS_KEY }),
  });
}

export function useUpdateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...patch }: { id: string } & UpdateUserInput) =>
      api.patch<UserRead>(`/api/v1/users/${id}`, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: USERS_KEY }),
  });
}

export function useDeactivateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/api/v1/users/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: USERS_KEY }),
  });
}
