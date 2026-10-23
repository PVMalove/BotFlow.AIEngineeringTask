import { expect, test } from '@playwright/test';
import type { WebSocketRoute } from '@playwright/test';

test('shows RU/HY connection states and moves a task after a signal without reloading', async ({
  page,
}) => {
  let socket!: WebSocketRoute;
  await page.routeWebSocket('**/ws/board', (connection) => {
    socket = connection;
  });
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill({
      json: { data: { id: 'owner', role: 'user', status: 'approved' } },
    }),
  );
  let status = 'pending';
  let version = 1;
  await page.route('**/api/v1/tasks?**', (route) =>
    route.fulfill({
      json: {
        data:
          new URL(route.request().url()).searchParams.get('status') === status
            ? [
                {
                  id: 'task',
                  text: 'Realtime / Թարմացում',
                  author_id: 'owner',
                  author_name: 'Анна',
                  status,
                  version,
                  created_at: '2026-01-01T00:00:00Z',
                  updated_at: '2026-01-01T00:00:00Z',
                },
              ]
            : [],
        meta: { has_more: false, next_cursor: null },
      },
    }),
  );
  await page.goto('/');
  await expect(
    page.getByText('Обновления подключены', { exact: true }),
  ).toBeVisible();
  await expect(
    page
      .getByRole('region', { name: 'Ожидает', exact: true })
      .getByRole('listitem'),
  ).toHaveCount(1);

  status = 'completed';
  version = 2;
  socket.send(JSON.stringify({ type: 'board.changed' }));

  await expect(
    page
      .getByRole('region', { name: 'Завершено', exact: true })
      .getByRole('listitem'),
  ).toHaveCount(1);
  await expect(page.getByRole('listitem')).toHaveCount(1);
  await page.getByRole('button', { name: 'Հայերեն', exact: true }).click();
  await expect(
    page.getByText('Թարմացումները միացված են', { exact: true }),
  ).toBeVisible();
  await socket.close({ code: 1013 });
  await expect(
    page.getByText('Վերականգնում ենք կապը։ Վահանակը պարբերաբար թարմացվում է։', {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText('Թարմացումները միացված են', { exact: true }),
  ).toBeVisible();
});

test('hides private cards on policy denial even when Identity is unavailable', async ({
  page,
}) => {
  let socket!: WebSocketRoute;
  let denied = false;
  await page.routeWebSocket('**/ws/board', (connection) => {
    socket = connection;
  });
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill(
      denied
        ? { status: 503 }
        : { json: { data: { id: 'owner', role: 'user', status: 'approved' } } },
    ),
  );
  await page.route('**/api/v1/tasks?**', (route) =>
    route.fulfill({
      json: {
        data:
          new URL(route.request().url()).searchParams.get('status') ===
          'pending'
            ? [
                {
                  id: 'task',
                  text: 'Защищённые данные',
                  author_id: 'owner',
                  author_name: 'Анна',
                  status: 'pending',
                  version: 1,
                  created_at: '2026-01-01T00:00:00Z',
                  updated_at: '2026-01-01T00:00:00Z',
                },
              ]
            : [],
        meta: { has_more: false, next_cursor: null },
      },
    }),
  );
  await page.goto('/');
  await expect(page.getByRole('listitem')).toHaveCount(1);

  denied = true;
  await socket.close({ code: 1008 });

  await expect(page.getByRole('listitem')).toHaveCount(0);
  await expect(
    page.getByText('Защищённые данные', { exact: true }),
  ).toHaveCount(0);
  await expect(page.getByRole('alert')).toContainText(
    'Сервис входа временно недоступен',
  );
});
