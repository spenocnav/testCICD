import { test, expect } from '@playwright/test';

/**
 * Flujo admin sobre el módulo Reportes (analytics de combustible):
 *   login (sesión reusada) → sidebar "Reportes" → verifica encabezado,
 *   KPI cards, tabla y filtros (vehículo + fechas).
 *
 * Requiere que el schema analytics.* esté poblado (loader de InformesRendimiento).
 * Si no hay datos, la tabla muestra "Sin resultados" y los asserts de estructura
 * (encabezado, columnas, KPIs) siguen siendo válidos.
 */
test.describe('Reportes — combustible', () => {
  test('navega a Reportes y muestra tabla con filtros', async ({ page }) => {
    // 1. Ir a Reportes desde el sidebar. Esperamos la respuesta del API de
    // combustible para que el assert de la tabla sea determinista (la ruta del
    // dev server puede compilar en frío en la primera visita).
    await page.goto('/inicio');
    const combustibleResp = page.waitForResponse(
      (r) => r.url().includes('/api/v1/reportes/combustible/daily') && r.status() === 200,
    );
    await page.getByRole('link', { name: 'Reportes' }).click();

    await expect(page).toHaveURL(/\/reportes/);
    await expect(page.getByRole('heading', { level: 1, name: 'Reportes' })).toBeVisible();
    await combustibleResp;

    // 2. KPI cards presentes.
    await expect(page.getByText('Registros', { exact: true })).toBeVisible();
    await expect(page.getByText('Combustible', { exact: true })).toBeVisible();
    await expect(page.getByText('km/gal prom.', { exact: true })).toBeVisible();

    // 3. Encabezados de la tabla (tras cargar los datos → branch !isLoading).
    const headers = page.locator('thead th');
    await expect(headers.filter({ hasText: 'Fecha' })).toBeVisible();
    await expect(headers.filter({ hasText: 'Placa' })).toBeVisible();
    await expect(headers.filter({ hasText: 'km/gal' }).first()).toBeVisible();
    await expect(headers.filter({ hasText: '% Ralentí' })).toBeVisible();
  });

  test('filtra por vehículo y por rango de fechas', async ({ page }) => {
    await page.goto('/reportes');
    await expect(page.getByRole('heading', { level: 1, name: 'Reportes' })).toBeVisible();

    const vehiculoSelect = page.getByLabel('Vehículo');
    await expect(vehiculoSelect).toBeVisible();

    // El selector debe traer vehículos del catálogo (dim_vehicle) además de "Todos".
    const optionCount = await vehiculoSelect.locator('option').count();
    expect(optionCount).toBeGreaterThan(1);

    // Seleccionar el primer vehículo real (índice 1; el 0 es "Todos").
    const firstValue = await vehiculoSelect.locator('option').nth(1).getAttribute('value');
    if (firstValue) {
      await vehiculoSelect.selectOption(firstValue);
      // La tabla recarga; el contador de registros debe seguir visible.
      await expect(page.getByText('Registros', { exact: true })).toBeVisible();
    }

    // Aplicar un rango de fechas amplio (no debe romper la vista).
    await page.getByLabel('Fecha desde').fill('2026-01-01');
    await page.getByLabel('Fecha hasta').fill('2026-12-31');

    // La sección de reportes sigue renderizada tras filtrar.
    await expect(page.getByRole('heading', { level: 1, name: 'Reportes' })).toBeVisible();
  });
});
