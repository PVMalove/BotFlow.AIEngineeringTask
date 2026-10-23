import {
  DndContext,
  PointerSensor,
  pointerWithin,
  useSensor,
  useSensors,
} from '@dnd-kit/core';
import type { DragEndEvent } from '@dnd-kit/core';
import {
  Alert,
  Box,
  Button,
  Container,
  Stack,
  Typography,
} from '@mui/material';
import {
  skipToken,
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import type { InfiniteData } from '@tanstack/react-query';
import { Fragment, useEffect, useRef, useState } from 'react';

import { ApiError, changeTaskStatus, getTask, listTasks } from './api';
import type { Member, Task, TaskPage, TaskStatus } from './api';
import type { Locale, Messages } from './locale';
import { boardTasks } from './tasks';
import { TaskCard, TaskColumn } from './TaskCard';
import { TaskDialog } from './TaskDialog';

function useTaskColumn(memberId: string, status: TaskStatus) {
  return useInfiniteQuery({
    queryKey: ['tasks', memberId, status],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => listTasks(status, pageParam, signal),
    getNextPageParam: (page) =>
      page.meta.has_more ? page.meta.next_cursor : undefined,
    retry: false,
    refetchInterval: 30_000,
    refetchOnWindowFocus: 'always',
    refetchOnReconnect: 'always',
  });
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

export function Board({
  member,
  messages,
  locale,
}: {
  member: Member;
  messages: Messages;
  locale: Locale;
}) {
  const client = useQueryClient();
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
  const denied = [pending.error, inProgress.error, completed.error].some(
    (error) => error instanceof ApiError && [401, 403].includes(error.status),
  );
  useEffect(() => {
    if (denied) void client.invalidateQueries({ queryKey: ['session'] });
  }, [client, denied]);
  const tasks = boardTasks(
    columns.flatMap(
      ({ query }) => query.data?.pages.flatMap((page) => page.data) ?? [],
    ),
  );
  const dateFormat = new Intl.DateTimeFormat(locale, {
    dateStyle: 'medium',
    timeStyle: 'short',
  });

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
    const known = boardTasks(
      client
        .getQueriesData<InfiniteData<TaskPage>>({ queryKey: key })
        .flatMap(([, data]) => data?.pages.flatMap((page) => page.data) ?? []),
    ).find((item) => item.id === id);
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
          notice:
            error instanceof ApiError && [401, 403].includes(error.status)
              ? 'accessDenied'
              : 'taskReconcileFailed',
        });
        if (error instanceof ApiError && [401, 403].includes(error.status))
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
          error instanceof ApiError && [401, 403].includes(error.status)
            ? 'accessDenied'
            : error.status === 429
              ? 'rateLimited'
              : 'taskSaveFailed',
        );
        if (error instanceof ApiError && [401, 403].includes(error.status))
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

  return (
    <Container component="main" maxWidth="lg" sx={{ py: { xs: 3, md: 4 } }}>
      <Stack
        direction="row"
        useFlexGap
        sx={{
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: 2,
          mb: 3,
          flexWrap: 'wrap',
        }}
      >
        <Typography variant="h2">{messages.defaultBoardName}</Typography>
        <Button
          color="secondary"
          variant="outlined"
          disabled={busy || columns.some(({ query }) => query.isFetching)}
          onClick={() => void client.invalidateQueries({ queryKey: key })}
        >
          {messages.refreshBoard}
        </Button>
      </Stack>
      {notice && !opened && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {messages[notice]}
          {blocked && (
            <Button disabled={busy} onClick={() => void retryReconcile()}>
              {messages.retry}
            </Button>
          )}
        </Alert>
      )}
      {denied && <Alert severity="error">{messages.tasksDenied}</Alert>}
      {!denied && (
        <DndContext
          sensors={sensors}
          collisionDetection={pointerWithin}
          accessibility={{
            screenReaderInstructions: { draggable: messages.dragInstructions },
            announcements: {
              onDragStart: () => messages.dragStarted,
              onDragOver: ({ over }) =>
                over
                  ? messages.dragOver +
                    ': ' +
                    columns.find((column) => column.status === over.id)?.label
                  : undefined,
              onDragEnd: () => messages.dragEnded,
              onDragCancel: () => messages.dragCancelled,
            },
          }}
          onDragStart={({ active }) => {
            setDragged(active.data.current?.task as Task);
            suppressClick.current = true;
          }}
          onDragOver={({ over }) => setOver(over ? String(over.id) : null)}
          onDragCancel={() => {
            setDragged(null);
            setOver(null);
            suppressClick.current = true;
          }}
          onDragEnd={endDrag}
        >
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: {
                xs: 'minmax(0, 1fr)',
                md: 'repeat(3, minmax(0, 1fr))',
              },
              gap: 3,
              alignItems: 'start',
            }}
          >
            {columns.map(({ status, label, query }) => {
              const items = tasks.filter((task) => task.status === status);
              const preview =
                dragged && dragged.status !== status && over === status;
              const position = preview
                ? boardTasks([...items, dragged]).findIndex(
                    (task) => task.id === dragged.id,
                  )
                : -1;
              const placeholder = (
                <Box
                  component="li"
                  role="status"
                  sx={{
                    listStyle: 'none',
                    border: '1px dashed',
                    borderColor: 'primary.main',
                    borderRadius: 2,
                    p: 2,
                    color: 'text.secondary',
                    fontSize: 13,
                  }}
                >
                  {messages.taskDrop}
                </Box>
              );
              return (
                <TaskColumn key={status} status={status} label={label}>
                  <Typography
                    variant="h3"
                    title={messages.loadedCount}
                    sx={{ mb: 2, fontSize: 15 }}
                  >
                    {label} (
                    {query.isPending ? '…' : query.isError ? '?' : items.length}
                    {query.hasNextPage ? '+' : ''})
                  </Typography>
                  {query.isFetching && (
                    <Typography role="status" sx={{ mb: 2 }}>
                      {messages.tasksLoading}
                    </Typography>
                  )}
                  {query.isError && (
                    <Alert severity="error" sx={{ mb: 2 }}>
                      {messages.tasksLoadFailed}
                      <Button
                        color="secondary"
                        disabled={query.isFetching}
                        onClick={() => {
                          if (query.isFetchNextPageError)
                            void query.fetchNextPage({ cancelRefetch: false });
                          else void query.refetch();
                        }}
                      >
                        {messages.retry}
                      </Button>
                    </Alert>
                  )}
                  {!query.isPending &&
                    !query.isError &&
                    items.length === 0 &&
                    !preview && (
                      <Typography color="text.secondary">
                        {messages.tasksEmpty}
                      </Typography>
                    )}
                  <Stack
                    component="ul"
                    spacing={1.5}
                    sx={{ listStyle: 'none', m: 0, p: 0 }}
                  >
                    {items.map((task, index) => (
                      <Fragment key={task.id}>
                        {position === index && placeholder}
                        <TaskCard
                          activate={() => {
                            suppressClick.current = false;
                          }}
                          task={task}
                          editable={mayEdit(task)}
                          busy={busy || blocked}
                          messages={messages}
                          dateFormat={dateFormat}
                          open={() => {
                            if (
                              suppressClick.current ||
                              busy ||
                              (blocked && operation?.task.id !== task.id)
                            )
                              return;
                            setOpened(task);
                            setSelected(task.status);
                            if (!blocked) setNotice(null);
                          }}
                        />
                      </Fragment>
                    ))}
                  </Stack>
                  {position === items.length && placeholder}
                  {query.hasNextPage && (
                    <Button
                      color="secondary"
                      fullWidth
                      sx={{ mt: 2 }}
                      disabled={query.isFetching || busy}
                      onClick={() =>
                        void query.fetchNextPage({ cancelRefetch: false })
                      }
                    >
                      {messages.showMore}
                    </Button>
                  )}
                </TaskColumn>
              );
            })}
          </Box>
        </DndContext>
      )}
      {opened && (
        <TaskDialog
          task={opened}
          selected={selected}
          setSelected={setSelected}
          editable={mayEdit(opened)}
          busy={busy}
          blocked={blocked}
          error={notice ? messages[notice] : null}
          messages={messages}
          dateFormat={dateFormat}
          close={() => setOpened(null)}
          save={() => {
            void save(opened, selected);
          }}
          retry={() => void retryReconcile()}
        />
      )}
    </Container>
  );
}
