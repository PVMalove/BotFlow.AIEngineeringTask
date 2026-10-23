import { InfiniteQueryObserver, QueryClient } from '@tanstack/react-query';
import { afterEach, expect, test, vi } from 'vitest';

import { boardColumnOptions, startBoardRealtime } from './boardRealtime';
import type { InfiniteData } from '@tanstack/react-query';
import type { TaskPage } from './api';

class Socket extends EventTarget {
  static instances: Socket[] = [];
  constructor(readonly url: string) {
    super();
    Socket.instances.push(this);
  }
  close() {}
  open() {
    this.dispatchEvent(new Event('open'));
  }
  changed() {
    this.dispatchEvent(
      new MessageEvent('message', { data: '{"type":"board.changed"}' }),
    );
  }
  disconnect(code = 1006) {
    this.dispatchEvent(Object.assign(new Event('close'), { code }));
  }
}

const task = {
  id: 'one',
  text: 'Задача',
  author_id: 'owner',
  author_name: 'Анна',
  status: 'pending',
  version: 1,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};
const page = (data: unknown[]) =>
  Response.json({ data, meta: { has_more: false, next_cursor: null } });
const cleanups: (() => void)[] = [];

afterEach(() => {
  cleanups
    .splice(0)
    .reverse()
    .forEach((cleanup) => cleanup());
  vi.unstubAllGlobals();
  vi.useRealTimers();
  Socket.instances = [];
});

function setup() {
  const browser = Object.assign(new EventTarget(), {
    location: new URL('https://botflow.example/'),
  });
  const document = Object.assign(new EventTarget(), {
    visibilityState: 'visible',
  });
  vi.stubGlobal('window', browser);
  vi.stubGlobal('document', document);
  vi.stubGlobal('WebSocket', Socket);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  cleanups.push(() => client.clear());
  const observer = new InfiniteQueryObserver(
    client,
    boardColumnOptions(client, 'owner', 'pending'),
  );
  cleanups.push(observer.subscribe(() => {}));
  const states = vi.fn();
  const sync = startBoardRealtime(client, 'owner', states);
  cleanups.push(sync.stop);
  return { client, observer, states, sync, browser, document };
}

test('a board signal during an unfinished REST read schedules a subsequent read', async () => {
  let finish!: (value: Response) => void;
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValueOnce(page([]))
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            finish = resolve;
          }),
      )
      .mockResolvedValue(page([task])),
  );
  const { observer } = setup();

  expect(Socket.instances).toHaveLength(1);
  expect(Socket.instances[0].url).toBe('wss://botflow.example/ws/board');
  await vi.waitFor(() =>
    expect(observer.getCurrentResult().isSuccess).toBe(true),
  );
  Socket.instances[0].open();
  await vi.waitFor(() => expect(finish).toBeTypeOf('function'));
  Socket.instances[0].changed();
  finish(page([]));

  await vi.waitFor(() =>
    expect(observer.getCurrentResult().data?.pages[0].data).toEqual([task]),
  );
});

test('a delayed REST page cannot restore an older version after a task moved columns', async () => {
  let finish!: (value: Response) => void;
  vi.stubGlobal(
    'fetch',
    vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    ),
  );
  const { client, observer } = setup();
  const current = { ...task, status: 'completed', version: 3 };
  const data: InfiniteData<TaskPage> = {
    pages: [
      {
        data: [current as TaskPage['data'][number]],
        meta: { has_more: false, next_cursor: null },
      },
    ],
    pageParams: [null],
  };
  client.setQueryData(['tasks', 'owner', 'completed'], data);

  finish(page([task]));

  await vi.waitFor(() =>
    expect(observer.getCurrentResult().isSuccess).toBe(true),
  );
  expect(observer.getCurrentResult().data?.pages[0].data).toEqual([]);
  expect(client.getQueryData(['tasks', 'owner', 'completed'])).toEqual(data);
});

test('does not resurrect an older version after the newer card leaves the loaded window', async () => {
  let finish!: (value: Response) => void;
  vi.stubGlobal(
    'fetch',
    vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    ),
  );
  const { client, observer } = setup();
  client.setQueryData(['tasks', 'owner', 'completed'], {
    pages: [
      {
        data: [{ ...task, status: 'completed', version: 3 }],
        meta: { has_more: false, next_cursor: null },
      },
    ],
    pageParams: [null],
  });
  // A later REST snapshot no longer contains this card in the loaded window.
  client.setQueryData(['tasks', 'owner', 'completed'], {
    pages: [{ data: [], meta: { has_more: false, next_cursor: null } }],
    pageParams: [null],
  });

  finish(page([task]));

  await vi.waitFor(() =>
    expect(observer.getCurrentResult().isSuccess).toBe(true),
  );
  expect(observer.getCurrentResult().data?.pages[0].data).toEqual([]);
});

