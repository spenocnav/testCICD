import path from 'node:path';

import { test, expect } from '@playwright/test';

/**
 * Novedades desde un teléfono: lista en tarjetas, filtros plegados, detalle con
 * las evidencias primero y visor a pantalla completa, formulario con cámara y
 * botón de envío siempre visible.
 *
 * No envía novedades: cada envío crea una issue real en CloudFleet. Se puede
 * habilitar con `E2E_ALLOW_WRITES=1` en un entorno que lo tolere.
 */

const FIXTURE = path.join(__dirname, '..', 'fixtures', 'evidence.png');

test.describe('Novedades en teléfono', () => {
  test('la lista se pinta en tarjetas y los filtros se pliegan', async ({ page }) => {
    await page.goto('/novedades');
    await expect(page.getByRole('heading', { level: 1, name: 'Novedades' })).toBeVisible();

    // La tabla es de escritorio; en el teléfono no se ve.
    await expect(page.locator('table')).toBeHidden();

    // Los filtros arrancan plegados y se despliegan con el botón.
    const dateFrom = page.locator('#novedades-date-from');
    await expect(dateFrom).toBeHidden();
    await page.getByRole('button', { name: /^Filtros/ }).click();
    await expect(dateFrom).toBeVisible();

    // Abiertas por defecto: es lo que hay que atender.
    const segments = page.getByRole('group', { name: 'Resolución' });
    await expect(segments.getByRole('button', { name: 'Abiertas' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );

    const cards = page.getByTestId('novedad-card');
    const empty = page.getByText(/No hay novedades/).first();
    await expect(cards.first().or(empty)).toBeVisible();
    await page.screenshot({ path: 'test-results/mobile-novedades-list.png', fullPage: true });
  });

  test('el detalle muestra las evidencias antes que CloudFleet y el visor llena la pantalla', async ({
    page,
  }) => {
    await page.goto('/novedades');
    await page.getByRole('group', { name: 'Resolución' }).getByRole('button', { name: 'Todas' }).click();
    const cards = page.getByTestId('novedad-card');
    const empty = page.getByText(/No hay novedades/).first();
    await expect(cards.first().or(empty)).toBeVisible();
    const count = await cards.count();
    test.skip(count === 0, 'no hay novedades en el alcance para abrir');

    await cards.first().click();
    // `next dev` compiló `/novedades/[id]` en 77 s la primera vez (SEC-033).
    await expect(page).toHaveURL(/\/novedades\/[0-9a-f-]{36}$/, { timeout: 90_000 });

    const evidencias = page.getByRole('heading', { level: 2, name: 'Evidencias' });
    const cloudfleet = page.getByRole('heading', { level: 2, name: 'Cloudfleet' });
    await expect(evidencias).toBeVisible();
    await expect(cloudfleet).toBeVisible();
    const evidenciasBox = await evidencias.boundingBox();
    const cloudfleetBox = await cloudfleet.boundingBox();
    expect(evidenciasBox!.y).toBeLessThan(cloudfleetBox!.y);

    // El JSON técnico va plegado: no compite con lo que el conductor busca.
    await expect(page.locator('details > summary')).toHaveCount(1);
    await page.screenshot({ path: 'test-results/mobile-novedad-detalle.png', fullPage: true });

    // Sólo el panel de evidencias: el `<aside>` de la barra lateral de
    // escritorio también existe (oculto) y tiene botones.
    const thumbnails = page.locator('aside').filter({ hasText: 'Evidencias' }).locator('button');
    if ((await thumbnails.count()) > 0) {
      await thumbnails.first().click();
      const viewer = page.getByRole('dialog');
      await expect(viewer).toBeVisible();
      const viewport = page.viewportSize()!;
      // La apertura anima `zoom-in-95`: se mide cuando la animación terminó.
      await expect
        .poll(async () => (await viewer.boundingBox())?.width ?? 0)
        .toBeGreaterThanOrEqual(viewport.width - 1);
      await page.screenshot({ path: 'test-results/mobile-visor.png' });
      await page.getByRole('button', { name: 'Cerrar' }).click();
      await expect(viewer).toBeHidden();
    }
  });

  test('el formulario ofrece la cámara y el envío queda a la vista', async ({ page }) => {
    await page.goto('/novedades/nuevo');

    const guidance = page.getByRole('dialog');
    await expect(guidance).toBeVisible();
    const viewport = page.viewportSize()!;
    const guidanceBox = await guidance.boundingBox();
    expect(guidanceBox!.width).toBeLessThanOrEqual(viewport.width);
    await page.getByRole('button', { name: 'Entendido' }).click();
    await expect(guidance).toBeHidden();

    // Cámara: entrada aparte, sólo fotos, con `capture`.
    const camera = page.getByTestId('evidence-camera-input');
    await expect(camera).toHaveAttribute('capture', 'environment');
    await expect(camera).not.toHaveAttribute('accept', /video/);

    // Galería/archivos: la entrada múltiple de siempre.
    const files = page.getByTestId('evidence-file-input');
    await files.setInputFiles(FIXTURE);
    const remove = page.getByRole('button', { name: 'Quitar evidence.png' });
    await expect(remove).toBeVisible();
    const removeBox = await remove.boundingBox();
    expect(removeBox!.width).toBeGreaterThanOrEqual(40);

    // El botón de envío está en el viewport sin desplazar la página: pie fijo.
    const submit = page.getByRole('button', { name: /^Registrar novedad/ });
    await expect(submit).toBeInViewport();
    await page.screenshot({ path: 'test-results/mobile-novedad-nuevo.png', fullPage: true });

    // No se envía: crearía una issue real en CloudFleet. Con
    // E2E_ALLOW_WRITES=1 se podría completar el flujo en un entorno que lo tolere.
    if (process.env.E2E_ALLOW_WRITES !== '1') return;
    await submit.click();
    await expect(page).toHaveURL(/\/novedades(\/[0-9a-f-]{36})?$/, { timeout: 60_000 });
  });
});
