/**
 * URLs/configuración derivadas de variables de entorno.
 * Si NEXT_PUBLIC_API_URL está vacío, se usan llamadas same-origin
 * (que pasan por Next.js rewrites para llegar al backend).
 */
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, '') ?? '';
