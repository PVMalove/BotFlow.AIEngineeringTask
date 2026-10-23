import { expect, test } from 'vitest';
import type { Task } from './api';
import { boardTasks } from './tasks';

const task: Task = {
  id: '00000000-0000-0000-0000-000000000001',
  text: 'Текст / Տեքստ',
  source_type: null,
  author_id: 'author',
  author_name: 'Анна',
  status: 'pending',
  version: 1,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

test('overlapping pages and columns keep one copy with the freshest version', () => {
  const current = { ...task, status: 'completed' as const, version: 2 };
  expect(boardTasks([task, current, task, current])).toEqual([current]);
});

test('sorts by creation time and descending ID, independently of fetch order', () => {
  const tie = { ...task, id: '00000000-0000-0000-0000-000000000002' };
  const newer = {
    ...task,
    id: '00000000-0000-0000-0000-000000000003',
    created_at: '2026-01-02T00:00:00Z',
  };
  expect(boardTasks([task, newer, tie, task]).map(({ id }) => id)).toEqual([
    newer.id,
    tie.id,
    task.id,
  ]);
});

test('preserves REST microsecond ordering even within the same millisecond', () => {
  const newer = { ...task, created_at: '2026-01-01T00:00:00.000002Z' };
  const older = {
    ...task,
    id: 'ffffffff-ffff-ffff-ffff-ffffffffffff',
    created_at: '2026-01-01T00:00:00.000001Z',
  };
  expect(boardTasks([older, newer])).toEqual([newer, older]);
});
