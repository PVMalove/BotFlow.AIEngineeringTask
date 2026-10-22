import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

import { fixture, loginCode } from './browser-fixture';

async function login(page: Page, code: string) {
  await page.goto('/login');
  await page.getByLabel('Код из Telegram').fill(code);
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
}

async function checkRequestList(response: import('@playwright/test').Response) {
  expect(
    response.status(),
    `Join request list returned HTTP ${response.status()}`,
  ).toBe(200);
  expect(
    await response.json(),
    'The real join request must retain the applicant name',
  ).toContainEqual(
    expect.objectContaining({
      telegram_id: 4200000008,
      display_name: 'Анна / Աննա',
      status: 'pending',
    }),
  );
}

test.beforeEach(() => {
  fixture('reset');
});

test('manages real join decisions and revokes and restores a regular user access', async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(120_000);
  fixture('join');
  await login(page, loginCode());
  const listResponse = page.waitForResponse(
    (response) =>
      response.url().includes('/api/v1/admin/join-requests?') &&
      new URL(response.url()).searchParams.get('status') === 'pending',
  );
  await page.getByRole('link', { name: 'Управление доступом' }).click();
  await checkRequestList(await listResponse);
  const applicant = page
    .getByRole('listitem')
    .filter({ hasText: 'Анна / Աննա' });
  await expect(applicant).toContainText('Ожидает решения');
  await applicant
    .getByRole('button', { name: 'Отклонить', exact: true })
    .click();
  await expect(applicant).toHaveCount(0);
  await page.getByRole('tab', { name: /^Отклонённые \(/ }).click();
  await expect(applicant).toContainText('Отклонён');

  fixture('join');
  await page.getByRole('tab', { name: /^Заявки \(/ }).click();
  await expect(applicant).toContainText('Ожидает решения');
  await applicant
    .getByRole('button', { name: 'Одобрить', exact: true })
    .click();
  await expect(applicant).toHaveCount(0);
  await page.getByRole('tab', { name: /^Участники \(/ }).click();
  await expect(applicant).toContainText('Допущен');

  const userContext = await browser.newContext({ baseURL });
  try {
    const userPage = await userContext.newPage();
    await login(userPage, loginCode('user-code'));
    await userPage.goto('/admin');
    await expect(userPage).toHaveURL(/\/$/);
    await expect(
      userPage.getByRole('link', { name: 'Управление доступом' }),
    ).toHaveCount(0);
    const members = await page.request.get('/api/v1/admin/members');
    const target = (await members.json()).find(
      (member: { telegram_id: number }) => member.telegram_id === 4200000008,
    );
    expect(target.status).toBe('approved');
    expect(
      (await userPage.request.get('/api/v1/admin/join-requests')).status(),
    ).toBe(403);
    expect((await userPage.request.get('/api/v1/admin/members')).status()).toBe(
      403,
    );
    const denied = await userPage.request.post(
      `/api/v1/admin/members/${target.id}/block`,
      {
        headers: { Origin: new URL(baseURL!).origin },
        data: { expected_version: target.access_version },
      },
    );
    expect(denied.status()).toBe(403);

    await applicant
      .getByRole('button', { name: 'Заблокировать', exact: true })
      .click();
    await expect(applicant).toContainText('Заблокирован');
    expect((await userPage.request.get('/api/v1/users/me')).status()).toBe(401);
    await userPage.reload();
    await expect(userPage).toHaveURL(/\/login$/);

    await page.getByRole('button', { name: 'Հայերեն', exact: true }).click();
    await expect(applicant).toContainText('Արգելափակված');
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <=
          document.documentElement.clientWidth,
      ),
    ).toBe(true);
    await page.reload();
    await expect(
      page.getByRole('heading', { name: 'Մուտքի կառավարում' }),
    ).toBeVisible();
    await page.getByRole('tab', { name: /^Մասնակիցներ \(/ }).click();
    await applicant
      .getByRole('button', { name: 'Ապաարգելափակել', exact: true })
      .click();
    await expect(applicant).toContainText('Մուտքը թույլատրված է');
    await login(userPage, loginCode('user-code'));
    expect((await userPage.request.get('/api/v1/users/me')).status()).toBe(200);
    await page.getByRole('button', { name: 'Русский', exact: true }).click();
    await expect(applicant).toContainText('Допущен');
  } finally {
    await userContext.close();
  }
});

test('rereads a real concurrent decision without overwriting it', async ({
  page,
  baseURL,
}) => {
  test.setTimeout(60_000);
  fixture('join');
  await login(page, loginCode());
  const listResponse = page.waitForResponse(
    (response) =>
      response.url().includes('/api/v1/admin/join-requests?') &&
      new URL(response.url()).searchParams.get('status') === 'pending',
  );
  await page.goto('/admin');
  await checkRequestList(await listResponse);
  const applicant = page
    .getByRole('listitem')
    .filter({ hasText: 'Анна / Աննա' });
  await expect(applicant).toBeVisible();
  const response = await page.request.get('/api/v1/admin/join-requests');
  const target = (await response.json()).find(
    (member: { telegram_id: number }) => member.telegram_id === 4200000008,
  );
  const concurrent = await page.request.post(
    `/api/v1/admin/members/${target.id}/reject`,
    {
      headers: { Origin: new URL(baseURL!).origin },
      data: { expected_version: target.access_version },
    },
  );
  expect(concurrent.status()).toBe(200);
  await applicant
    .getByRole('button', { name: 'Одобрить', exact: true })
    .click();
  await expect(page.getByRole('alert')).toContainText('Данные изменились');
  await expect(applicant).toHaveCount(0);
  await page.getByRole('tab', { name: /^Отклонённые \(/ }).click();
  await expect(applicant).toContainText('Отклонён');
});
