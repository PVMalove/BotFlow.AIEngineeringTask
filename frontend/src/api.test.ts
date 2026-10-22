import { afterEach, expect, test, vi } from 'vitest';

import { exchangeCode, getCurrentMember, listTasks, logout } from './api';

const member = {
  id: '85a66d7a-744f-4bd3-b4ca-7cb2a9c632c7',
  telegram_id: 4200000001,
  role: 'admin',
  status: 'approved',
};

afterEach(() => vi.unstubAllGlobals());

test('reads a status page of 50 tasks and passes the opaque cursor unchanged', async () => {
  const page = {
    data: [],
    meta: {
      next_cursor: null,
      prev_cursor: null,
      has_more: false,
      has_prev: false,
    },
  };
  const fetchBoundary = vi.fn().mockResolvedValue(Response.json(page));
  vi.stubGlobal('fetch', fetchBoundary);

  expect(await listTasks('in_progress', 'opaque+/= cursor')).toEqual(page);
  expect(fetchBoundary).toHaveBeenCalledWith(
    '/api/v1/tasks?status=in_progress&limit=50&cursor=opaque%2B%2F%3D+cursor',
    expect.objectContaining({ credentials: 'same-origin', cache: 'no-store' }),
  );
});

test('sends a login code only as JSON with same-origin cookies and no caching', async () => {
  const fetchBoundary = vi
    .fn()
    .mockResolvedValue(Response.json({ data: member, meta: {} }));
  vi.stubGlobal('fetch', fetchBoundary);

  const result = await exchangeCode('a'.repeat(32));

  expect(result).toEqual(member);
  expect(fetchBoundary).toHaveBeenCalledWith('/api/v1/auth/exchange', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code: 'a'.repeat(32) }),
    credentials: 'same-origin',
    cache: 'no-store',
  });
});

test('loads the current member using cookies without browser HTTP caching', async () => {
  const fetchBoundary = vi
    .fn()
    .mockResolvedValue(Response.json({ data: member, meta: {} }));
  vi.stubGlobal('fetch', fetchBoundary);

  expect(await getCurrentMember()).toEqual(member);
  expect(fetchBoundary).toHaveBeenCalledWith('/api/v1/users/me', {
    credentials: 'same-origin',
    cache: 'no-store',
  });
});

test('accepts an empty logout response and sends a cookie-authenticated POST', async () => {
  const fetchBoundary = vi
    .fn()
    .mockResolvedValue(new Response(null, { status: 204 }));
  vi.stubGlobal('fetch', fetchBoundary);

  await expect(logout()).resolves.toBeUndefined();
  expect(fetchBoundary).toHaveBeenCalledWith('/api/v1/auth/logout', {
    method: 'POST',
    credentials: 'same-origin',
    cache: 'no-store',
  });
});

test('keeps server and transport diagnostics out of public errors', async () => {
  const fetchBoundary = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json(
        { error: { code: 'private-server-detail', message: 'private' } },
        { status: 429 },
      ),
    )
    .mockRejectedValueOnce(new Error('private-transport-detail'));
  vi.stubGlobal('fetch', fetchBoundary);

  await expect(exchangeCode('a'.repeat(32))).rejects.toMatchObject({
    status: 429,
    message: 'API request failed',
  });
  await expect(getCurrentMember()).rejects.toMatchObject({
    status: 0,
    message: 'API request failed',
  });
});
