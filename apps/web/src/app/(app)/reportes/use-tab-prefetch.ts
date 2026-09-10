'use client';

import * as React from 'react';
import { type QueryClient, useIsFetching, useQueryClient } from '@tanstack/react-query';

import { isFleetScopeHydrated } from '@/lib/api-client';
import {
  FALLAS_REPORT_SCOPE,
  type ReportesFilters,
  calificacionQueryOptions,
  combustibleSummaryQueryOptions,
  faultSummaryQueryOptions,
  habitoSummaryQueryOptions,
  operativosTimeseriesQueryOptions,
  ralentiSummaryQueryOptions,
} from '@/lib/reportes';
import type { FuelKind, Granularity } from '@/lib/types';

export type TabKey =
  | 'calificacion'
  | 'combustible'
  | 'operativos'
  | 'ralenti'
  | 'habitos'
  | 'fallas';

/** Orden del barrido en reposo. Coincide con el orden visual de la barra. */
const TAB_ORDER: TabKey[] = [
  'combustible',
  'operativos',
  'ralenti',
  'habitos',
  'calificacion',
  'fallas',
];

/**
 * Retardo del disparador por intención. Los cinco botones están pegados: barrer
 * la barra con el mouse los atraviesa todos y sin retardo se dispararían cinco
 * consultas caras "de paso". 120 ms es más de lo que dura un barrido y menos de
 * lo que tarda una persona en decidir el clic.
 */
const HOVER_DELAY_MS = 120;

/** Techo del `requestIdleCallback`: si el hilo nunca se desocupa, igual corre. */
const IDLE_TIMEOUT_MS = 2_000;

/** Reserva para Safari, que no implementa `requestIdleCallback`. */
const IDLE_FALLBACK_MS = 300;

/**
 * Cuánto tiempo seguido tiene que estar quieta la pantalla antes de barrer.
 *
 * Sin esto, cada cambio de filtro relanza el barrido en cuanto la pestaña
 * activa termina: alguien explorando filtros generaría cuatro consultas de
 * fondo por cada pausa, con claves nuevas cada vez —así que la caché no las
 * ahorra— compitiendo justo con lo que está mirando. La precarga es para el
 * usuario que dejó de tocar la pantalla, no para el que la está usando.
 */
const SWEEP_SETTLE_MS = 1_500;

/**
 * Todo lo que hace falta para reconstruir, DESDE FUERA de la pestaña, la
 * primera consulta que ella pedirá al montarse.
 */
export interface ReportesPrefetchContext {
  /** Filtros globales, tal cual los recibe cada pestaña por props. */
  filters: ReportesFilters;
  granularity: Granularity;
  /** Unidad de combustible activa; entra en los filtros de Combustible. */
  fuelKind: FuelKind;
  /**
   * Filtros con los que montará Calificación. NO son los globales: al elegir
   * esa pestaña, `selectTab` le fija granularidad mensual y el rango de los
   * últimos tres meses, así que la clave que pedirá es otra. Los calcula
   * `reportes-client.tsx` con la misma función que usa `selectTab`.
   */
  calificacionFilters: ReportesFilters;
}

/**
 * La consulta que se precarga por pestaña: UNA, la que domina el tiempo de la
 * primera pintada. No se replican las seis o siete de cada pestaña; el objetivo
 * es que al cambiar de pestaña haya algo en pantalla, no calentar la caché
 * entera a costa de la API (un solo worker uvicorn, pool 5+5).
 *
 * Las tablas de detalle de Hábitos seguros (`habitos/events`) y de Fallas
 * (`fallas/events`) quedan deliberadamente fuera aunque sean las consultas más
 * caras del módulo: ambas montan con `enabled: false`
 * (`showDetail` / `isHistoryExpanded` arrancan en `false`), así que NO se piden
 * al montar la pestaña. Precargarlas sería trabajo que nadie iba a pedir.
 *
 * El `prefetchQuery` se hace DENTRO de cada rama, no sobre un objeto devuelto
 * por el `switch`: las cinco factorías tienen tipos de respuesta distintos y
 * una unión de `queryOptions` no es asignable al parámetro genérico de
 * `prefetchQuery`. Cada rama, con su tipo concreto, sí lo es.
 *
 * `prefetchQuery` no rechaza: se traga el error, así que una precarga fallida
 * no pinta nada, no propaga y la pestaña volverá a intentarlo al montarse.
 */
