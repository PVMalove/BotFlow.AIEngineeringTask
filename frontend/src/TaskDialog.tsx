import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import type { Task, TaskStatus } from './api';
import type { Messages } from './locale';
import { TaskSource } from './TaskSource';

export function TaskDialog({
  task,
  selected,
  setSelected,
  editable,
  busy,
  blocked,
  error,
  messages,
  dateFormat,
  close,
  save,
  retry,
}: {
  task: Task;
  selected: TaskStatus;
  setSelected: (status: TaskStatus) => void;
  editable: boolean;
  busy: boolean;
  blocked: boolean;
  error: string | null;
  messages: Messages;
  dateFormat: Intl.DateTimeFormat;
  close: () => void;
  save: () => void;
  retry: () => void;
}) {
  return (
    <Dialog
      open
      fullWidth
      maxWidth="sm"
      aria-labelledby="task-dialog-title"
      onClose={() => {
        if (!busy) close();
      }}
    >
      <DialogTitle id="task-dialog-title">{messages.taskDetails}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <Typography
            sx={{
              whiteSpace: 'pre-wrap',
              overflowWrap: 'anywhere',
              fontWeight: 600,
            }}
          >
            {task.text}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {messages.taskSource}:{' '}
            <TaskSource source={task.source_type} messages={messages} />
            <br />
            {messages.taskDate}: {dateFormat.format(new Date(task.created_at))}
            <br />
            {messages.taskAuthor}: {task.author_name}
          </Typography>
          {!editable && <Alert severity="info">{messages.taskReadOnly}</Alert>}
          <TextField
            select
            label={messages.taskStatus}
            value={selected}
            disabled={!editable || busy || blocked}
            onChange={(event) => setSelected(event.target.value as TaskStatus)}
            slotProps={{ select: { native: true } }}
          >
            <option value="pending">{messages.taskPending}</option>
            <option value="in_progress">{messages.taskInProgress}</option>
            <option value="completed">{messages.taskCompleted}</option>
          </TextField>
          {error && <Alert severity="error">{error}</Alert>}
          {blocked && !busy && (
            <Button onClick={retry}>{messages.retry}</Button>
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ p: 3, gap: 1 }}>
        <Button color="secondary" onClick={close} disabled={busy}>
          {messages.cancel}
        </Button>
        {editable && (
          <Button variant="contained" onClick={save} disabled={busy || blocked}>
            {busy ? messages.savingTask : messages.save}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}
