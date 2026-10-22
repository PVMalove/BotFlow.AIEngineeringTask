import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill({ status: 401, json: { detail: 'unauthenticated' } }),
  );
});

test('switches Russian to Armenian, restores it on reload and returns to Russian', async ({
  page,
}) => {
  await page.goto('/');
  await expect(page.locator('html')).toHaveAttribute('lang', 'ru');
  await expect(page.getByText('Вход в BotFlow')).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Русский', exact: true }),
  ).toHaveAttribute('aria-pressed', 'true');

  await page.getByRole('button', { name: 'Հայերեն', exact: true }).click();

  await expect(page.locator('html')).toHaveAttribute('lang', 'hy');
  await expect(page.getByText('Մուտք BotFlow')).toBeVisible();
  await expect(page.getByText('Вход в BotFlow')).toHaveCount(0);

  await page.reload();

  await expect(page.locator('html')).toHaveAttribute('lang', 'hy');
  await expect(page.getByText('Մուտք BotFlow')).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Հայերեն', exact: true }),
  ).toHaveAttribute('aria-pressed', 'true');

  await page.getByRole('button', { name: 'Русский', exact: true }).click();
  await page.reload();

  await expect(page.locator('html')).toHaveAttribute('lang', 'ru');
  await expect(page.getByText('Вход в BotFlow')).toBeVisible();
});

for (const language of ['Русский', 'Հայերեն']) {
  test(`keeps ${language} readable with local fonts and keyboard access`, async ({
    page,
    baseURL,
  }) => {
    if (!baseURL) throw new Error('A baseURL is required for shell checks.');
    const appOrigin = new URL(baseURL).origin;
    const externalRequests: string[] = [];
    page.on('request', (request) => {
      if (new URL(request.url()).origin !== appOrigin) {
        externalRequests.push(request.url());
      }
    });
    await page.goto('/');
    await page.getByRole('button', { name: language, exact: true }).click();

    await expect(page.getByRole('banner')).toBeVisible();
    await expect(page.getByRole('main')).toBeVisible();
    await expect(
      page.getByRole('heading', { name: 'BotFlow', exact: true }),
    ).toBeVisible();
    const layout = await page.evaluate(async () => {
      await document.fonts.ready;
      const montserrat = await document.fonts.load(
        '400 16px Montserrat',
        'Русский',
      );
      const armenian = await document.fonts.load(
        '400 16px "Noto Sans Armenian"',
        'Հայերեն',
      );
      return {
        width: document.documentElement.clientWidth,
        contentWidth: document.documentElement.scrollWidth,
        fontsLoaded: montserrat.length > 0 && armenian.length > 0,
        clipped: Array.from(
          document.querySelectorAll('button, h1, h2, p'),
        ).some((element) => element.scrollWidth > element.clientWidth + 1),
      };
    });

    expect(layout.contentWidth).toBeLessThanOrEqual(layout.width);
    expect(layout.clipped).toBe(false);
    expect(layout.fontsLoaded).toBe(true);
    expect(externalRequests).toEqual([]);

    await page.reload();
    const russian = page.getByRole('button', { name: 'Русский', exact: true });
    await expect(russian).toBeVisible();
    await page.keyboard.press('Tab');

    await expect(russian).toBeFocused();
    await expect(russian).toHaveCSS('outline-style', 'solid');
    await page.keyboard.press('ArrowRight');
    await expect(
      page.getByRole('button', { name: 'Հայերեն', exact: true }),
    ).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.locator('html')).toHaveAttribute('lang', 'hy');
  });
}