function prefetchTab(
  queryClient: QueryClient,
  tab: TabKey,
  ctx: ReportesPrefetchContext,
): Promise<void> {
  switch (tab) {
    case 'combustible':
      // `combustible-tab.tsx`: `consumerFilters = applyCross(fuelFilters, cross)`
      // y `fuelFilters = { ...filters, fuel_kind: fuelKind }`. Al montar, `cross`
      // siempre está vacío —`reportes-client` lo resetea en cada cambio de
      // pestaña— y `applyCross(base, {})` devuelve `{ ...base }`.
      return queryClient.prefetchQuery(
        combustibleSummaryQueryOptions({ ...ctx.filters, fuel_kind: ctx.fuelKind }),
      );
    case 'operativos':
      // `operativos-tab.tsx`: `useOperativosTimeseries(filters, granularity)`,
      // sin estado local de por medio.
      return queryClient.prefetchQuery(
        operativosTimeseriesQueryOptions(ctx.filters, ctx.granularity),
      );
    case 'ralenti':
      // `ralenti-tab.tsx`: `useRalentiSummary(filters)` con los filtros globales
      // tal cual. Los cross-filters de rango (`duration_bucket`, `rpm_bucket`)
      // NO entran al resumen a propósito, así que la clave coincide siempre.
      return queryClient.prefetchQuery(ralentiSummaryQueryOptions(ctx.filters));
    case 'habitos':
      // `habitos-tab.tsx`: `habFilters` añade `event_type` y `rpm_threshold`,
      // que al montar valen `undefined` (`eventType = ''`, `rpmMode = null`), y
      // `vehicle_id: scopedVehicleIds` que sin `vehicleFilter` es
      // `filters.vehicle_id`. `hashKey` omite las claves `undefined`, así que
      // ese objeto y `filters` producen la misma clave.
      return queryClient.prefetchQuery(habitoSummaryQueryOptions(ctx.filters));
    case 'calificacion':
      return queryClient.prefetchQuery(calificacionQueryOptions(ctx.calificacionFilters));
    case 'fallas':
      // `fallas-tab.tsx`: mismo razonamiento que Hábitos; `severity = ''` y
      // `paretoFilter = null` dejan `faultFilters` equivalente a `filters`.
      // `FALLAS_REPORT_SCOPE` sí entra siempre: el tab lo pone en sus cinco
      // objetos de filtros, así que sin él la clave precargada NUNCA sería la
      // que pide el montaje. Se importa la misma constante en vez de repetir
      // el literal, que es como estas dos definiciones se desincronizan.
      return queryClient.prefetchQuery(
        faultSummaryQueryOptions({ ...ctx.filters, ...FALLAS_REPORT_SCOPE }),
      );
  }
}

/**
 * Precarga por intención de las pestañas de Reportes.
 *
 * Dos disparadores:
 *
 * 1. **Intención explícita**: hover o foco de teclado sobre el botón de una
 *    pestaña, con `HOVER_DELAY_MS` de retardo que se cancela al salir.
 * 2. **Reposo**: cuando la pestaña activa terminó de cargar, precarga las demás
 *    de a UNA por vez, en serie, en huecos de `requestIdleCallback`.
 *
 * Dos garantías que no se pueden relajar:
 *
 * - **Nunca antes de que el alcance de flota esté hidratado.** El header
 *   `X-Fleet-Id` sale de una variable de módulo de `api-client` que
 *   `FleetProvider` escribe en un efecto. Una precarga anterior a eso guardaría
 *   en caché la respuesta de un alcance que no es el del usuario. Se comprueba
 *   con `isFleetScopeHydrated()` en el momento del disparo, no en el render.
 * - **Nunca compitiendo con la pestaña que se está mirando.** El barrido en
 *   reposo exige que no quede ninguna consulta de `['reportes']` con observador
 *   montado en vuelo. El filtro `type: 'active'` es lo que hace posible esa
 *   cuenta: una consulta precargada no tiene observador, así que no se cuenta a
 *   sí misma y el barrido no se cancela solo al arrancar.
 */
