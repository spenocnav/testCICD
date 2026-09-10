/**
 * Cliente HTTP centralizado. Maneja:
 * - Credenciales (cookies httpOnly) en cada request.
 * - Refresh automático en 401 (un único intento).
 * - Errores tipados via `ApiError`.
 */

import { API_BASE_URL } from '@/lib/env';
import type { ApiError } from '@/lib/types';

export const FLEET_HEADER = 'X-Fleet-Id';

/** Debe coincidir con `STORAGE_KEY` de `FleetProvider`. */
const FLEET_STORAGE_KEY = 'portal-clientes.selected-fleet';

/**
 * Flotas seleccionadas en el filtro global. Las setea `FleetProvider`; el cliente
 * las adjunta como header en cada request. `[]` se usa solo antes de que el
 * provider conozca las flotas; al hidratar, "Todas" se materializa con todos
 * los IDs de flota visibles, nunca como un alcance administrativo sin scoping.
 *
 * `null` = el provider aún no hidrató. Antes de eso (p. ej. al refrescar la
 * pestaña) leemos la selección persistida en `currentFleetIds()` para que el
 * primer fetch ya salga scopeado; si no, el efecto del provider corre DESPUÉS
 * de que los hijos disparan sus queries y la página traería toda la flota.
 */
let activeFleetIds: string[] | null = null;

export function setActiveFleetIds(ids: string[]): void {
  activeFleetIds = ids;
}

/**
 * ¿`FleetProvider` ya aplicó el alcance real de flota?
 *
 * Existe para las PRECARGAS: una consulta que se dispara por decisión nuestra
 * —no porque una pantalla la necesite— no puede salir antes de que el alcance
 * esté puesto. Antes de la hidratación, `currentFleetIds()` cae al valor
 * persistido en `localStorage`, que es suficiente para no dejar la primera
 * pantalla sin scoping, pero no es el alcance verificado: puede estar vacío o
 * ser el de una sesión anterior. Cachear una respuesta bajo esa suposición es
 * un defecto de datos, no de rendimiento, así que la precarga espera.
 */
export function isFleetScopeHydrated(): boolean {
  return activeFleetIds !== null;
}

/**
 * Limpia el scope de flota en memoria y la clave persistida.
 * Llamar en logout / cambio de sesión / errores de auth para que la siguiente
 * petición no salga con `X-Fleet-Id` de un usuario anterior.
 */
export function clearFleetScope(): void {
  activeFleetIds = null;
  if (typeof window !== 'undefined') {
    try {
      window.localStorage.removeItem(FLEET_STORAGE_KEY);
    } catch {
      // localStorage inaccesible: no hacer nada.
    }
  }
}

function currentFleetIds(): string[] {
  if (activeFleetIds !== null) return activeFleetIds;
  if (typeof window !== 'undefined') {
    try {
      const parsed: unknown = JSON.parse(window.localStorage.getItem(FLEET_STORAGE_KEY) ?? '[]');
      if (Array.isArray(parsed) && parsed.every((x) => typeof x === 'string')) {
        return parsed as string[];
      }
    } catch {
      // localStorage corrupto → sin scoping hasta que el provider hidrate.
    }
  }
  return [];
}

interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown;
  skipRefresh?: boolean;
  query?: Record<string, string | string[] | number | boolean | undefined | null>;
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  if (path.startsWith('http')) {
    return new URL(path).toString();
  }
  const base =
    API_BASE_URL ||
    (typeof window !== 'undefined' ? window.location.origin : 'http://localhost:3000');
  const url = new URL(path, base);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v === undefined || v === null || v === '') continue;
      if (Array.isArray(v)) {
        for (const item of v) {
          url.searchParams.append(k, item);
        }
      } else {
        url.searchParams.set(k, String(v));
      }
    }
  }
  return url.toString();
}

