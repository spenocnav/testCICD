/**
 * Stub de observabilidad para el frontend.
 *
 * Si `NEXT_PUBLIC_SENTRY_DSN` está definido, importa dinámicamente
 * `@sentry/nextjs` y lo inicializa. Si no, no-op.
 *
 * El paquete `@sentry/nextjs` NO se instala por defecto para no engordar
 * el bundle local. Cuando se quiera activar:
 *   pnpm --filter web add @sentry/nextjs
 * y exportar `NEXT_PUBLIC_SENTRY_DSN`.
 */

export async function initFrontendObservability(): Promise<void> {
  const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
  if (!dsn) return;

  try {
    // @ts-expect-error: paquete opcional; instalar bajo demanda
    const Sentry = await import('@sentry/nextjs');
    Sentry.init({
      dsn,
      tracesSampleRate: 0,
      replaysOnErrorSampleRate: 0,
      replaysSessionSampleRate: 0,
    });
  } catch {
    // sentry-sdk no instalado — ignorar.
  }
}