export function useReportesTabPrefetch(args: {
  activeTab: TabKey;
  filters: ReportesFilters;
  granularity: Granularity;
  fuelKind: FuelKind;
  calificacionFilters: ReportesFilters;
  /**
   * Pestañas que la barra muestra. El barrido sólo recorre estas: precargar una
   * pestaña que el alcance no ofrece (p. ej. `ralenti` sin el módulo contratado)
   * sería una consulta que nadie va a pedir y, peor, un 4xx de fondo. Por
   * defecto, todas.
   */
  availableTabs?: readonly TabKey[];
}) {
  const { activeTab, filters, granularity, fuelKind, calificacionFilters, availableTabs } = args;
  const queryClient = useQueryClient();

  // Clave estable para que el efecto del barrido no se relance por una lista
  // nueva con el mismo contenido en cada render del padre.
  const availableKey = (availableTabs ?? TAB_ORDER).join(',');

  // Solo las consultas con observador montado: las precargas no se cuentan.
  const activeFetching = useIsFetching({ queryKey: ['reportes'], type: 'active' });

  const ctx = React.useMemo<ReportesPrefetchContext>(
    () => ({ filters, granularity, fuelKind, calificacionFilters }),
    [filters, granularity, fuelKind, calificacionFilters],
  );

  // Los temporizadores leen el contexto vigente al dispararse, no el del render
  // que los programó: entre el hover y los 120 ms el usuario pudo cambiar un
  // filtro, y precargar la clave vieja sería una petición desperdiciada.
  const latest = React.useRef({ activeTab, ctx });
  React.useEffect(() => {
    latest.current = { activeTab, ctx };
  }, [activeTab, ctx]);

  const hoverTimer = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  const cancelHoverPrefetch = React.useCallback(() => {
    if (hoverTimer.current !== null) {
      clearTimeout(hoverTimer.current);
      hoverTimer.current = null;
    }
  }, []);

  const scheduleHoverPrefetch = React.useCallback(
    (tab: TabKey) => {
      cancelHoverPrefetch();
      hoverTimer.current = setTimeout(() => {
        hoverTimer.current = null;
        const current = latest.current;
        if (tab === current.activeTab) return;
        if (!isFleetScopeHydrated()) return;
        void prefetchTab(queryClient, tab, current.ctx);
      }, HOVER_DELAY_MS);
    },
    [cancelHoverPrefetch, queryClient],
  );

  React.useEffect(() => cancelHoverPrefetch, [cancelHoverPrefetch]);

  // Barrido en reposo. Arranca solo cuando la pestaña activa ya se resolvió por
  // completo, de modo que el alcance de flota no solo está hidratado sino
  // aplicado: si el primer lote hubiera salido sin él, `FleetProvider` lo
  // habría invalidado y el refetch mantendría `activeFetching > 0`.
  React.useEffect(() => {
    if (activeFetching > 0) return;
    if (!isFleetScopeHydrated()) return;

    // Estado de TODO lo que la pantalla tiene montado, no de una clave concreta:
    // así no hay que reconstruir aquí la clave de la pestaña activa —que es
    // justo lo que puede divergir— y además cubre sus seis o siete consultas,
    // no solo la principal.
    const mounted = queryClient.getQueryCache().findAll({ queryKey: ['reportes'], type: 'active' });

    // Al menos una respuesta buena ya llegó (hay datos en pantalla) y ninguna
    // quedó marcada como rancia. Lo segundo es la garantía de alcance: si el
    // primer lote salió antes de que `FleetProvider` hidratara, ese lote está
    // invalidado y su refetch todavía no terminó.
    const settled =
      mounted.some((query) => query.state.status === 'success') &&
      mounted.every((query) => !query.state.isInvalidated && query.state.fetchStatus === 'idle');
    if (!settled) return;

    let cancelled = false;
    let idleHandle: number | null = null;
    let timeoutHandle: ReturnType<typeof setTimeout> | null = null;
    let settleHandle: ReturnType<typeof setTimeout> | null = null;

    const schedule = (run: () => void) => {
      if (typeof window.requestIdleCallback === 'function') {
        idleHandle = window.requestIdleCallback(() => run(), { timeout: IDLE_TIMEOUT_MS });
      } else {
        timeoutHandle = setTimeout(run, IDLE_FALLBACK_MS);
      }
    };

    const available = new Set(availableKey.split(',') as TabKey[]);
    const pending = TAB_ORDER.filter((tab) => tab !== activeTab && available.has(tab));

    // En serie y de a una: el servidor de la API es un solo worker y cuatro
    // agregaciones simultáneas se estorban entre ellas más de lo que adelantan.
    const step = () => {
      if (cancelled) return;
      const next = pending.shift();
      if (!next) return;
      void prefetchTab(queryClient, next, ctx).then(() => {
        if (!cancelled) schedule(step);
      });
    };

    settleHandle = setTimeout(() => {
      settleHandle = null;
      schedule(step);
    }, SWEEP_SETTLE_MS);

    return () => {
      cancelled = true;
      if (settleHandle !== null) clearTimeout(settleHandle);
      if (idleHandle !== null && typeof window.cancelIdleCallback === 'function') {
        window.cancelIdleCallback(idleHandle);
      }
      if (timeoutHandle !== null) clearTimeout(timeoutHandle);
    };
  }, [activeFetching, activeTab, availableKey, ctx, queryClient]);

  // `cancelled` frena el ENCADENAMIENTO del barrido, pero no la consulta que ya
  // salió: una precarga no tiene observador, así que nada la aborta sola y
  // seguiría ocupando una conexión para una clave que el usuario acaba de dejar
  // atrás. Al cambiar los filtros se abortan esas huérfanas; `type: 'inactive'`
  // alcanza exactamente a las precargas, porque lo que la pantalla tiene montado
  // sí tiene observador, y la señal que los `queryFn` propagan es lo que hace
  // que el aborto llegue hasta `fetch`.
  //
  // Va atado a `ctx` y NO a la limpieza del barrido a propósito. Esa limpieza
  // corre también al cambiar de pestaña, y ahí existe una ventana en la que la
  // pestaña recién elegida todavía no suscribió su observador: cancelar en ese
  // momento mataría justo la precarga que el usuario acaba de pedir con el clic.
  const isFirstCtx = React.useRef(true);
  React.useEffect(() => {
    if (isFirstCtx.current) {
      isFirstCtx.current = false;
      return;
    }
    void queryClient.cancelQueries({ queryKey: ['reportes'], type: 'inactive' });
  }, [ctx, queryClient]);

  /** Props del botón de una pestaña que disparan la precarga por intención. */
  const tabPrefetchHandlers = React.useCallback(
    (tab: TabKey) => ({
      onMouseEnter: () => scheduleHoverPrefetch(tab),
      onMouseLeave: cancelHoverPrefetch,
      onFocus: () => scheduleHoverPrefetch(tab),
      onBlur: cancelHoverPrefetch,
    }),
    [scheduleHoverPrefetch, cancelHoverPrefetch],
  );

  return { tabPrefetchHandlers };
}
