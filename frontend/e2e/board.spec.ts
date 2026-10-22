import { expect, test } from '@playwright/test';
import { fixture, loginCode } from './browser-fixture';
import type { Task } from '../src/api';

const statuses = ['pending', 'in_progress', 'completed'];
const labels = ['Ожидает', 'В работе', 'Завершено'];

test('reads and paginates all real columns, then refreshes changed history without gaps or duplicates', async ({
  page,
}) => {
  test.setTimeout(120_000);
  fixture('reset');
  fixture('board');
  await page.goto('/login');
  await page.getByLabel('Код из Telegram').fill(loginCode());
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  for (const label of labels) {
    const column = page.getByRole('region', { name: label, exact: true });
    await expect(column.getByRole('listitem')).toHaveCount(50);
    await column.getByRole('button', { name: 'Показать ещё' }).click();
    await expect(column.getByRole('listitem')).toHaveCount(52);
    await expect(
      column.getByRole('button', { name: 'Показать ещё' }),
    ).toHaveCount(0);
    await expect(column.getByRole('listitem').first()).toContainText(
      'Анна / Աննա',
    );
  }
  // Inserts above the old head and moves a card across the page boundary.
  fixture('board-change');
  await page.getByRole('button', { name: 'Обновить доску' }).click();
  await expect(
    page
      .getByRole('region', { name: 'В работе', exact: true })
      .getByRole('listitem'),
  ).toHaveCount(53);
  await expect(
    page.getByRole('button', { name: 'Обновить доску' }),
  ).toBeEnabled();
  for (const [index, status] of statuses.entries()) {
    const response = await page.request.get(
      `/api/v1/tasks?status=${status}&limit=100`,
    );
    expect(response.status()).toBe(200);
    const expected = (await response.json()).data as Task[];
    const ids = await page
      .getByRole('region', { name: labels[index], exact: true })
      .getByRole('listitem')
      .evaluateAll((cards) =>
        cards.map((card) => card.getAttribute('data-task-id')),
      );
    expect(ids).toEqual(expected.map(({ id }) => id));
    expect(new Set(ids).size).toBe(ids.length);
  }
  const pending = page.getByRole('region', { name: 'Ожидает', exact: true });
  await expect(pending.getByRole('listitem').first()).toContainText(
    'Новая задача / Նոր առաջադրանք',
  );
  const userText = await pending.getByRole('listitem').first().textContent();
  await page.getByRole('button', { name: 'Հայերեն', exact: true }).click();
  const hyPending = page.getByRole('region', {
    name: 'Սպասում է',
    exact: true,
  });
  await expect(hyPending.getByRole('listitem')).toHaveCount(52);
  await expect(hyPending.getByRole('listitem').first()).toContainText(
    'Новая задача / Նոր առաջադրանք',
  );
  expect(userText).toContain('Анна / Աննա');
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth,
    ),
  ).toBe(true);
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('lang', 'hy');
  await expect(hyPending.getByRole('listitem')).toHaveCount(50);
  await hyPending.getByRole('button', { name: 'Ցույց տալ ավելին' }).click();
  await expect(hyPending.getByRole('listitem')).toHaveCount(52);
  await page.getByRole('button', { name: 'Русский', exact: true }).click();
  await expect(pending.getByRole('listitem')).toHaveCount(52);
});

test('changes status through real PATCH, handles no-op and reconciles a concurrent version', async ({
  page,
}) => {
  test.setTimeout(120_000);
  fixture('reset');
  fixture('board');
  await page.goto('/login');
  await page.getByLabel('Код из Telegram').fill(loginCode());
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  const card = page
    .getByRole('region', { name: 'Ожидает', exact: true })
    .getByRole('listitem')
    .first();
  const id = await card.getAttribute('data-task-id');
  if (!id) throw new Error('Expected a real task');
  await card.getByRole('button').first().click();
  let dialog = page.getByRole('dialog');
  await dialog
    .getByLabel('Статус', { exact: true })
    .selectOption('in_progress');
  await dialog.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  const moved = page
    .getByRole('region', { name: 'В работе', exact: true })
    .locator('[data-task-id="' + id + '"]');
  await expect(moved).toBeVisible();
  const fresh = await (await page.request.get('/api/v1/tasks/' + id)).json();
  expect(fresh.data.status).toBe('in_progress');
  expect(fresh.data.version).toBe(2);

  await moved.getByRole('button').first().click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Сохранить', exact: true })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(
    (await (await page.request.get('/api/v1/tasks/' + id)).json()).data.version,
  ).toBe(2);

  await moved.getByRole('button').first().click();
  const concurrent = await page.request.patch('/api/v1/tasks/' + id, {
    data: { status: 'completed', expected_version: 2 },
    headers: { Origin: new URL(page.url()).origin },
  });
  expect(concurrent.status()).toBe(200);
  dialog = page.getByRole('dialog');
  await dialog.getByLabel('Статус', { exact: true }).selectOption('pending');
  await dialog.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('Задача изменилась');
  await expect(dialog.getByLabel('Статус', { exact: true })).toHaveValue(
    'completed',
  );
  expect(
    (await (await page.request.get('/api/v1/tasks/' + id)).json()).data.version,
  ).toBe(3);
});
