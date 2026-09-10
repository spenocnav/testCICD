import { test as setup, expect } from '@playwright/test';
import fs from 'node:fs';

/**
 * Loguea como admin una sola vez y persiste la sesión en disco.
 * El resto de specs reusan `.auth/admin.json` (cookies httpOnly) y no vuelven
 * a pegarle al login → evita el rate limit (5/min).
 */
const authFile = '.auth/admin.json';
const EMAIL = process.env.E2E_EMAIL ?? 'admin@portalclientes.local';
const PASSWORD = process.env.E2E_PASSWORD ?? 'ChangeMe123!';

setup('autenticar como admin', async ({ page }) => {
  await page.goto('/login');

  await page.getByLabel('Correo').fill(EMAIL);
  // getByLabel('Contraseña') es ambiguo: choca con el botón "Mostrar contraseña".
  // El input password expone role textbox; el botón es role button → desambigua.
  await page.getByRole('textbox', { name: 'Contraseña' }).fill(PASSWORD);
  await page.getByRole('button', { name: 'Iniciar sesión' }).click();

  // El login exitoso redirige a /inicio.
  await page.waitForURL('**/inicio');

  // Admin ve el módulo Roles y permisos en el sidebar → confirma sesión válida.
  await expect(page.getByRole('link', { name: 'Roles y permisos' })).toBeVisible();

  fs.mkdirSync('.auth', { recursive: true });
  await page.context().storageState({ path: authFile });
});