async function parseJson(response: Response): Promise<unknown> {
  if (response.status === 204) return null;
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

async function rawFetch<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { body, query, headers, skipRefresh, ...rest } = opts;
  void skipRefresh;
  const isFormData = typeof FormData !== 'undefined' && body instanceof FormData;

  const fleetIds = currentFleetIds();
  const init: RequestInit = {
    credentials: 'include',
    headers: {
      Accept: 'application/json',
      ...(body !== undefined && !isFormData && { 'Content-Type': 'application/json' }),
      ...(fleetIds.length > 0 && { [FLEET_HEADER]: fleetIds.join(',') }),
      ...headers,
    },
    ...rest,
  };
  if (body !== undefined) {
    init.body = isFormData ? body : JSON.stringify(body);
  }

  const response = await fetch(buildUrl(path, query), init);

  if (!response.ok) {
    const payload = await parseJson(response);
    const error: ApiError = {
      status: response.status,
      detail:
        typeof payload === 'object' && payload && 'detail' in payload
          ? (payload as { detail: unknown }).detail
          : payload,
    };
    throw error;
  }

  return (await parseJson(response)) as T;
}

/**
 * Promesa compartida de refresh. Mientras esté pendiente, TODAS las respuestas
 * 401 reentran y esperan el MISMO POST /api/v1/auth/refresh (single-flight).
 * Se descarta la promesa al resolverse (con éxito o error) para que un
 * próximo 401 pueda volver a refrescar si el backend lo necesita.
 */
let refreshInFlight: Promise<unknown> | null = null;

function runRefresh(): Promise<unknown> {
  if (refreshInFlight) return refreshInFlight;
  const p = rawFetch<unknown>('/api/v1/auth/refresh', {
    method: 'POST',
    skipRefresh: true,
  }).finally(() => {
    refreshInFlight = null;
  });
  refreshInFlight = p;
  return p;
}

export async function apiRequest<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  try {
    return await rawFetch<T>(path, opts);
  } catch (err) {
    const e = err as ApiError;
    if (e?.status === 401 && !opts.skipRefresh && !path.startsWith('/api/v1/auth/')) {
      // Intentar refresh y reintentar UNA sola vez. Varias 401 concurrentes
      // comparten la misma llamada a /refresh (single-flight); el endpoint
      // /api/v1/auth/* (login/refresh/logout) nunca entra en este bloque,
      // así no se genera un bucle de reintentos.
      try {
        await runRefresh();
      } catch {
        throw e;
      }
      return await rawFetch<T>(path, { ...opts, skipRefresh: true });
    }
    throw e;
  }
}

export const api = {
  get: <T>(path: string, opts?: RequestOptions) => apiRequest<T>(path, { ...opts, method: 'GET' }),
  post: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
    apiRequest<T>(path, { ...opts, method: 'POST', body }),
  patch: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
    apiRequest<T>(path, { ...opts, method: 'PATCH', body }),
  put: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
    apiRequest<T>(path, { ...opts, method: 'PUT', body }),
  delete: <T>(path: string, opts?: RequestOptions) =>
    apiRequest<T>(path, { ...opts, method: 'DELETE' }),
};

export function extractErrorMessage(err: unknown, fallback = 'Ocurrió un error'): string {
  if (err && typeof err === 'object' && 'detail' in err) {
    const detail = (err as ApiError).detail;
    if (typeof detail === 'string') return detail;
    if (
      Array.isArray(detail) &&
      detail.length &&
      typeof detail[0] === 'object' &&
      detail[0] &&
      'msg' in detail[0]
    ) {
      return String((detail[0] as { msg: string }).msg);
    }
    // Varios endpoints devuelven `{code, message}`: el código es para el
    // cliente y el mensaje para la persona. Sin esta rama el mensaje se perdía
    // y la pantalla mostraba su texto genérico, que no dice qué hacer.
    if (
      detail &&
      typeof detail === 'object' &&
      !Array.isArray(detail) &&
      'message' in detail &&
      typeof (detail as { message: unknown }).message === 'string'
    ) {
      return (detail as { message: string }).message;
    }
  }
  return fallback;
}
