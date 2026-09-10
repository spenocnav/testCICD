import { test, expect } from '@playwright/test';

/**
 * Flujo de usuario admin:
 *   login (sesión reusada) → sidebar "Roles y permisos" → rol "Visor"
 *   → activar "Ver" del módulo "Roles y permisos" → Guardar → confirmar + persistir.
 */
test.describe('Roles y permisos — rol Visor', () => {
  test('activa "Ver" del módulo Roles y permisos para el rol Visor', async ({ page }) => {
    // 1. Navegar a Roles y permisos desde el sidebar.
    await page.goto('/inicio');
    await page.getByRole('link', { name: 'Roles y permisos' }).click();

    await expect(page).toHaveURL(/\/gestion\/roles/);
    await expect(
      page.getByRole('heading', { level: 1, name: 'Roles y permisos' }),
    ).toBeVisible();

    // 2. Seleccionar el rol Visor en la lista.
    await page.getByRole('button', { name: 'Visor' }).click();
    await expect(page.getByRole('heading', { level: 2, name: 'Visor' })).toBeVisible();

    const verRoles = page.getByRole('checkbox', {
      name: 'Ver Roles y permisos',
      exact: true,
    });
    const guardar = page.getByRole('button', { name: 'Guardar permisos' });

    // El botón "Guardar" solo se habilita si hay cambios (form dirty).
    // Para que el flujo corra determinista en cada ejecución, normalizamos:
    // si ya estaba activado, lo desactivamos y guardamos primero (baseline OFF).
    if (await verRoles.isChecked()) {
      await verRoles.uncheck();
      await guardar.click();
      await expect(page.getByText('Permisos de "Visor" actualizados')).toBeVisible();
      await expect(verRoles).not.toBeChecked();
    }

    // 3. Activar el checkbox "Ver" del módulo "Roles y permisos".
    await verRoles.check();
    await expect(verRoles).toBeChecked();

    // 4. Guardar permisos (ahora sí hay cambios → botón habilitado).
    await expect(guardar).toBeEnabled();
    await guardar.click();

    // 5. Confirmación (toast sonner).
    await expect(page.getByText('Permisos de "Visor" actualizados')).toBeVisible();

    // 6. Persistencia: recargar y verificar que el permiso quedó guardado.
    await page.reload();
    await page.getByRole('button', { name: 'Visor' }).click();
    await expect(
      page.getByRole('checkbox', { name: 'Ver Roles y permisos', exact: true }),
    ).toBeChecked();
  });
});
