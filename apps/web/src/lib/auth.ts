import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api, clearFleetScope } from '@/lib/api-client';
import type { LoginResponse, MeResponse } from '@/lib/types';

export const ME_QUERY_KEY = ['auth', 'me'] as const;

export function useMe() {
  return useQuery({
    queryKey: ME_QUERY_KEY,
    queryFn: () => api.get<MeResponse>('/api/v1/me'),
    retry: false,
  });
}

export function useHasPermission(): (code: string) => boolean {
  const { data } = useMe();
  const permissions = data?.permissions ?? [];
  const isAdmin = data?.user.roles.some((role) => role.code === 'admin') ?? false;
  return (code: string) => isAdmin || permissions.includes(code);
}

/** ¿El usuario puede VER el módulo? (el backend ya expande edit→view). */
export function useCanView(): (moduleCode: string) => boolean {
  const has = useHasPermission();
  return (moduleCode: string) => has(`${moduleCode}.view`);
}

/** ¿El usuario puede EDITAR el módulo? */
export function useCanEdit(): (moduleCode: string) => boolean {
  const has = useHasPermission();
  return (moduleCode: string) => has(`${moduleCode}.edit`);
}

export async function loginRequest(email: string, password: string): Promise<LoginResponse> {
  return api.post<LoginResponse>('/api/v1/auth/login', { email, password });
}

export async function logoutRequest(): Promise<void> {
  await api.post('/api/v1/auth/logout');
}

export function useInvalidateMe() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: ME_QUERY_KEY });
}

interface LoginInput {
  email: string;
  password: string;
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ email, password }: LoginInput) => loginRequest(email, password),
    onSuccess: (data) => {
      // Aislamiento de sesión: vaciamos TODA la caché previa (datos del
      // usuario anterior, listas cacheadas, scope de flota en memoria y
      // localStorage) antes de sembrar el nuevo /me. Si no, la primera
      // navegación tras login puede mezclar respuestas del usuario previo.
      qc.clear();
      clearFleetScope();
      qc.setQueryData(ME_QUERY_KEY, data satisfies MeResponse);
    },
  });
}
