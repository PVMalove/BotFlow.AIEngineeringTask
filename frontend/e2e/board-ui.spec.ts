import { expect, test } from '@playwright/test';

const member = {
  id: 'owner',
  telegram_id: 4200000001,
  role: 'user',
  status: 'approved',
};
const card = {
  id: 'task',
  text: 'Текст / Տեքստ\n' + 'длинный'.repeat(40),
  author_name: 'Анна / Աննա',
  author_id: 'owner',
  version: 1,
  status: 'pending',
  created_at: '2026-01-02T03:04:00Z',
  updated_at: '2026-01-02T03:04:00Z',
};

test('shows loading, independent errors, empty columns and localized retry without translating content', async ({
  page,
}) => {
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill({ json: { data: member, meta: {} } }),
  );
  let fail = true;
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/v1/tasks?**', async (route) => {
    const status = new URL(route.request().url()).searchParams.get('status');
    if (status === 'pending') await gate;
    if (status === 'in_progress' && fail)
      return route.fulfill({ status: 503, json: {} });
    await route.fulfill({
      json: {
        data: status === 'pending' ? [card] : [],
        meta: { has_more: false, next_cursor: null },
      },
    });
  });
  await page.goto('/');
  await expect(
    page
      .getByRole('region', { name: 'Ожидает', exact: true })
      .getByText('Загружаем задачи…'),
  ).toBeVisible();
  await expect(
    page.getByRole('region', { name: 'В работе' }).getByRole('alert'),
  ).toBeVisible();
  await expect(page.getByRole('region', { name: 'Завершено' })).toContainText(
    'Задач пока нет.',
  );
  release();
  await expect(page.getByRole('listitem')).toHaveCount(1);
  await expect(page.locator('time')).toHaveAttribute(
    'datetime',
    card.created_at,
  );
  const ruTime = await page.locator('time').textContent();
  await page.getByRole('button', { name: 'Հայերեն', exact: true }).click();
  await expect(page.getByRole('listitem')).toContainText(card.text);
  await expect(page.getByRole('listitem')).toContainText(card.author_name);
  expect(await page.locator('time').textContent()).not.toEqual(ruTime);
  fail = false;
  await page.getByRole('button', { name: 'Կրկին փորձել', exact: true }).click();
  await expect(page.getByRole('alert')).toHaveCount(0);
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth,
    ),
  ).toBe(true);
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('lang', 'hy');
  await expect(page.getByRole('listitem')).toContainText(card.text);
  await page.getByRole('button', { name: 'Русский', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Ожидает' })).toContainText(
    card.author_name,
  );
});

test('retries only the failed next page and keeps earlier cards', async ({
  page,
}) => {
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill({ json: { data: member, meta: {} } }),
  );
  let fail = true;
  const cursors: (string | null)[] = [];
  await page.route('**/api/v1/tasks?**', async (route) => {
    const params = new URL(route.request().url()).searchParams;
    const pending = params.get('status') === 'pending';
    const cursor = params.get('cursor');
    if (pending) cursors.push(cursor);
    if (pending && cursor && fail) return route.abort('failed');
    await route.fulfill({
      json: {
        data: pending ? [{ ...card, id: cursor ? 'older' : 'newer' }] : [],
        meta: {
          has_more: pending && !cursor,
          next_cursor: pending && !cursor ? 'opaque' : null,
        },
      },
    });
  });
  await page.goto('/');
  const column = page.getByRole('region', { name: 'Ожидает' });
  await column.getByRole('button', { name: 'Показать ещё' }).click();
  await expect(column.getByRole('alert')).toBeVisible();
  await expect(column.getByRole('listitem')).toHaveCount(1);
  fail = false;
  await column.getByRole('button', { name: 'Повторить', exact: true }).click();
  await expect(column.getByRole('listitem')).toHaveCount(2);
  expect(cursors.filter((cursor) => cursor !== null)).toEqual([
    'opaque',
    'opaque',
  ]);
});
