import {
  Alert,
  Box,
  Button,
  Chip,
  Container,
  Paper,
  Stack,
  Typography,
} from '@mui/material';
import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';

import { ApiError, listTasks } from './api';
import type { Member, TaskStatus } from './api';
import type { Locale, Messages } from './locale';
import { boardTasks } from './tasks';

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

  return (
    <Container component="main" maxWidth="lg" sx={{ py: { xs: 4, md: 6 } }}>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        {messages.workspace}
      </Typography>
      <Typography variant="h2" sx={{ mb: 1.5 }}>
        {messages.board}
      </Typography>
      <Typography color="text.secondary" sx={{ mb: 3 }}>
        {messages.boardDescription}
      </Typography>
      <Button
        color="secondary"
        variant="outlined"
        sx={{ mb: 3 }}
        disabled={columns.some(({ query }) => query.isFetching)}
        onClick={() => {
          void client.invalidateQueries({ queryKey: ['tasks', member.id] });
        }}
      >
        {messages.refreshBoard}
      </Button>
      {denied && <Alert severity="error">{messages.tasksDenied}</Alert>}
      {!denied && (
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: {
              xs: 'minmax(0, 1fr)',
              md: 'repeat(3, minmax(0, 1fr))',
            },
            gap: 2,
            alignItems: 'start',
          }}
        >
          {columns.map(({ status, label, query }) => {
            const items = tasks.filter((task) => task.status === status);
            return (
              <Paper
                component="section"
                aria-label={label}
                key={status}
                variant="outlined"
                sx={{ minWidth: 0, p: 2, bgcolor: 'background.default' }}
              >
                <Typography variant="h3" sx={{ mb: 2 }}>
                  {label}
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
                {!query.isPending && !query.isError && items.length === 0 && (
                  <Typography color="text.secondary">
                    {messages.tasksEmpty}
                  </Typography>
                )}
                <Stack
                  component="ul"
                  spacing={2}
                  sx={{ listStyle: 'none', m: 0, p: 0 }}
                >
                  {items.map((task) => (
                    <Paper
                      component="li"
                      variant="outlined"
                      key={task.id}
                      data-task-id={task.id}
                      sx={{ p: 2, minWidth: 0, overflowWrap: 'anywhere' }}
                    >
                      <Chip
                        size="small"
                        label={label}
                        sx={{
                          mb: 1,
                          maxWidth: '100%',
                          height: 'auto',
                          '& .MuiChip-label': { whiteSpace: 'normal' },
                        }}
                      />
                      <Typography sx={{ whiteSpace: 'pre-wrap', mb: 2 }}>
                        {task.text}
                      </Typography>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        {task.author_name}
                      </Typography>
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        component="time"
                        dateTime={task.created_at}
                      >
                        {dateFormat.format(new Date(task.created_at))}
                      </Typography>
                    </Paper>
                  ))}
                </Stack>
                {query.hasNextPage && (
                  <Button
                    color="secondary"
                    fullWidth
                    sx={{ mt: 2 }}
                    disabled={query.isFetching}
                    onClick={() =>
                      void query.fetchNextPage({ cancelRefetch: false })
                    }
                  >
                    {messages.showMore}
                  </Button>
                )}
              </Paper>
            );
          })}
        </Box>
      )}
    </Container>
  );
}
