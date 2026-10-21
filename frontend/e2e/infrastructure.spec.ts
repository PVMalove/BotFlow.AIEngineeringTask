import { expect, test } from '@playwright/test';

test('serves the scaffold without exposing internal routes', async ({
  page,
}) => {
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'BotFlow', exact: true }),
  ).toBeVisible();
  await expect(page.getByText('Панель готовится к запуску.')).toBeVisible();
  const internal = await page.request.get('/internal/');
  expect(internal.status()).toBe(404);
});
