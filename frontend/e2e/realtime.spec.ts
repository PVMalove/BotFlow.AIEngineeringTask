import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

import { fixture, loginCode } from './browser-fixture';

async function openBoard(page: Page) {
  await page.clock.install();
  await page.goto('/login');
  await page.getByLabel('Код из Telegram').fill(loginCode());
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(
    page.getByText('Обновления подключены', { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Обновить доску', exact: true }),
  ).toBeEnabled();
  // Freeze polling and reconnect timers: live WS/HTTP traffic stays real.
  await page.clock.pauseAt(new Date());
}

function createTask() {
  const task = fixture('realtime-task');
  if (!task.task_id || !task.text)
    throw new Error('Expected a committed task from the real worker');
  return { id: task.task_id, text: task.text };
}

async function waitForCard(page: Page, status: string, id: string) {
  const column = page.getByRole('region', { name: status, exact: true });
  // REST notifications use zero-delay timers too. Drain a few milliseconds
  // while awaiting the card, without reaching the 30-second polling interval.
  await expect
    .poll(async () => {
      await page.clock.runFor(50);
      return column.locator(`[data-task-id="${id}"]`).count();
    })
    .toBe(1);
  await expect
    .poll(async () => {
      await page.clock.runFor(50);
      return page
        .getByRole('button', { name: 'Обновить доску', exact: true })
        .isEnabled();
    })
    .toBe(true);
}

test('real RabbitMQ creation and REST status changes reach the open board through Redis and WS without reload', async ({
  page,
}) => {
  test.setTimeout(120_000);
  fixture('reset');
  await openBoard(page);
  let reloads = 0;
  page.on('request', (request) => {
    if (request.isNavigationRequest()) reloads++;
  });
  await expect(page.getByRole('listitem')).toHaveCount(0);

  const task = createTask();

  const card = page.locator(`[data-task-id="${task.id}"]`);
  const pending = page.getByRole('region', { name: 'Ожидает', exact: true });
  const completed = page.getByRole('region', {
    name: 'Завершено',
    exact: true,
  });
  await waitForCard(page, 'Ожидает', task.id);
  await expect(pending.locator(`[data-task-id="${task.id}"]`)).toContainText(
    task.text,
  );
  const response = await page.request.patch(`/api/v1/tasks/${task.id}`, {
    headers: { Origin: new URL(page.url()).origin },
    data: { status: 'completed', expected_version: 1 },
  });
  expect(response.status()).toBe(200);
  expect((await response.json()).data).toMatchObject({
    status: 'completed',
    version: 2,
  });

  await waitForCard(page, 'Завершено', task.id);
  await expect(completed.locator(`[data-task-id="${task.id}"]`)).toBeVisible();
  await expect(pending.getByRole('listitem')).toHaveCount(0);
  await expect(card).toHaveCount(1);
  expect(reloads).toBe(0);
});

test('reconnect rereads tasks and status changes committed while the real socket was closed; Pub/Sub does not replay them', async ({
  page,
}) => {
  test.setTimeout(120_000);
  fixture('reset');
  // Instrument only construction so the test can close the real connection.
  // Frames, cookies, Origin, Redis and API requests are never substituted.
  await page.addInitScript(() => {
    const NativeSocket = window.WebSocket;
    const sockets: WebSocket[] = [];
    Object.assign(window, { boardSockets: sockets });
    window.WebSocket = class extends NativeSocket {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols);
        sockets.push(this);
      }
    };
  });
  const original = createTask();
  await openBoard(page);
  const pending = page.getByRole('region', { name: 'Ожидает', exact: true });
  await expect(
    pending.locator(`[data-task-id="${original.id}"]`),
  ).toBeVisible();
  let reloads = 0;
  page.on('request', (request) => {
    if (request.isNavigationRequest()) reloads++;
  });

  await page.evaluate(() => {
    const sockets = (window as unknown as { boardSockets: WebSocket[] })
      .boardSockets;
    sockets.at(-1)!.close(1000, 'E2E disconnect');
  });
  await expect(
    page.getByText('Восстанавливаем связь. Доска обновляется периодически.', {
      exact: true,
    }),
  ).toBeVisible();
  const changed = await page.request.patch(`/api/v1/tasks/${original.id}`, {
    headers: { Origin: new URL(page.url()).origin },
    data: { status: 'in_progress', expected_version: 1 },
  });
  expect(changed.status()).toBe(200);
  const missed = createTask();
  await expect(page.locator(`[data-task-id="${missed.id}"]`)).toHaveCount(0);
  await expect(
    pending.locator(`[data-task-id="${original.id}"]`),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () =>
        (window as unknown as { boardSockets: WebSocket[] }).boardSockets
          .length,
    ),
  ).toBe(1);

  await page.clock.runFor(1_000);

  await waitForCard(page, 'Ожидает', missed.id);
  await expect(
    page.getByText('Обновления подключены', { exact: true }),
  ).toBeVisible();
  await expect(pending.locator(`[data-task-id="${missed.id}"]`)).toContainText(
    missed.text,
  );
  await expect(
    page
      .getByRole('region', { name: 'В работе', exact: true })
      .locator(`[data-task-id="${original.id}"]`),
  ).toBeVisible();
  await expect(page.locator(`[data-task-id="${original.id}"]`)).toHaveCount(1);
  await expect(page.locator(`[data-task-id="${missed.id}"]`)).toHaveCount(1);
  expect(reloads).toBe(0);
});
