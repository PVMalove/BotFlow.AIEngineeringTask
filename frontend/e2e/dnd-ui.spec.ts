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

test('moves to the adjacent empty column with one arrow and confirms with Space', async ({
  page,
}) => {
  const patches = await board(page);
  await page.getByRole('button', { name: 'Русский', exact: true }).click();
  await page.keyboard.press('Tab');
  const handle = page.getByRole('button', { name: /Перенести задачу/ });
  await handle.focus();
  await expect(handle).toHaveCSS('outline-style', 'solid');
  await page.keyboard.press('Space');
  await expect(handle).toHaveAttribute('aria-pressed', 'true');
  await page.keyboard.press('ArrowRight');
  await expect(page.getByText('Перенести сюда', { exact: true })).toBeVisible();
  expect(patches).toEqual([]);
  await page.keyboard.press('Space');
  await expect(
    page
      .getByRole('region', { name: 'В работе', exact: true })
      .getByRole('listitem'),
  ).toHaveCount(1);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(patches).toEqual([{ status: 'in_progress', expected_version: 1 }]);
});

async function pointerDrop(page: Page, label: string) {
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
      return route.fulfill({ status: 403, json: { detail: 'forbidden' } });
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
        json: { detail: 'version_conflict' },
      });
    }
    return route.fulfill({ json: { data: { ...initial, version: 3 } } });
  });
  const handle = page.getByRole('button', { name: /Перенести задачу/ });
  await handle.focus();
  await page.keyboard.press('Space');
  version = 3;
  await page.clock.runFor(31_000);
  await page.keyboard.press('ArrowRight');
  await expect(page.getByText('Перенести сюда')).toBeVisible();
  await page.keyboard.press('Space');
  await expect(page.getByRole('alert')).toContainText('Задача изменилась');
  expect(patches).toEqual([{ status: 'in_progress', expected_version: 1 }]);
});

test('localizes drag instructions and cancellation in Armenian without translating the task', async ({
  page,
}) => {
  const patches = await board(page);
  await page.getByRole('button', { name: 'Հայերեն', exact: true }).click();
  const handle = page.getByRole('button', { name: /Տեղափոխել առաջադրանքը/ });
  await handle.focus();
  const instructionsId = await handle.getAttribute('aria-describedby');
  if (!instructionsId) throw new Error('Expected keyboard instructions');
  await expect(page.locator('[id="' + instructionsId + '"]')).toContainText(
    'Բացատը',
  );
  await page.keyboard.press('Space');
  await expect(handle).toHaveAttribute('aria-pressed', 'true');
  await expect(
    page.locator('[role="status"][aria-live="assertive"]'),
  ).toContainText('Նպատակային սյունակ: Սպասում է');
  await page.keyboard.press('ArrowRight');
  await expect(page.getByText('Տեղափոխել այստեղ')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByText('Տեղափոխել այստեղ')).toHaveCount(0);
  await expect(page.getByRole('listitem')).toContainText(initial.text);
  await expect(
    page.locator('[role="status"][aria-live="assertive"]'),
  ).toContainText('Տեղափոխումը չեղարկված է');
  expect(patches).toEqual([]);
});
