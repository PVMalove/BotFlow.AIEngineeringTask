import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

const initial = {
  id: 'drag-task',
  text: 'Задача / Առաջադրանք',
  author_id: 'owner',
  author_name: 'Анна / Աննա',
  status: 'pending',
  version: 1,
  created_at: '2026-10-22T18:57:00Z',
  updated_at: '2026-10-22T18:57:00Z',
};

async function board(page: Page) {
  let task = { ...initial };
  const patches: unknown[] = [];
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill({
      json: {
        data: { id: 'owner', role: 'user', status: 'approved' },
        meta: {},
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
  await page.route('**/api/v1/tasks/drag-task', async (route) => {
    if (route.request().method() === 'PATCH') {
      const body = route.request().postDataJSON();
      patches.push(body);
      task = { ...task, status: body.status, version: 2 };
    }
    await route.fulfill({ json: { data: task } });
  });
  await page.goto('/');
  return patches;
}

async function pointerDrop(
  page: Page,
  label: string,
  duringDrag?: () => Promise<void>,
) {
  const handle = page.getByRole('button', { name: /Перенести задачу/ });
  const start = await handle.boundingBox();
  const target = await page
    .getByRole('region', { name: label, exact: true })
    .boundingBox();
  if (!start || !target) throw new Error('Expected drag surfaces');
  await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2);
  await page.mouse.down();
  await page.mouse.move(
    start.x + start.width / 2 - 12,
    start.y + start.height / 2,
    { steps: 3 },
  );
  await page.mouse.move(
    target.x + target.width / 2,
    target.y + target.height / 2,
    { steps: 10 },
  );
  if (duringDrag) await duringDrag();
  await page.mouse.up();
}

test('rereads the task after a refused drop and shows the server status without another PATCH', async ({
  page,
}) => {
  await board(page);
  let refused = false;
  let patches = 0;
  const fresh = { ...initial, status: 'in_progress', version: 2 };
  await page.route('**/api/v1/tasks?**', (route) =>
    route.fulfill({
      json: {
        data:
          new URL(route.request().url()).searchParams.get('status') ===
          (refused ? fresh.status : initial.status)
            ? [refused ? fresh : initial]
            : [],
        meta: { has_more: false },
      },
    }),
  );
  await page.route('**/api/v1/tasks/drag-task', async (route) => {
    if (route.request().method() === 'PATCH') {
      patches++;
      refused = true;
      return route.fulfill({
        status: 403,
        json: { error: { code: 'forbidden', message: 'forbidden' } },
      });
    }
    await route.fulfill({ json: { data: fresh } });
  });
  await pointerDrop(page, 'Завершено');
  await expect(page.getByRole('alert')).toContainText('Запрос отклонён');
  await expect(
    page
      .getByRole('region', { name: 'В работе', exact: true })
      .getByRole('listitem'),
  ).toHaveCount(1);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(patches).toBe(1);
});

test('keeps a newer REST version when an older drag response arrives later', async ({
  page,
}) => {
  await page.clock.install();
  await board(page);
  let phase: 'initial' | 'fresh' | 'failed' = 'initial';
  let finish!: () => void;
  const gate = new Promise<void>((resolve) => {
    finish = resolve;
  });
  const fresh = { ...initial, status: 'completed', version: 3 };
  await page.route('**/api/v1/tasks?**', (route) => {
    if (phase === 'failed') return route.fulfill({ status: 503 });
    const task = phase === 'fresh' ? fresh : initial;
    return route.fulfill({
      json: {
        data:
          new URL(route.request().url()).searchParams.get('status') ===
          task.status
            ? [task]
            : [],
        meta: { has_more: false },
      },
    });
  });
  await page.route('**/api/v1/tasks/drag-task', async (route) => {
    await gate;
    phase = 'failed';
    await route.fulfill({
      json: { data: { ...initial, status: 'in_progress', version: 2 } },
    });
  });
  const sent = page.waitForRequest((request) => request.method() === 'PATCH');
  await pointerDrop(page, 'В работе');
  await sent;
  phase = 'fresh';
  await page.clock.runFor(31_000);
  const completed = page.getByRole('region', {
    name: 'Завершено',
    exact: true,
  });
  await expect(completed.getByRole('listitem')).toHaveCount(1);

  finish();
  await expect(
    page
      .getByRole('region', { name: 'В работе', exact: true })
      .getByRole('alert'),
  ).toBeVisible();
  await expect(completed.getByRole('listitem')).toHaveCount(1);
  await expect(page.getByRole('listitem')).toHaveCount(1);
});

test('keeps the version observed at drag start when REST refreshes the same card during dragging', async ({
  page,
}) => {
  await page.clock.install();
  await board(page);
  let version = 1;
  const patches: unknown[] = [];
  await page.route('**/api/v1/tasks?**', (route) =>
    route.fulfill({
      json: {
        data:
          new URL(route.request().url()).searchParams.get('status') ===
          'pending'
            ? [{ ...initial, version }]
            : [],
        meta: { has_more: false },
      },
    }),
  );
  await page.route('**/api/v1/tasks/drag-task', (route) => {
    if (route.request().method() === 'PATCH') {
      patches.push(route.request().postDataJSON());
      return route.fulfill({
        status: 409,
        json: {
          error: { code: 'version_conflict', message: 'version_conflict' },
        },
      });
    }
    return route.fulfill({ json: { data: { ...initial, version: 3 } } });
  });
  await pointerDrop(page, 'В работе', async () => {
    version = 3;
    await page.clock.runFor(31_000);
  });
  await expect(page.getByRole('alert')).toContainText('Задача изменилась');
  expect(patches).toEqual([{ status: 'in_progress', expected_version: 1 }]);
});

test('localizes pointer drag instructions and cancellation without translating the task', async ({
  page,
}) => {
  const patches = await board(page);
  await page.getByRole('button', { name: 'Հայերեն', exact: true }).click();
  const handle = page.getByRole('button', { name: /Տեղափոխել առաջադրանքը/ });
  const instructionsId = await handle.getAttribute('aria-describedby');
  if (!instructionsId) throw new Error('Expected drag instructions');
  await expect(page.locator('[id="' + instructionsId + '"]')).toContainText(
    'մկնիկով',
  );
  const start = await handle.boundingBox();
  const target = await page
    .getByRole('region', { name: 'Ընթացքի մեջ', exact: true })
    .boundingBox();
  if (!start || !target) throw new Error('Expected drag surfaces');
  await page.mouse.move(start.x + 20, start.y + 20);
  await page.mouse.down();
  await page.mouse.move(start.x + 5, start.y + 20, { steps: 3 });
  await page.mouse.move(
    target.x + target.width / 2,
    target.y + target.height / 2,
    { steps: 12 },
  );
  await expect(page.getByText('Տեղափոխել այստեղ')).toBeVisible();
  await page.keyboard.press('Escape');
  await page.mouse.up();
  await expect(page.getByText('Տեղափոխել այստեղ')).toHaveCount(0);
  await expect(page.getByRole('listitem')).toContainText(initial.text);
  await expect(
    page.locator('[role="status"][aria-live="assertive"]'),
  ).toContainText('Տեղափոխումը չեղարկված է');
  expect(patches).toEqual([]);
});

test('changes status with the keyboard through the task dialog', async ({
  page,
}) => {
  const patches = await board(page);
  const card = page.getByRole('button', { name: initial.text, exact: true });
  await card.focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Статус', { exact: true }).focus();
  await page.keyboard.press('ArrowDown');
  await expect(dialog.getByLabel('Статус', { exact: true })).toHaveValue(
    'in_progress',
  );
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  await expect(
    dialog.getByRole('button', { name: 'Сохранить', exact: true }),
  ).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(dialog).toHaveCount(0);
  await expect(
    page
      .getByRole('region', { name: 'В работе', exact: true })
      .getByRole('listitem'),
  ).toHaveCount(1);
  expect(patches).toEqual([{ status: 'in_progress', expected_version: 1 }]);
});
