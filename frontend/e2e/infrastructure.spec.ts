import { expect, test } from '@playwright/test';

test('requires login without exposing internal routes', async ({ page }) => {
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'BotFlow', exact: true }),
  ).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);
  await expect(
    page.getByRole('heading', { name: 'Вход в BotFlow' }),
  ).toBeVisible();
  const internal = await page.request.get('/internal/');
  expect(internal.status()).toBe(404);
});