test('reconnect rereads changes made while the socket was disconnected', async () => {
  vi.useFakeTimers();
  let tasks: unknown[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => page(tasks)),
  );
  const { observer, states } = setup();
  Socket.instances[0].open();
  await vi.waitFor(() =>
    expect(observer.getCurrentResult().isFetching).toBe(false),
  );

  Socket.instances[0].disconnect();
  await vi.waitFor(() =>
    expect(observer.getCurrentResult().isFetching).toBe(false),
  );
  tasks = [task];
  expect(states).toHaveBeenLastCalledWith('reconnecting');
  await vi.advanceTimersByTimeAsync(1_000);
  expect(Socket.instances).toHaveLength(2);
  Socket.instances[1].open();

  await vi.waitFor(() =>
    expect(observer.getCurrentResult().data?.pages[0].data).toEqual([task]),
  );
  expect(states).toHaveBeenLastCalledWith('connected');
});

test('refreshes every 30 seconds only while visible and on focus or return to the tab', async () => {
  vi.useFakeTimers();
  let tasks: unknown[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => page(tasks)),
  );
  const { observer, document, browser } = setup();
  await vi.waitFor(() =>
    expect(observer.getCurrentResult().isSuccess).toBe(true),
  );
  document.visibilityState = 'hidden';
  tasks = [task];
  await vi.advanceTimersByTimeAsync(60_000);
  expect(observer.getCurrentResult().data?.pages[0].data).toEqual([]);

  document.visibilityState = 'visible';
  document.dispatchEvent(new Event('visibilitychange'));
  await vi.waitFor(() =>
    expect(observer.getCurrentResult().data?.pages[0].data).toEqual([task]),
  );
  tasks = [{ ...task, version: 2 }];
  await vi.advanceTimersByTimeAsync(30_000);
  await vi.waitFor(() =>
    expect(observer.getCurrentResult().data?.pages[0].data[0].version).toBe(2),
  );
  tasks = [{ ...task, version: 3 }];
  browser.dispatchEvent(new Event('focus'));
  await vi.waitFor(() =>
    expect(observer.getCurrentResult().data?.pages[0].data[0].version).toBe(3),
  );
});

test('policy denial clears private pages and blocks late reads and reconnect while session verification fails', async () => {
  vi.useFakeTimers();
  let finish!: (value: Response) => void;
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) =>
      path === '/api/v1/users/me'
        ? Promise.resolve(Response.json({}, { status: 401 }))
        : new Promise<Response>((resolve) => {
            finish = resolve;
          }),
    ),
  );
  const { client, states, browser } = setup();

  Socket.instances[0].disconnect(1008);
  finish(page([task]));
  await vi.advanceTimersByTimeAsync(60_000);
  browser.dispatchEvent(new Event('focus'));

  expect(states).toHaveBeenLastCalledWith('denied');
  expect(client.getQueryData(['tasks', 'owner', 'pending'])).toBeUndefined();
  expect(Socket.instances).toHaveLength(1);
  expect(client.getQueryData(['session'])).toBeNull();
});

test('refresh rebuilds the loaded window using fresh cursors without duplicate cards', async () => {
  let changed = false;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => {
      const cursor = new URL(path, 'https://botflow.example').searchParams.get(
        'cursor',
      );
      const result = cursor
        ? changed
          ? [{ ...task, id: 'older' }]
          : [{ ...task, id: 'second' }]
        : changed
          ? [{ ...task, id: 'new', created_at: '2026-01-02T00:00:00Z' }, task]
          : [task];
      if (cursor) expect(cursor).toBe(changed ? 'fresh-cursor' : 'old-cursor');
      return Response.json({
        data: result,
        meta: {
          has_more: !cursor,
          next_cursor: cursor ? null : changed ? 'fresh-cursor' : 'old-cursor',
        },
      });
    }),
  );
  const { observer, sync } = setup();
  await vi.waitFor(() =>
    expect(observer.getCurrentResult().isSuccess).toBe(true),
  );
  await observer.fetchNextPage();

  changed = true;
  await sync.refresh();

  expect(
    observer
      .getCurrentResult()
      .data?.pages.flatMap((page) => page.data.map((task) => task.id)),
  ).toEqual(['new', 'one', 'older']);
  expect(observer.getCurrentResult().data?.pageParams).toEqual([
    null,
    'fresh-cursor',
  ]);
});
