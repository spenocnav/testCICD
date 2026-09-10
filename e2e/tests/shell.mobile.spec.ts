import { test, expect, type Page } from '@playwright/test';

/**
 * El cascarón de la aplicación en un teléfono: la barra lateral fija no se
 * pinta, el menú vive en un panel que abre la hamburguesa, y ninguna de las
 * rutas de novedades desborda horizontalmente.
 */

async function noHorizontalOverflow(page: Page) {
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(scrollWidth, 'la página no debe desbordar horizontalmente').toBeLessThanOrEqual(
    innerWidth,
  );
}

test.describe('Shell en teléfono', () => {
  test('la barra lateral se esconde y el menú abre en un panel', async ({ page }) => {
    await page.goto('/novedades');
    await expect(page.getByRole('heading', { level: 1, name: 'Novedades' })).toBeVisible();

    // La barra lateral de escritorio no ocupa espacio en el teléfono.
    await expect(page.locator('aside[data-collapsed]')).toBeHidden();

    const menuButton = page.getByRole('button', { name: 'Abrir menú' });
    await expect(menuButton).toBeVisible();
    const box = await menuButton.boundingBox();
    expect(box?.width ?? 0).toBeGreaterThanOrEqual(44);
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);

    await menuButton.click();
    // Radix nombra el diálogo por su título (`aria-labelledby` gana a `aria-label`).
    const drawer = page.getByRole('dialog', { name: 'Menú' });
    await expect(drawer).toBeVisible();
    await expect(drawer.getByRole('link', { name: 'Novedades' })).toBeVisible();
    await page.screenshot({ path: 'test-results/mobile-drawer.png' });

    // Tocar un enlace navega y cierra el panel.
    await drawer.getByRole('link', { name: 'Inicio' }).click();
    await expect(page).toHaveURL(/\/inicio/);
    await expect(drawer).toBeHidden();
  });

  for (const path of ['/novedades', '/novedades/nuevo']) {
    test(`sin desborde horizontal en ${path}`, async ({ page }) => {
      await page.goto(path);
      // El diálogo de guía (si la ruta lo abre) deja el resto de la página
      // `aria-hidden`: hay que cerrarlo antes de buscar el encabezado.
      const entendido = page.getByRole('button', { name: 'Entendido' });
      await entendido
        .waitFor({ state: 'visible', timeout: 15_000 })
        .then(() => entendido.click())
        .catch(() => undefined);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      await noHorizontalOverflow(page);
    });
  }
});
