import { defineConfig, devices } from '@playwright/test';

/**
 * App accesible por IP Tailscale en dev (web Next.js en :3000).
 * Override con E2E_BASE_URL si corres contra localhost u otro host.
 */
const baseURL = process.env.E2E_BASE_URL ?? 'http://100.108.186.115:3000';

// Las specs `*.mobile.spec.ts` corren sólo en los proyectos móviles; las demás
// sólo en escritorio. Así una spec no se ejecuta dos veces por error.
const MOBILE_SPECS = /\.mobile\.spec\.ts$/;

export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  // Login tiene rate limit (5/min). Serial evita agotarlo entre workers.
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL,
    // 'on' para ver SIEMPRE en server sin pantalla: trace (timeline con
    // snapshots del DOM por paso) y video de la corrida. Bajar a
    // 'on-first-retry'/'retain-on-failure' si molesta el peso en CI.
    trace: 'on',
    screenshot: 'only-on-failure',
    video: 'on',
  },
  projects: [
    // Proyecto "setup": loguea una sola vez y guarda la sesión (cookies httpOnly).
    // `next dev` compila /login e /inicio al primer acceso y puede tardar
    // minutos en este host (SEC-033); el login no puede medirse con 30 s.
    { name: 'setup', testMatch: /auth\.setup\.ts/, timeout: 180_000 },
    {
      name: 'chromium',
      testIgnore: MOBILE_SPECS,
      use: {
        ...devices['Desktop Chrome'],
        storageState: '.auth/admin.json',
      },
      dependencies: ['setup'],
    },
    {
      // Teléfono Android de referencia (393×851, táctil, DPR 2.75). El host
      // sirve `next dev` y compila cada ruta al primer acceso (SEC-033), por
      // eso el timeout es el doble que en escritorio.
      name: 'mobile-chrome',
      testMatch: MOBILE_SPECS,
      timeout: 120_000,
      expect: { timeout: 30_000 },
      use: {
        ...devices['Pixel 5'],
        storageState: '.auth/admin.json',
      },
      dependencies: ['setup'],
    },
  ],
});
