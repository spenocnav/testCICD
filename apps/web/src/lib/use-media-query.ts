'use client';

import * as React from 'react';

/**
 * `true` cuando el `matchMedia(query)` del navegador casa.
 *
 * Con `useSyncExternalStore` el valor del servidor es `false` y el cliente lo
 * corrige en la hidratación sin efectos ni estado intermedio. La maquetación
 * responsive va por clases de Tailwind; este hook queda para lo poco que no
 * se puede expresar en CSS, como cerrar el menú móvil al cruzar a escritorio.
 */
export function useMediaQuery(query: string): boolean {
  const subscribe = React.useCallback(
    (onChange: () => void) => {
      const media = window.matchMedia(query);
      media.addEventListener('change', onChange);
      return () => media.removeEventListener('change', onChange);
    },
    [query],
  );
  return React.useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => false,
  );
}
