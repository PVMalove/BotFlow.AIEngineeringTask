import { isAccessDenied } from './errors';
import { PointerSensor, useSensor, useSensors } from '@dnd-kit/core';
import type {
  DragEndEvent,
  DragStartEvent,
  DragOverEvent,
} from '@dnd-kit/core';
import {
  skipToken,
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import type { InfiniteData } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { ApiError, changeTaskStatus, getTask } from './api';
import type { Member, Task, TaskPage, TaskStatus } from './api';
import type { Messages } from './locale';
import { boardTasks } from './tasks';
import { boardColumnOptions, knownBoardTasks } from './boardRealtime';
import { useBoardRealtime } from './useBoardRealtime';

function useTaskColumn(memberId: string, status: TaskStatus) {
  return useInfiniteQuery(
    boardColumnOptions(useQueryClient(), memberId, status),
  );
}

type Notice =
  | 'taskConflict'
  | 'taskUnknown'
  | 'taskReconcileFailed'
  | 'taskSaveFailed'
  | 'taskMissing'
  | 'accessDenied'
  | 'rateLimited';

type TaskOperation = {
  attemptId: symbol;
  task: Task;
  phase: 'saving' | 'blocked';
  notice: Notice | null;
};

export function useBoard(member: Member, messages: Messages) {
  const client = useQueryClient();
  const { connection, refresh } = useBoardRealtime(member.id);
  const key = ['tasks', member.id];
  const pending = useTaskColumn(member.id, 'pending');
  const inProgress = useTaskColumn(member.id, 'in_progress');
  const completed = useTaskColumn(member.id, 'completed');
  const columns = [
    { status: 'pending', label: messages.taskPending, query: pending },
    {
      status: 'in_progress',
      label: messages.taskInProgress,
      query: inProgress,
    },
    { status: 'completed', label: messages.taskCompleted, query: completed },
  ] as const;
  const [opened, setOpened] = useState<Task | null>(null);
  const [selected, setSelected] = useState<TaskStatus>('pending');
  // This session-scoped state must survive Board unmounting during navigation.
  // SessionRoutes removes it along with other private cache entries on logout.
  const operationKey = ['task-operation', member.id] as const;
  const { data: operation } = useQuery<TaskOperation | null>({
    queryKey: operationKey,
    queryFn: skipToken,
    initialData: null,
    gcTime: Infinity,
  });
  const busy = operation?.phase === 'saving';
  const blocked = operation?.phase === 'blocked';
  const [localNotice, setNotice] = useState<Notice | null>(null);
  const notice = operation?.notice ?? localNotice;
  const setOperation = (next: TaskOperation | null) =>
    client.setQueryData(operationKey, next);
  const [dragged, setDragged] = useState<Task | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const suppressClick = useRef(false);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
  );
  const mayEdit = (task: Task) =>
    member.role === 'admin' || member.id === task.author_id;
  const denied =
    connection === 'denied' ||
    [pending.error, inProgress.error, completed.error].some((error) =>
      isAccessDenied(error),
    );
  useEffect(() => {
    if (denied && connection !== 'denied')
      void client.invalidateQueries({ queryKey: ['session'] });
  }, [client, denied, connection]);
  const observedTasks = boardTasks(
    columns.flatMap(
      ({ query }) => query.data?.pages.flatMap((page) => page.data) ?? [],
    ),
  );
  // Keep the active source mounted until drop/cancel. Realtime may move its
  // cached version meanwhile; the command still uses the drag-start snapshot.
  const tasks = dragged
    ? boardTasks([
        ...observedTasks.filter((task) => task.id !== dragged.id),
        dragged,
      ])
    : observedTasks;

  function beginOperation(
    task: Task,
    notice: Notice | null = null,
  ): TaskOperation {
    const attempt: TaskOperation = {
      attemptId: Symbol(),
      task,
      phase: 'saving',
      notice,
    };
    setOperation(attempt);
    return attempt;
  }

  function ownsOperation(attempt: TaskOperation) {
    return (
      client.getQueryData<TaskOperation | null>(operationKey)?.attemptId ===
      attempt.attemptId
    );
  }

  function finishOperation(attempt: TaskOperation) {
    const current = client.getQueryData<TaskOperation | null>(operationKey);
    if (current?.phase === 'saving' && current.attemptId === attempt.attemptId)
      setOperation(null);
  }

  function putTask(task: Task | null, id: string) {
    // A delayed mutation/GET must not replace a newer REST (or future WS) version.
    const known = knownBoardTasks(client, member.id).find(
      (item) => item.id === id,
    );
    if (task && known && known.version > task.version) task = known;
    for (const { status } of columns) {
      client.setQueryData<InfiniteData<TaskPage>>(
        [...key, status],
        (previous) => {
          if (!previous) return previous;
          return {
            ...previous,
            pages: previous.pages.map((page, index) => ({
              ...page,
              data: boardTasks([
                ...page.data.filter((item) => item.id !== id),
                ...(index === 0 && task?.status === status ? [task] : []),
              ]),
            })),
          };
        },
      );
    }
    return task;
  }

  async function reconcile(attempt: TaskOperation, reason: Notice) {
    const { task } = attempt;
    try {
      const fresh = await getTask(task.id);
      if (!ownsOperation(attempt)) return;
      await client.cancelQueries({ queryKey: key });
      if (!ownsOperation(attempt)) return;
      const observed = putTask(fresh, task.id) ?? fresh;
      setOpened((current) =>
        current?.id === observed.id ? observed : current,
      );
      // A second command requires the user to select again against the observed version.
      setSelected(observed.status);
      setNotice(reason);
    } catch (error) {
      if (!ownsOperation(attempt)) return;
      if (error instanceof ApiError && error.status === 404) {
        putTask(null, task.id);
        setNotice('taskMissing');
        setOpened(null);
      } else {
        setOperation({
          ...attempt,
          phase: 'blocked',
          notice: isAccessDenied(error)
            ? 'accessDenied'
            : 'taskReconcileFailed',
        });
        if (isAccessDenied(error))
          void client.invalidateQueries({ queryKey: ['session'] });
      }
    }
    void client.invalidateQueries({ queryKey: key });
  }

  async function save(task: Task, status: TaskStatus) {
    if (client.getQueryData(operationKey) || !mayEdit(task)) return;
    const attempt = beginOperation(task);
    setNotice(null);
    try {
      await client.cancelQueries({ queryKey: key });
      if (!ownsOperation(attempt)) return;
      const changed = await changeTaskStatus(task, status);
      if (!ownsOperation(attempt)) return;
      putTask(changed, task.id);
      setOpened(null);
      await client.invalidateQueries({ queryKey: key });
    } catch (error) {
      if (!ownsOperation(attempt)) return;
      if (error instanceof ApiError && error.status === 409) {
        await reconcile(attempt, 'taskConflict');
      } else if (
        !(error instanceof ApiError) ||
        error.status === 0 ||
        error.status >= 500
      ) {
        await reconcile(attempt, 'taskUnknown');
      } else if (error.status === 403) {
        await reconcile(attempt, 'accessDenied');
        if (ownsOperation(attempt))
          void client.invalidateQueries({ queryKey: ['session'] });
      } else if (error.status === 404) {
        await reconcile(attempt, 'taskMissing');
      } else {
        setNotice(
          isAccessDenied(error)
            ? 'accessDenied'
            : error.status === 429
              ? 'rateLimited'
              : 'taskSaveFailed',
        );
        if (isAccessDenied(error))
          void client.invalidateQueries({ queryKey: ['session'] });
      }
    } finally {
      finishOperation(attempt);
    }
  }

  async function retryReconcile() {
    const current = client.getQueryData<TaskOperation | null>(operationKey);
    if (current?.phase !== 'blocked') return;
    const attempt = beginOperation(current.task, current.notice);
    try {
      await reconcile(attempt, 'taskUnknown');
    } finally {
      finishOperation(attempt);
    }
  }

  function endDrag(event: DragEndEvent) {
    setDragged(null);
    setOver(null);
    // Reset only on the next user activation; the trailing click stays suppressed.
    suppressClick.current = true;
    const task = dragged;
    const target = columns.find(
      (column) => column.status === event.over?.id,
    )?.status;
    if (task && target && task.status !== target) void save(task, target);
  }

  function startDrag({ active }: DragStartEvent) {
    setDragged(active.data.current?.task as Task);
    suppressClick.current = true;
  }

  function overDrag({ over }: DragOverEvent) {
    setOver(over ? String(over.id) : null);
  }

  function cancelDrag() {
    setDragged(null);
    setOver(null);
    suppressClick.current = true;
  }

  function openTask(task: Task) {
    if (
      suppressClick.current ||
      busy ||
      (blocked && operation?.task.id !== task.id)
    )
      return;
    setOpened(task);
    setSelected(task.status);
    if (!blocked) setNotice(null);
  }

  return {
    connection,
    refresh,
    columns,
    tasks,
    dragged,
    over,
    sensors,
    endDrag,
    busy,
    blocked,
    notice,
    denied,
    opened,
    selected,
    mayEdit,
    retryReconcile,
    save,
    setOpened,
    setSelected,
    startDrag,
    overDrag,
    cancelDrag,
    openTask,
    activateTask: () => {
      suppressClick.current = false;
    },
  };
}
