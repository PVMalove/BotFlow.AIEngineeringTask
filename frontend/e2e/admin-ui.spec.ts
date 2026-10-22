import { expect, test } from '@playwright/test';

const admin = {
  id: 'admin',
  telegram_id: 4200000001,
  role: 'admin',
  status: 'approved',
};
const applicant = {
  id: '85a66d7a-744f-4bd3-b4ca-7cb2a9c632c7',
  telegram_id: 4200000002,
  display_name: 'Анна / Աննա',
  role: 'user',
  status: 'pending',
  access_version: 1,
  created_at: '2026-10-20T10:00:00Z',
  status_changed_at: null,
};

test('approves a request with its observed version and refreshes the members list', async ({
  page,
}) => {
  let approved = false;
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill({ json: admin }),
  );
  await page.route('**/api/v1/admin/join-requests?*', (route) =>
    route.fulfill({ json: approved ? [] : [applicant] }),
  );
  await page.route('**/api/v1/admin/members?*', (route) =>
    route.fulfill({
      json: approved
        ? [{ ...applicant, status: 'approved', access_version: 2 }]
        : [],
    }),
  );
  await page.route(
    `**/api/v1/admin/members/${applicant.id}/approve`,
    async (route) => {
      expect(route.request().method()).toBe('POST');
      expect(route.request().postDataJSON()).toEqual({ expected_version: 1 });
      approved = true;
      await route.fulfill({
        json: {
          changed: true,
          member: { ...applicant, status: 'approved', access_version: 2 },
        },
      });
    },
  );

  await page.goto('/');
  await page.getByRole('link', { name: 'Управление доступом' }).click();
  await expect(page.getByText(applicant.display_name)).toBeVisible();
  await page.getByRole('button', { name: 'Հայերեն', exact: true }).click();
  await expect(page.getByText(/Հայտի ամսաթիվը:/)).not.toHaveText(/[а-яё]/i);
  await page.getByRole('button', { name: 'Русский', exact: true }).click();
  await page.getByRole('button', { name: 'Одобрить', exact: true }).click();

  await expect(page.getByText('Решение сохранено.')).toBeVisible();
  await expect(page.getByText('Заявок нет.')).toBeVisible();
  await page.getByRole('tab', { name: 'Участники', exact: true }).click();
  await expect(page.getByText(applicant.display_name)).toBeVisible();
  await expect(page.getByText('Допущен', { exact: true })).toBeVisible();
});

test('denies the admin route to a regular user without requesting admin data', async ({
  page,
}) => {
  const adminRequests: string[] = [];
  page.on('request', (request) => {
    if (request.url().includes('/api/v1/admin/'))
      adminRequests.push(request.url());
  });
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill({ json: { ...admin, role: 'user' } }),
  );
  await page.goto('/admin');

  await expect(page).toHaveURL(/\/$/);
  await expect(
    page.getByRole('heading', { name: 'Командная доска' }),
  ).toBeVisible();
  await expect(
    page.getByRole('link', { name: 'Управление доступом' }),
  ).toHaveCount(0);
  expect(adminRequests).toEqual([]);
});

