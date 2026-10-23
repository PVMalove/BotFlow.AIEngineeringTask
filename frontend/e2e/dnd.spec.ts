import { expect, test } from '@playwright/test';
import type { Locator, Page } from '@playwright/test';
import { fixture, loginCode } from './browser-fixture';

async function prepare(page: Page) {
  fixture('reset');
  fixture('dnd-board');
  await page.goto('/login');
  await page.getByLabel('Код из Telegram').fill(loginCode());
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  const card = page
    .getByRole('region', { name: 'Ожидает', exact: true })
    .getByRole('listitem');
  await expect(card).toHaveCount(1);
  const id = await card.getAttribute('data-task-id');
  if (!id) throw new Error('Expected a real task');
  const patches: unknown[] = [];
  page.on('request', (request) => {
    if (request.method() === 'PATCH' && request.url().endsWith('/tasks/' + id))
      patches.push(request.postDataJSON());
  });
  return { id, card, patches };
}

async function drag(
  page: Page,
  handle: Locator,
  target: Locator,
  touch = false,
  duringDrag?: () => Promise<void>,
) {
  await handle.scrollIntoViewIfNeeded();
  const start = await handle.boundingBox();
  if (!start) throw new Error('Expected a visible handle');
  const x = start.x + start.width / 2;
  const y = start.y + start.height / 2;
  const cdp = touch ? await page.context().newCDPSession(page) : null;
  if (cdp) {
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchStart',
      touchPoints: [{ x, y }],
    });
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [{ x: x - 12, y }],
    });
  } else {
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x - 12, y, { steps: 3 });
  }
  await expect(handle).toHaveAttribute('aria-pressed', 'true');
  if (duringDrag) await duringDrag();
  await target.scrollIntoViewIfNeeded();
  const end = await target.boundingBox();
  if (!end) throw new Error('Expected a visible target');
  const destination = { x: end.x + end.width / 2, y: end.y + end.height / 2 };
  if (cdp) {
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [destination],
    });
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchEnd',
      touchPoints: [],
    });
    await cdp.detach();
  } else {
    await page.mouse.move(destination.x, destination.y, { steps: 12 });
    await page.mouse.up();
  }
}

async function read(page: Page, id: string) {
  const response = await page.request.get('/api/v1/tasks/' + id);
  expect(response.status()).toBe(200);
  return (await response.json()).data;
}

test('moves into a real empty column using mouse with exactly one versioned PATCH', async ({
  page,
}) => {
  test.setTimeout(90_000);
  const { id, card, patches } = await prepare(page);
  const completed = page.getByRole('region', {
    name: 'Завершено',
    exact: true,
  });
  await expect(completed.getByRole('listitem')).toHaveCount(0);
  await drag(
    page,
    card.getByRole('button', { name: /Перенести задачу/ }),
    completed,
  );
  await expect(completed.locator('[data-task-id="' + id + '"]')).toBeVisible();
  await expect(page.locator('[data-task-id="' + id + '"]')).toHaveCount(1);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(patches).toEqual([{ status: 'completed', expected_version: 1 }]);
  expect(await read(page, id)).toMatchObject({
    status: 'completed',
    version: 2,
  });
});

