import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Clave de idempotencia RFC 4122 v4 para las mutaciones con efecto externo
 * (alta de novedades, escalamiento de fallas a CloudFleet).
 *
 * `crypto.randomUUID()` sólo se expone en CONTEXTO SEGURO: por HTTP sobre una IP
 * —como se sirve el portal en el host de desarrollo— no existe, aunque el
 * navegador sea reciente. `getRandomValues` sí está disponible ahí, así que el
 * fallback arma el UUID a mano; `Math.random` es el último recurso.
 *
 * No usar esto para nada criptográfico: es un identificador de reintento.
 */
export function newIdempotencyKey(): string {
  const c = globalThis.crypto as Crypto | undefined;
  if (c && typeof c.randomUUID === 'function') {
    return c.randomUUID();
  }
  const bytes = new Uint8Array(16);
  if (c && typeof c.getRandomValues === 'function') {
    c.getRandomValues(bytes);
  } else {
    for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
  }
  // Versión 4, variant 10xx.
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0'));
  return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex
    .slice(6, 8)
    .join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10, 16).join('')}`;
}