for (const { status, detail, expectedRu, expectedHy } of [
  {
    status: 409,
    detail: 'version_conflict',
    expectedRu: 'Данные изменились',
    expectedHy: 'Տվյալները փոխվել են',
  },
  {
    status: 503,
    detail: 'private-server-details',
    expectedRu: 'Результат действия не подтверждён',
    expectedHy: 'Գործողության արդյունքը հաստատված չէ',
  },
]) {
  test(`rereads after ${status} without repeating a decision and localizes the error`, async ({
    page,
  }) => {
    let decided = false;
    let posts = 0;
    await page.route('**/api/v1/users/me', (route) =>
      route.fulfill({ json: admin }),
    );
    await page.route('**/api/v1/admin/join-requests?*', (route) =>
      route.fulfill({ json: decided ? [] : [applicant] }),
    );
    await page.route(
      `**/api/v1/admin/members/${applicant.id}/approve`,
      async (route) => {
        posts += 1;
        decided = true;
        await route.fulfill({
          status,
          json: { detail },
        });
      },
    );
    await page.goto('/admin');
    await page.getByRole('button', { name: 'Одобрить', exact: true }).click();

    await expect(page.getByRole('alert')).toContainText(expectedRu);
    await expect(page.getByText('Заявок нет.')).toBeVisible();
    await page.getByRole('button', { name: 'Հայերեն', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText(expectedHy);
    await expect(page.getByText('private-server-details')).toHaveCount(0);
    expect(posts).toBe(1);
  });
}

test('hides stale decisions when reconciliation fails and allows a manual list refresh', async ({
  page,
}) => {
  let failed = false;
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill({ json: admin }),
  );
  await page.route('**/api/v1/admin/join-requests?*', (route) =>
    route.fulfill(failed ? { status: 503 } : { json: [applicant] }),
  );
  await page.route(
    `**/api/v1/admin/members/${applicant.id}/approve`,
    async (route) => {
      failed = true;
      await route.abort();
    },
  );
  await page.goto('/admin');
  await page.getByRole('button', { name: 'Одобрить', exact: true }).click();
  await expect(
    page.getByText(
      'Не удалось обновить список. Проверьте связь и перечитайте данные.',
    ),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Одобрить', exact: true }),
  ).toHaveCount(0);

  failed = false;
  await page
    .getByRole('button', { name: 'Обновить список', exact: true })
    .click();
  await expect(
    page.getByRole('button', { name: 'Одобрить', exact: true }),
  ).toBeEnabled();
});

test('explains an already applied decision and preserves Armenian on reload without resending it', async ({
  page,
}) => {
  let posts = 0;
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill({ json: admin }),
  );
  await page.route('**/api/v1/admin/join-requests?*', (route) =>
    route.fulfill({ json: posts ? [] : [applicant] }),
  );
  await page.route(
    `**/api/v1/admin/members/${applicant.id}/reject`,
    async (route) => {
      posts += 1;
      await route.fulfill({
        json: { changed: false, member: { ...applicant, status: 'rejected' } },
      });
    },
  );
  await page.goto('/admin');
  await page.getByRole('button', { name: 'Отклонить', exact: true }).click();
  await expect(page.getByRole('alert')).toHaveText(
    'Такое решение уже принято. Данные перечитаны.',
  );
  await page.getByRole('button', { name: 'Հայերեն', exact: true }).click();
  await expect(page.getByRole('alert')).toHaveText(
    'Այս որոշումն արդեն ընդունված է։ Տվյալները կրկին բեռնված են։',
  );
  await page.reload();
  await expect(
    page.getByRole('heading', { name: 'Մուտքի կառավարում' }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth,
    ),
  ).toBe(true);
  await page.getByRole('button', { name: 'Русский', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Управление доступом' }),
  ).toBeVisible();
  expect(posts).toBe(1);
});

test('pages requests and disables decisions until a pending response is reconciled', async ({
  page,
}) => {
  const firstPage = Array.from({ length: 20 }, (_, i) => ({
    ...applicant,
    id: `request-${i}`,
    display_name: `Applicant ${i}`,
  }));
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill({ json: admin }),
  );
  await page.route('**/api/v1/admin/join-requests?*', (route) => {
    const url = new URL(route.request().url());
    return route.fulfill({
      json: url.searchParams.get('offset') === '0' ? firstPage : [applicant],
    });
  });
  let finish!: () => void;
  const pending = new Promise<void>((resolve) => {
    finish = resolve;
  });
  await page.route(
    `**/api/v1/admin/members/${applicant.id}/block`,
    async (route) => {
      await pending;
      await route.fulfill({
        json: { changed: true, member: { ...applicant, status: 'blocked' } },
      });
    },
  );
  await page.goto('/admin');
  await expect(page.getByText('Applicant 19', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Далее', exact: true }).click();
  await expect(page.getByText(applicant.display_name)).toBeVisible();
  await page
    .getByRole('button', { name: 'Заблокировать', exact: true })
    .click();
  await expect(
    page.getByRole('button', { name: 'Одобрить', exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole('button', { name: 'Назад', exact: true }),
  ).toBeDisabled();
  finish();
  await expect(page.getByText('Решение сохранено.')).toBeVisible();
  await page.getByRole('button', { name: 'Назад', exact: true }).click();
  await expect(page.getByText('Applicant 0', { exact: true })).toBeVisible();
});
