import type { CloudfleetStatus, NovedadListItem, NovedadPriority } from '@/lib/types';

/**
 * Etiquetas y lógica de estado de una novedad, compartidas por la lista, la
 * tarjeta móvil y el detalle. Sin React: se prueba en Node.
 *
 * Dos señales distintas conviven y NO se mezclan:
 * - `cloudfleet_status`: si el POST llegó a CloudFleet (Enviada/Pendiente/Fallida);
 * - `external_*`: lo que CloudFleet dice hoy de esa issue (resuelta o no), que
 *   el portal replica una vez al día.
 */

export const CLOUDFLEET_STATUS_META: Record<
  CloudfleetStatus,
  { label: string; variant: 'success' | 'warning' | 'destructive' }
> = {
  sent: { label: 'Enviada', variant: 'success' },
  pending: { label: 'Pendiente', variant: 'warning' },
  failed: { label: 'Fallida', variant: 'destructive' },
};

export const PRIORITY_LABEL: Record<NovedadPriority, string> = {
  low: 'Baja',
  medium: 'Media',
  high: 'Alta',
};

export function formatNovedadDate(
  value: string,
  style: 'short' | 'medium' | 'full' = 'medium',
): string {
  return new Intl.DateTimeFormat('es-CO', {
    dateStyle: style,
    timeStyle: 'short',
  }).format(new Date(value));
}

export function formatNovedadDay(value: string): string {
  return new Intl.DateTimeFormat('es-CO', { dateStyle: 'medium' }).format(new Date(value));
}

type ResolvableNovedad = Pick<
  NovedadListItem,
  'cloudfleet_status' | 'external_is_done' | 'external_done_at' | 'external_work_order_number'
>;

export type ExternalResolution =
  | { done: true; label: string; workOrder: number | null; doneAt: string | null }
  | { done: false; label: string }
  | null;

/**
 * `null` cuando no hay nada que decir: el backend aún no verificó la issue
 * (`external_is_done === null`) o el envío nunca llegó a CloudFleet, donde
 * hablar de "abierta" sería inventar un estado.
 */
export function externalResolution(novedad: ResolvableNovedad): ExternalResolution {
  if (novedad.external_is_done == null) return null;
  if (novedad.external_is_done) {
    return {
      done: true,
      label: 'Resuelta en CloudFleet',
      workOrder: novedad.external_work_order_number,
      doneAt: novedad.external_done_at,
    };
  }
  if (novedad.cloudfleet_status !== 'sent') return null;
  return { done: false, label: 'Abierta en CloudFleet' };
}

/** "Resuelta · OT 5619 · 21 ago 2026", con las partes que existan. */
export function resolutionSummary(resolution: ExternalResolution): string | null {
  if (!resolution) return null;
  if (!resolution.done) return resolution.label;
  const parts = ['Resuelta'];
  if (resolution.workOrder != null) parts.push(`OT ${resolution.workOrder}`);
  if (resolution.doneAt) parts.push(formatNovedadDay(resolution.doneAt));
  return parts.join(' · ');
}

/** "OT 5619 · 21 ago 2026": lo que acompaña al badge "Resuelta". */
export function resolutionDetail(resolution: ExternalResolution): string | null {
  if (!resolution?.done) return null;
  const parts: string[] = [];
  if (resolution.workOrder != null) parts.push(`OT ${resolution.workOrder}`);
  if (resolution.doneAt) parts.push(formatNovedadDay(resolution.doneAt));
  return parts.length ? parts.join(' · ') : null;
}

export type NovedadLifecycle = 'failed' | 'pending' | 'open' | 'resolved' | 'deleted';

export interface LifecycleMeta {
  code: NovedadLifecycle;
  label: string;
  variant: 'destructive' | 'warning' | 'info' | 'success';
  /** Texto secundario bajo el badge; `null` si no aporta nada. */
  detail: string | null;
}

/**
 * UN solo estado para quien reporta, en el orden en que ocurre:
 * Fallida → Pendiente → Abierta → Resuelta. Mezcla las dos señales del
 * backend (`cloudfleet_status`, el envío; `external_*`, la issue) en un ciclo
 * de vida legible. "Enviada" no es un estado para el usuario: es el detalle
 * técnico de que el POST llegó, y vive sólo en la sección Cloudfleet del detalle.
 *
 * Una novedad enviada pero aún no verificada por el sync se muestra "Abierta":
 * para quien la reportó sigue pendiente de atención hasta que CloudFleet diga
 * lo contrario.
 */
export function lifecycle(
  novedad: ResolvableNovedad &
    Pick<NovedadListItem, 'cloudfleet_issue_number' | 'external_deleted_at'>,
): LifecycleMeta {
  if (novedad.cloudfleet_status === 'failed') {
    return {
      code: 'failed',
      label: 'Fallida',
      variant: 'destructive',
      detail: 'No llegó a Cloudfleet',
    };
  }
  if (novedad.cloudfleet_status === 'pending') {
    return {
      code: 'pending',
      label: 'Pendiente',
      variant: 'warning',
      detail: 'Enviando a Cloudfleet',
    };
  }
  // Borrada en CloudFleet: va ANTES de la resolución porque una issue borrada
  // no está resuelta —nadie la atendió, dejó de existir— y una que llegó a
  // marcarse hecha antes de desaparecer se pintaría "Resuelta" con el orden
  // invertido. El sync lo replica; sin esto la lista la seguía llamando
  // "Abierta" y mandaba a perseguir algo que ya no está.
  if (novedad.external_deleted_at) {
    return {
      code: 'deleted',
      label: 'Borrada',
      variant: 'warning',
      detail: 'Ya no existe en Cloudfleet',
    };
  }
  const resolution = externalResolution(novedad);
  if (resolution?.done) {
    return {
      code: 'resolved',
      label: 'Resuelta',
      variant: 'success',
      detail: resolutionDetail(resolution),
    };
  }
  return {
    code: 'open',
    label: 'Abierta',
    variant: 'info',
    detail: novedad.cloudfleet_issue_number != null ? `#${novedad.cloudfleet_issue_number}` : null,
  };
}
