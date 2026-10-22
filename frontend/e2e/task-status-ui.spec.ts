import { expect, test } from '@playwright/test';

const original = {
  id: 'task',
  text: 'Подготовить запуск',
  author_id: 'owner',
  author_name: 'Malove',
  status: 'pending',
  version: 1,
  created_at: '2026-10-22T18:57:00Z',
  updated_at: '2026-10-22T18:57:00Z',
};

test('saves the selected status once and moves the card without duplicates', async ({
  page,
}) => {
  let task = { ...original };
  const patches: unknown[] = [];
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill({
      json: {
        id: 'owner',
        telegram_id: 42,
        display_name: 'Malove',
        role: 'user',
        status: 'approved',
      },
    }),
  );
  await page.route('**/api/v1/tasks?**', (route) =>
    route.fulfill({
      json: {
        data:
          new URL(route.request().url()).searchParams.get('status') ===
          task.status
            ? [task]
            : [],
        meta: { has_more: false, next_cursor: null },
      },
    }),
  );
  await page.route('**/api/v1/tasks/task', async (route) => {
    if (route.request().method() === 'PATCH') {
      patches.push(route.request().postDataJSON());
      task = { ...task, status: 'in_progress', version: 2 };
    }
    await route.fulfill({ json: { data: task } });
  });

  await page.goto('/');
  await page.getByRole('button', { name: original.text, exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog
    .getByLabel('Статус', { exact: true })
    .selectOption('in_progress');
  expect(patches).toEqual([]);
  await dialog.getByRole('button', { name: 'Сохранить', exact: true }).click();

  await expect(dialog).toHaveCount(0);
  await expect(
    page
      .getByRole('region', { name: 'В работе', exact: true })
      .getByRole('listitem'),
  ).toHaveCount(1);
  await expect(page.getByRole('listitem')).toHaveCount(1);
  expect(patches).toEqual([{ status: 'in_progress', expected_version: 1 }]);
});

for (const { outcome, expected, expectedStatus } of [
  {
    outcome: 'conflict',
    expected: 'Задача изменилась',
    expectedStatus: 'completed',
  },
  {
    outcome: 'lost',
    expected: 'Результат сохранения не подтверждён',
    expectedStatus: 'completed',
  },
  {
    outcome: 'failed-read',
    expected: 'Не удалось перечитать задачу',
    expectedStatus: 'in_progress',
  },
  { outcome: 'denied', expected: 'Запрос отклонён', expectedStatus: 'pending' },
] as const) {
  test(
    'keeps the dialog and reconciles ' + outcome + ' without repeating PATCH',
    async ({ page }) => {
      let task = { ...original };
      let patches = 0;
      let readFails = outcome === 'failed-read';
      await page.route('**/api/v1/users/me', (route) =>
        route.fulfill({
          json: {
            id: 'owner',
            role: 'user',
            status: 'approved',
            display_name: 'Malove',
          },
        }),
      );
      await page.route('**/api/v1/tasks?**', (route) =>
        route.fulfill({
          json: {
            data:
              new URL(route.request().url()).searchParams.get('status') ===
              task.status
                ? [task]
                : [],
            meta: { has_more: false },
          },
        }),
      );
      await page.route('**/api/v1/tasks/task', async (route) => {
        if (route.request().method() === 'PATCH') {
          patches++;
          if (outcome === 'denied')
            return route.fulfill({
              status: 403,
              json: { detail: 'forbidden' },
            });
          task = { ...task, status: 'completed', version: 2 };
          if (outcome === 'conflict')
            return route.fulfill({
              status: 409,
              json: { detail: 'version_conflict' },
            });
          return route.abort('failed');
        }
        if (readFails) return route.fulfill({ status: 503 });
        await route.fulfill({ json: { data: task } });
      });
      await page.goto('/');
      await page
        .getByRole('button', { name: original.text, exact: true })
        .click();
      const dialog = page.getByRole('dialog');
      await dialog
        .getByLabel('Статус', { exact: true })
        .selectOption('in_progress');
      await dialog
        .getByRole('button', { name: 'Сохранить', exact: true })
        .click();
      await expect(dialog.getByRole('alert')).toContainText(expected);
      await expect(dialog.getByLabel('Статус', { exact: true })).toHaveValue(
        expectedStatus,
      );
      if (outcome === 'failed-read') {
        await expect(
          dialog.getByRole('button', { name: 'Сохранить', exact: true }),
        ).toBeDisabled();
        readFails = false;
        await dialog
          .getByRole('button', { name: 'Повторить', exact: true })
          .click();
        await expect(dialog.getByLabel('Статус', { exact: true })).toHaveValue(
          'completed',
        );
        await expect(
          dialog.getByRole('button', { name: 'Сохранить', exact: true }),
        ).toBeEnabled();
      }
      await dialog.getByRole('button', { name: 'Отмена', exact: true }).click();
      await page.getByRole('button', { name: 'Հայերեն', exact: true }).click();
      await expect(page.getByRole('alert')).not.toContainText(expected);
      expect(patches).toBe(1);
    },
  );
}

test('lets a regular user read a foreign task without status actions', async ({
  page,
}) => {
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill({ json: { id: 'other', role: 'user', status: 'approved' } }),
  );
  await page.route('**/api/v1/tasks?**', (route) =>
    route.fulfill({
      json: {
        data:
          new URL(route.request().url()).searchParams.get('status') ===
          'pending'
            ? [original]
            : [],
        meta: { has_more: false },
      },
    }),
  );
  await page.goto('/');
  await expect(
    page.getByRole('button', { name: /Перенести задачу/ }),
  ).toHaveCount(0);
  await page.getByRole('button', { name: original.text, exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(
    page.getByRole('dialog').getByLabel('Статус', { exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole('dialog').getByRole('button', { name: 'Сохранить' }),
  ).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('blocks duplicate submissions until the response completes', async ({
  page,
}) => {
  let finish!: () => void;
  const gate = new Promise<void>((resolve) => {
    finish = resolve;
  });
  let patches = 0;
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill({ json: { id: 'admin', role: 'admin', status: 'approved' } }),
  );
  await page.route('**/api/v1/tasks?**', (route) =>
    route.fulfill({
      json: {
        data:
          new URL(route.request().url()).searchParams.get('status') ===
          'pending'
            ? [original]
            : [],
        meta: { has_more: false },
      },
    }),
  );
  await page.route('**/api/v1/tasks/task', async (route) => {
    patches++;
    await gate;
    await route.fulfill({ json: { data: original } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: original.text, exact: true }).click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Сохранить', exact: true })
    .click();
  await expect(
    page.getByRole('button', { name: 'Сохраняем…', exact: true }),
  ).toBeDisabled();
  await page.keyboard.press('Enter');
  expect(patches).toBe(1);
  finish();
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('drags to another column once and never opens the task from a drag', async ({
  page,
}) => {
  let task = { ...original };
  const patches: unknown[] = [];
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill({ json: { id: 'owner', role: 'user', status: 'approved' } }),
  );
  await page.route('**/api/v1/tasks?**', (route) =>
    route.fulfill({
      json: {
        data:
          new URL(route.request().url()).searchParams.get('status') ===
          task.status
            ? [task]
            : [],
        meta: { has_more: false },
      },
    }),
  );
  await page.route('**/api/v1/tasks/task', async (route) => {
    patches.push(route.request().postDataJSON());
    task = { ...task, status: 'in_progress', version: 2 };
    await route.fulfill({ json: { data: task } });
  });
  await page.goto('/');
  const handle = page.getByRole('button', { name: /Перенести задачу/ });
  const source = await handle.boundingBox();
  const target = await page
    .getByRole('region', { name: 'В работе', exact: true })
    .boundingBox();
  if (!source || !target) throw new Error('Expected visible drag surfaces');
  await page.mouse.move(
    source.x + source.width / 2,
    source.y + source.height / 2,
  );
  await page.mouse.down();
  await page.mouse.move(
    source.x + source.width / 2 + 12,
    source.y + source.height / 2,
    { steps: 3 },
  );
  await page.mouse.move(
    target.x + target.width / 2,
    target.y + target.height / 2,
    { steps: 15 },
  );
  await expect(page.getByText('Перенести сюда', { exact: true })).toBeVisible();
  await page.mouse.up();
  await expect(
    page
      .getByRole('region', { name: 'В работе', exact: true })
      .getByRole('listitem'),
  ).toHaveCount(1);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(patches).toEqual([{ status: 'in_progress', expected_version: 1 }]);
  // A drag within the same column never changes status.
  await expect(handle).toBeEnabled();
  const same = await handle.boundingBox();
  if (!same) throw new Error('Expected the moved handle');
  await page.mouse.move(same.x + 20, same.y + 20);
  await page.mouse.down();
  await page.mouse.move(same.x + 5, same.y + 35, { steps: 5 });
  await page.mouse.up();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(patches).toHaveLength(1);
});

test.describe('touch input', () => {
  test.use({
    hasTouch: true,
    isMobile: true,
    viewport: { width: 390, height: 844 },
  });

  test('moves a card with real touch events on its handle', async ({
    page,
    context,
  }) => {
    let task = { ...original };
    let patches = 0;
    await page.route('**/api/v1/users/me', (route) =>
      route.fulfill({
        json: { id: 'owner', role: 'user', status: 'approved' },
      }),
    );
    await page.route('**/api/v1/tasks?**', (route) =>
      route.fulfill({
        json: {
          data:
            new URL(route.request().url()).searchParams.get('status') ===
            task.status
              ? [task]
              : [],
          meta: { has_more: false },
        },
      }),
    );
    await page.route('**/api/v1/tasks/task', async (route) => {
      expect(route.request().postDataJSON()).toEqual({
        status: 'in_progress',
        expected_version: 1,
      });
      patches++;
      task = { ...task, status: 'in_progress', version: 2 };
      await route.fulfill({ json: { data: task } });
    });
    await page.goto('/');
    const handle = page.getByRole('button', { name: /Перенести задачу/ });
    const source = await handle.boundingBox();
    const target = await page
      .getByRole('region', { name: 'В работе', exact: true })
      .boundingBox();
    if (!source || !target) throw new Error('Expected visible touch surfaces');
    const cdp = await context.newCDPSession(page);
    const x = source.x + source.width / 2;
    const y = source.y + source.height / 2;
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchStart',
      touchPoints: [{ x, y }],
    });
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [{ x: x + 12, y }],
    });
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [
        { x: target.x + target.width / 2, y: target.y + target.height / 2 },
      ],
    });
    await expect(
      page.getByText('Перенести сюда', { exact: true }),
    ).toBeVisible();
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchEnd',
      touchPoints: [],
    });
    await expect(
      page
        .getByRole('region', { name: 'В работе', exact: true })
        .getByRole('listitem'),
    ).toHaveCount(1);
    await expect(page.getByRole('dialog')).toHaveCount(0);
    expect(patches).toBe(1);
  });
});
