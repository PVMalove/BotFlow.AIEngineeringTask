import { DndContext, pointerWithin } from '@dnd-kit/core';
import {
  Alert,
  Box,
  Button,
  Container,
  Stack,
  Typography,
} from '@mui/material';
import { Fragment } from 'react';
import type { Member } from './api';
import type { Locale, Messages } from './locale';
import { boardTasks } from './tasks';
import { TaskCard, TaskColumn } from './TaskCard';
import { TaskDialog } from './TaskDialog';
import { useBoard } from './useBoard';

export function Board({
  member,
  messages,
  locale,
}: {
  member: Member;
  messages: Messages;
  locale: Locale;
}) {
  const {
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
    activateTask,
  } = useBoard(member, messages);
  const dateFormat = new Intl.DateTimeFormat(locale, {
    dateStyle: 'medium',
    timeStyle: 'short',
  });

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
        <Typography variant="h2">{messages.brandName}</Typography>
        <Button
          color="secondary"
          variant="outlined"
          disabled={busy || columns.some(({ query }) => query.isFetching)}
          onClick={() => void refresh()}
        >
          {messages.refreshBoard}
        </Button>
      </Stack>
      <Typography
        role="status"
        aria-live="polite"
        color="text.secondary"
        sx={{ mb: 2 }}
      >
        {messages[connection]}
      </Typography>
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
                  ? messages.dragOver.replace(
                      '{column}',
                      columns.find((column) => column.status === over.id)
                        ?.label ?? '',
                    )
                  : undefined,
              onDragEnd: () => messages.dragEnded,
              onDragCancel: () => messages.dragCancelled,
            },
          }}
          onDragStart={startDrag}
          onDragOver={overDrag}
          onDragCancel={cancelDrag}
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
                  {/* Background reads must not move a handle under the pointer. */}
                  {query.isPending && query.isFetching && (
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
                          activate={activateTask}
                          task={task}
                          editable={mayEdit(task)}
                          busy={busy || blocked}
                          messages={messages}
                          dateFormat={dateFormat}
                          open={() => openTask(task)}
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
      {opened && !denied && (
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
