import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query';
import type { TaskStatus } from './api';
import { boardColumnOptions } from './boardRealtime';
import { isAccessDenied } from './errors';
import type { Messages } from './locale';

function useTaskColumn(memberId: string, status: TaskStatus) {
  return useInfiniteQuery(
    boardColumnOptions(useQueryClient(), memberId, status),
  );
}

// The three cursor-paginated columns of the board and whether any was refused.
export function useBoardColumns(memberId: string, messages: Messages) {
  const pending = useTaskColumn(memberId, 'pending');
  const inProgress = useTaskColumn(memberId, 'in_progress');
  const completed = useTaskColumn(memberId, 'completed');
  const columns = [
    { status: 'pending', label: messages.taskPending, query: pending },
    {
      status: 'in_progress',
      label: messages.taskInProgress,
      query: inProgress,
    },
    { status: 'completed', label: messages.taskCompleted, query: completed },
  ] as const;
  const refused = columns.some(({ query }) => isAccessDenied(query.error));
  return { columns, refused };
}