test('changes real task status with the keyboard through its dialog', async ({
  page,
}) => {
  test.setTimeout(90_000);
  const { id, card, patches } = await prepare(page);
  await card.getByRole('button').first().focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await dialog.getByLabel('Статус').focus();
  await page.keyboard.press('ArrowDown');
  await expect(dialog.getByLabel('Статус')).toHaveValue('in_progress');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  await expect(dialog.getByRole('button', { name: 'Сохранить' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(dialog).toHaveCount(0);
  await expect(
    page
      .getByRole('region', { name: 'В работе', exact: true })
      .locator('[data-task-id="' + id + '"]'),
  ).toBeVisible();
  expect(patches).toEqual([{ status: 'in_progress', expected_version: 1 }]);
  expect(await read(page, id)).toMatchObject({
    status: 'in_progress',
    version: 2,
  });
});

test('cancels with Escape and ignores same-column and invalid drops against the real API', async ({
  page,
}) => {
  test.setTimeout(90_000);
  const { id, card, patches } = await prepare(page);
  const handle = card.getByRole('button', { name: /Перенести задачу/ });
  await handle.scrollIntoViewIfNeeded();
  const initial = await handle.boundingBox();
  if (!initial) throw new Error('Expected a visible handle');
  await page.mouse.move(
    initial.x + initial.width / 2,
    initial.y + initial.height / 2,
  );
  await page.mouse.down();
  await page.mouse.move(initial.x - 12, initial.y, { steps: 3 });
  await expect(handle).toHaveAttribute('aria-pressed', 'true');
  const target = page.getByRole('region', { name: 'В работе', exact: true });
  await target.scrollIntoViewIfNeeded();
  const destination = await target.boundingBox();
  if (!destination) throw new Error('Expected a visible target');
  await page.mouse.move(
    destination.x + destination.width / 2,
    destination.y + destination.height / 2,
    { steps: 12 },
  );
  await expect(page.getByText('Перенести сюда')).toBeVisible();
  await page.keyboard.press('Escape');
  await page.mouse.up();
  await expect(page.getByText('Перенести сюда')).toHaveCount(0);
  await drag(
    page,
    handle,
    page.getByRole('region', { name: 'Ожидает', exact: true }),
  );
  await handle.scrollIntoViewIfNeeded();
  const start = await handle.boundingBox();
  if (!start) throw new Error('Expected a visible handle');
  await page.mouse.move(start.x + 20, start.y + 20);
  await page.mouse.down();
  await page.mouse.move(start.x - 15, start.y, { steps: 3 });
  await page.mouse.move(2, 2, { steps: 12 });
  await page.mouse.up();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(card).toHaveCount(1);
  expect(patches).toEqual([]);
  expect(await read(page, id)).toMatchObject({ status: 'pending', version: 1 });
});

test('starts dragging at the visible handle during a delayed real REST refresh', async ({
  page,
}) => {
  test.setTimeout(90_000);
  const { id, card, patches } = await prepare(page);
  const refresh = page.getByRole('button', {
    name: 'Обновить доску',
    exact: true,
  });
  await expect(
    page.getByText('Обновления подключены', { exact: true }),
  ).toBeVisible();
  await expect(refresh).toBeEnabled();
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  const handle = card.getByRole('button', { name: /Перенести задачу/ });
  await handle.scrollIntoViewIfNeeded();
  const start = await handle.boundingBox();
  if (!start) throw new Error('Expected a visible drag handle');
  let finish!: () => void;
  const gate = new Promise<void>((resolve) => {
    finish = resolve;
  });
  // Delay real responses without replacing the API data or WebSocket traffic.
  await page.route('**/api/v1/tasks?**', async (route) => {
    await gate;
    await route.continue();
  });

  // Start the refresh without scrolling away from the measured handle.
  await refresh.dispatchEvent('click');
  await expect(refresh).toBeDisabled();
  try {
    await page.mouse.move(
      start.x + start.width / 2,
      start.y + start.height / 2,
    );
    await page.mouse.down();
    await page.mouse.move(
      start.x + start.width / 2 - 12,
      start.y + start.height / 2,
      { steps: 3 },
    );
    await expect(handle).toHaveAttribute('aria-pressed', 'true');
    await page.keyboard.press('Escape');
  } finally {
    await page.mouse.up();
    finish();
  }
  await expect(refresh).toBeEnabled();
  expect(patches).toEqual([]);
  expect(await read(page, id)).toMatchObject({ status: 'pending', version: 1 });
});

test('reconciles a real version conflict after drop without repeating PATCH', async ({
  page,
}) => {
  test.setTimeout(90_000);
  const { id, card, patches } = await prepare(page);
  await drag(
    page,
    card.getByRole('button', { name: /Перенести задачу/ }),
    page.getByRole('region', { name: 'Завершено', exact: true }),
    false,
    async () => {
      // Capture the drag's version before a concurrent writer changes it.
      // Realtime may move the original card as soon as that writer commits.
      const concurrent = await page.request.patch('/api/v1/tasks/' + id, {
        headers: { Origin: new URL(page.url()).origin },
        data: { status: 'in_progress', expected_version: 1 },
      });
      expect(concurrent.status()).toBe(200);
    },
  );
  await expect(page.getByRole('alert')).toContainText('Задача изменилась');
  await expect(
    page
      .getByRole('region', { name: 'В работе', exact: true })
      .locator('[data-task-id="' + id + '"]'),
  ).toBeVisible();
  expect(patches).toEqual([{ status: 'completed', expected_version: 1 }]);
  expect(await read(page, id)).toMatchObject({
    status: 'in_progress',
    version: 2,
  });
});

test('recovers the committed real status after losing the PATCH response', async ({
  page,
}) => {
  test.setTimeout(90_000);
  const { id, card, patches } = await prepare(page);
  await page.route('**/api/v1/tasks/' + id, async (route) => {
    if (route.request().method() !== 'PATCH') return route.continue();
    const response = await route.fetch();
    expect(response.status()).toBe(200);
    await route.abort('failed');
  });
  await drag(
    page,
    card.getByRole('button', { name: /Перенести задачу/ }),
    page.getByRole('region', { name: 'Завершено', exact: true }),
  );
  await expect(page.getByRole('alert')).toContainText(
    'Результат сохранения не подтверждён',
  );
  await expect(
    page
      .getByRole('region', { name: 'Завершено', exact: true })
      .locator('[data-task-id="' + id + '"]'),
  ).toBeVisible();
  expect(patches).toEqual([{ status: 'completed', expected_version: 1 }]);
  expect(await read(page, id)).toMatchObject({
    status: 'completed',
    version: 2,
  });
});

test('hides foreign handles for a real user and preserves the server refusal', async ({
  page,
}) => {
  test.setTimeout(90_000);
  const { id, patches } = await prepare(page);
  fixture('join');
  const requests = await page.request.get('/api/v1/admin/join-requests');
  const target = (await requests.json()).data.find(
    (member: { telegram_id: number }) => member.telegram_id === 4200000008,
  );
  expect(target).toBeTruthy();
  const approved = await page.request.post(
    '/api/v1/admin/members/' + target.id + '/approve',
    {
      headers: { Origin: new URL(page.url()).origin },
      data: { expected_version: target.access_version },
    },
  );
  expect(approved.status()).toBe(200);
  await page.getByRole('button', { name: 'Выйти', exact: true }).click();
  await page.getByLabel('Код из Telegram').fill(loginCode('user-code'));
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page.locator('[data-task-id="' + id + '"]')).toBeVisible();
  await expect(
    page.getByRole('button', { name: /Перенести задачу/ }),
  ).toHaveCount(0);
  const refused = await page.request.patch('/api/v1/tasks/' + id, {
    headers: { Origin: new URL(page.url()).origin },
    data: { status: 'completed', expected_version: 1 },
  });
  expect(refused.status()).toBe(403);
  expect(patches).toEqual([]);
  expect(await read(page, id)).toMatchObject({ status: 'pending', version: 1 });
});

test.describe('real touch input', () => {
  test.use({
    hasTouch: true,
    isMobile: true,
    viewport: { width: 390, height: 650 },
  });
  test('preserves scrolling and transfers a task by its handle on a touch screen', async ({
    page,
  }) => {
    test.setTimeout(90_000);
    const { id, card, patches } = await prepare(page);
    const body = card.getByRole('button').first();
    await body.scrollIntoViewIfNeeded();
    const start = await body.boundingBox();
    if (!start) throw new Error('Expected a visible task body');
    const cdp = await page.context().newCDPSession(page);
    const x = start.x + 30;
    const y = start.y + start.height - 20;
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchStart',
      touchPoints: [{ x, y }],
    });
    for (let distance = 20; distance <= 100; distance += 20) {
      await cdp.send('Input.dispatchTouchEvent', {
        type: 'touchMove',
        touchPoints: [{ x, y: y - distance }],
      });
    }
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchEnd',
      touchPoints: [],
    });
    await expect
      .poll(() => page.evaluate(() => window.scrollY))
      .toBeGreaterThan(0);
    expect(patches).toEqual([]);
    await cdp.detach();
    await drag(
      page,
      card.getByRole('button', { name: /Перенести задачу/ }),
      page.getByRole('region', { name: 'Завершено', exact: true }),
      true,
    );
    await expect(
      page
        .getByRole('region', { name: 'Завершено', exact: true })
        .locator('[data-task-id="' + id + '"]'),
    ).toBeVisible();
    expect(patches).toEqual([{ status: 'completed', expected_version: 1 }]);
    expect(await read(page, id)).toMatchObject({
      status: 'completed',
      version: 2,
    });
  });
});
