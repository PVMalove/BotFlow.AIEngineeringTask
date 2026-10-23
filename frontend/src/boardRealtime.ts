import {
  infiniteQueryOptions,
  type InfiniteData,
  type QueryClient,
} from '@tanstack/react-query';

import {
  ApiError,
  getCurrentMember,
  listTasks,
  type Task,
  type TaskPage,
  type TaskStatus,
} from './api';
import { boardTasks } from './tasks';

export type BoardConnection =
  'connecting' | 'connected' | 'reconnecting' | 'denied';

export function knownBoardTasks(client: QueryClient, memberId: string): Task[] {
  return boardTasks([
    ...(client.getQueryData<Task[]>(['board-versions', memberId]) ?? []),
    ...client
      .getQueriesData<InfiniteData<TaskPage>>({ queryKey: ['tasks', memberId] })
      .flatMap(([, data]) => data?.pages.flatMap((page) => page.data) ?? []),
  ]);
}

export function boardColumnOptions(
  client: QueryClient,
  memberId: string,
  status: TaskStatus,
) {
  return infiniteQueryOptions({
    queryKey: ['tasks', memberId, status],
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => {
      const before = new Map(
        knownBoardTasks(client, memberId).map((task) => [
          task.id,
          task.version,
        ]),
      );
      const page = await listTasks(status, pageParam, signal);
      const known = new Map(
        knownBoardTasks(client, memberId).map((task) => [task.id, task]),
      );
      const data = page.data.map((task) => {
        const current = known.get(task.id);
        return current && current.version > task.version ? current : task;
      });
      // Keep newer writes that arrived after this read started, including a
      // mutation inserting a card while an older empty first page was in flight.
      if (pageParam === null)
        for (const task of known.values()) {
          if (task.version > (before.get(task.id) ?? 0)) data.push(task);
        }
      return {
        ...page,
        data: boardTasks(data).filter((task) => task.status === status),
      };
    },
    getNextPageParam: (page) =>
      page.meta.has_more ? page.meta.next_cursor : undefined,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    enabled: () => client.getQueryData(['board-access', memberId]) !== false,
  });
}

export function startBoardRealtime(
  client: QueryClient,
  memberId: string,
  notify: (state: BoardConnection) => void,
): { refresh: () => Promise<void>; stop: () => void } {
  const queryKey = ['tasks', memberId];
  let stopped = false;
  let denied = false;
  let dirty = false;
  let running: Promise<void> | undefined;
  let socket: WebSocket | undefined;
  let reconnect: ReturnType<typeof setTimeout> | undefined;
  let attempt = 0;
  client.setQueryDefaults(['board-versions', memberId], { gcTime: Infinity });
  // Remember observed versions even when a later page no longer contains the
  // card. SessionRoutes removes this cache with all private data on logout.
  const forgetVersions = client.getQueryCache().subscribe((event) => {
    if (
      event.type !== 'updated' ||
      event.action.type !== 'success' ||
      event.query.queryKey[0] !== 'tasks' ||
      event.query.queryKey[1] !== memberId
    )
      return;
    const data = event.query.state.data as InfiniteData<TaskPage> | undefined;
    if (!data) return;
    client.setQueryData<Task[]>(['board-versions', memberId], (previous = []) =>
      boardTasks([...previous, ...data.pages.flatMap((page) => page.data)]),
    );
  });

  function deny() {
    denied = true;
    clearTimeout(reconnect);
    const previous = socket;
    socket = undefined;
    previous?.close();
    client.setQueryData(['board-access', memberId], false);
    notify('denied');
    void client.cancelQueries({ queryKey });
    client.removeQueries({ queryKey });
    client.removeQueries({ queryKey: ['board-versions', memberId] });
  }

  async function refresh() {
    if (stopped || denied) return;
    dirty = true;
    if (running) return running;
    running = (async () => {
      while (dirty && !stopped && !denied) {
        // Joining an existing read cannot acknowledge a newer change signal.
        dirty = client.isFetching({ queryKey }) > 0;
        await client.invalidateQueries({ queryKey }, { cancelRefetch: false });
        if (stopped) return;
        const refused = client
          .getQueryCache()
          .findAll({ queryKey })
          .some(
            ({ state }) =>
              state.error instanceof ApiError &&
              [401, 403].includes(state.error.status),
          );
        if (refused) {
          deny();
          void client.invalidateQueries({ queryKey: ['session'] });
        }
      }
    })().finally(() => {
      running = undefined;
    });
    return running;
  }

  async function checkSession() {
    try {
      await client.cancelQueries({ queryKey: ['session'] });
      if (stopped) return;
      const member = await client.fetchQuery({
        queryKey: ['session'],
        queryFn: async () => {
          try {
            return await getCurrentMember();
          } catch (error) {
            if (error instanceof ApiError && error.status === 401) return null;
            throw error;
          }
        },
        staleTime: 0,
        retry: false,
      });
      if (
        stopped ||
        !member ||
        member.id !== memberId ||
        member.status !== 'approved'
      )
        return;
      denied = false;
      client.setQueryData(['board-access', memberId], true);
      connect();
      void refresh();
    } catch {
      // The board remains hidden until the current session can be confirmed.
    }
  }

  function connect() {
    if (stopped || denied) return;
    notify(attempt ? 'reconnecting' : 'connecting');
    const url = new URL('/ws/board', window.location.href);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    const current = new WebSocket(url.href);
    socket = current;
    current.addEventListener('open', () => {
      if (stopped || current !== socket) return;
      attempt = 0;
      client.setQueryData(['board-access', memberId], true);
      notify('connected');
      void refresh();
    });
    current.addEventListener('message', (event) => {
      if (stopped || denied || current !== socket) return;
      try {
        const frame: unknown = JSON.parse(event.data as string);
        if (
          frame &&
          typeof frame === 'object' &&
          'type' in frame &&
          frame.type === 'board.changed'
        )
          void refresh();
      } catch {
        /* Invalid frames carry no board state. */
      }
    });
    current.addEventListener('close', (event) => {
      if (stopped || current !== socket) return;
      if (event.code === 1008) {
        deny();
        void checkSession();
        return;
      }
      notify('reconnecting');
      // A failed handshake is opaque in browsers. REST resolves access denial.
      void refresh();
      reconnect = setTimeout(connect, Math.min(1_000 * 2 ** attempt++, 30_000));
    });
  }

  const resume = () => {
    if (document.visibilityState === 'visible') void refresh();
  };
  window.addEventListener('focus', resume);
  window.addEventListener('online', resume);
  document.addEventListener('visibilitychange', resume);
  const interval = setInterval(resume, 30_000);
  connect();
  return {
    refresh,
    stop: () => {
      stopped = true;
      clearTimeout(reconnect);
      clearInterval(interval);
      socket?.close();
      forgetVersions();
      window.removeEventListener('focus', resume);
      window.removeEventListener('online', resume);
      document.removeEventListener('visibilitychange', resume);
    },
  };
}
