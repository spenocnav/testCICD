import { expect, test } from '@playwright/test';

// Administración de flotas: lista, detalle (bases geotab + credenciales +
// reglas), toggle de vehículo y dialog de detalle. Usa la flota demo
// "Transportes El Roble" (scripts/seed_demo_master_data.py).
test.describe('Flotas — administración', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/gestion/flotas');
    await page.getByRole('link', { name: 'Transportes El Roble' }).click();
    await expect(page.getByRole('heading', { name: /Transportes El Roble/ })).toBeVisible();
  });

  test('detalle muestra métricas, bases geotab y credenciales sin password', async ({ page }) => {
    // Métricas
    await expect(page.getByText('Vehículos activos')).toBeVisible();
    await expect(page.getByText('Credenciales', { exact: false }).first()).toBeVisible();

    // Bases geotab con su database_key
    await expect(page.getByRole('heading', { name: 'el_roble_sa' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'roble_refrigerados' })).toBeVisible();

    // Credenciales: username visible, password enmascarado, nunca en claro
    await expect(page.getByText('reportes@navitrans.com.co').first()).toBeVisible();
    await expect(page.getByText('••••••••').first()).toBeVisible();
    await expect(page.locator('body')).not.toContainText('demo-password-no-real');

    // Reglas por categoría
    await expect(page.getByText('Operación').first()).toBeVisible();
    await expect(page.getByText('Hábitos seguros').first()).toBeVisible();
    await expect(page.getByText('Frenada brusca').first()).toBeVisible();
  });

  test('tabla de vehículos con estado geotab y dialog de detalle', async ({ page }) => {
    // Estados de vínculo geotab presentes
    await expect(page.getByText('Vinculado').first()).toBeVisible();
    await expect(page.getByText('No encontrado').first()).toBeVisible();
    await expect(page.getByText('Sin verificar').first()).toBeVisible();

    // Dialog de detalle con la ficha completa
    await page.getByRole('button', { name: 'GQT318', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByText('3HSDJAPR1KN123456')).toBeVisible(); // VIN
    await expect(dialog.getByText('INTERNATIONAL')).toBeVisible();
    await expect(dialog.getByText('el_roble_sa')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).not.toBeVisible();
  });

  test('activar/desactivar vehículo (idempotente)', async ({ page }) => {
    // Desactivar y reactivar TRC587 para dejar el estado como estaba.
    await page.getByRole('button', { name: 'Desactivar TRC587' }).click();
    await expect(page.getByText('Vehículo "TRC587" desactivado')).toBeVisible();

    await page.getByRole('button', { name: 'Activar TRC587' }).click();
    await expect(page.getByText('Vehículo "TRC587" activado')).toBeVisible();
  });
});
