import { Skeleton } from '@/components/ui/skeleton';

/**
 * Marcador del área de contenido mientras la ruta se resuelve.
 *
 * Lo usan los dos momentos en que antes no se pintaba nada: el `loading.tsx`
 * del grupo `(app)`, que cubre la carga del segmento al navegar, y la compuerta
 * de permisos, que esperaba a `/me` en blanco.
 *
 * Imita el esqueleto de las pantallas del portal —título, fila de tarjetas y un
 * bloque grande— para que el salto al contenido real no reacomode la página.
 */
export function PageLoadingSkeleton() {
  return (
    <div role="status" aria-busy="true" aria-live="polite" className="space-y-6">
      <span className="sr-only">Cargando la página…</span>

      <div className="space-y-2">
        <Skeleton className="h-7 w-56" />
        <Skeleton className="h-4 w-80" />
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-24" />
        ))}
      </div>

      <Skeleton className="h-72" />
    </div>
  );
}
