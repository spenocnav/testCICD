import { QueryClient } from '@tanstack/react-query';

/**
 * Los hechos de reportes los publica el ETL una vez al día (cadena de las 04:00
 * America/Bogota). Marcarlos rancios a los 30 s del default global no refleja
 * cómo cambian los datos y obliga a repetir agregaciones caras cada vez que el
 * usuario vuelve a una pestaña que ya había visitado.
 *
 * `gcTime` se mantiene por debajo de `staleTime` × 2 a propósito: la respuesta
 * de calificación ronda los 460 KiB con el detalle inline y un usuario que
 * explora periodos y estados genera decenas de claves distintas.
 *
 * Estos valores son defaults POR PREFIJO DE CLAVE: un hook que declare su
 * propio `staleTime` sigue ganando (`defaultQueryOptions` mezcla en el orden
 * global < por clave < por query).
 */
const REPORTES_STALE_TIME = 10 * 60_000;
const REPORTES_GC_TIME = 15 * 60_000;

export function createQueryClient(): QueryClient {
  const client = new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => {
          const status = (error as { status?: number } | null)?.status;
          if (status && status >= 400 && status < 500) return false;
          return failureCount < 2;
        },
      },
      mutations: {
        retry: false,
      },
    },
  });

  client.setQueryDefaults(['reportes'], {
    staleTime: REPORTES_STALE_TIME,
    gcTime: REPORTES_GC_TIME,
  });

  return client;
}
