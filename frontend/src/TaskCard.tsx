import { useDraggable, useDroppable } from '@dnd-kit/core';
import { Box, ButtonBase, IconButton, Paper, Typography } from '@mui/material';
import type { ReactNode } from 'react';
import type { Task, TaskStatus } from './api';
import type { Messages } from './locale';

export function TaskCard({
  task,
  editable,
  busy,
  messages,
  dateFormat,
  open,
  activate,
}: {
  task: Task;
  editable: boolean;
  busy: boolean;
  messages: Messages;
  dateFormat: Intl.DateTimeFormat;
  open: () => void;
  activate: () => void;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    isDragging,
  } = useDraggable({
    id: task.id,
    data: { task },
    disabled: !editable || busy,
  });
  return (
    <Paper
      component="li"
      variant="outlined"
      ref={setNodeRef}
      data-task-id={task.id}
      onPointerDownCapture={activate}
      onKeyDownCapture={activate}
      sx={{
        minWidth: 0,
        position: 'relative',
        overflowWrap: 'anywhere',
        opacity: isDragging ? 0.5 : 1,
        zIndex: isDragging ? 10 : 'auto',
        transform: transform
          ? 'translate3d(' + transform.x + 'px,' + transform.y + 'px,0)'
          : undefined,
      }}
    >
      <ButtonBase
        onClick={open}
        aria-label={task.text}
        sx={{
          display: 'block',
          width: '100%',
          textAlign: 'left',
          p: 2,
          borderRadius: 'inherit',
        }}
      >
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ fontSize: 11, pr: editable ? 4 : 0, mb: 1 }}
        >
          Telegram •{' '}
          <Box component="time" dateTime={task.created_at}>
            {dateFormat.format(new Date(task.created_at))}
          </Box>
        </Typography>
        <Typography
          sx={{
            whiteSpace: 'pre-wrap',
            fontSize: 15,
            fontWeight: 600,
            lineHeight: 1.5,
            mb: 1,
          }}
        >
          {task.text}
        </Typography>
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ fontSize: 12 }}
        >
          {task.author_name}
        </Typography>
      </ButtonBase>
      {editable && (
        <IconButton
          {...attributes}
          {...listeners}
          ref={setActivatorNodeRef}
          tabIndex={-1}
          aria-label={messages.taskMove + ': ' + task.text}
          disabled={busy}
          sx={{
            position: 'absolute',
            top: 3,
            right: 3,
            width: 40,
            height: 40,
            touchAction: 'none',
            cursor: 'grab',
            fontSize: 18,
          }}
        >
          <span aria-hidden="true">⠿</span>
        </IconButton>
      )}
    </Paper>
  );
}

export function TaskColumn({
  status,
  label,
  children,
}: {
  status: TaskStatus;
  label: string;
  children: ReactNode;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: status });
  return (
    <Paper
      component="section"
      ref={setNodeRef}
      aria-label={label}
      variant="outlined"
      sx={{
        minWidth: 0,
        p: 2,
        bgcolor: isOver ? '#F4E8CD' : 'background.default',
        borderColor: isOver ? 'primary.main' : 'divider',
        minHeight: 180,
      }}
    >
      {children}
    </Paper>
  );
}
