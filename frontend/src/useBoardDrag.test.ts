import { expect, test } from 'vitest';

import type { Task } from './api';
import { dropTarget } from './useBoardDrag';

const task: Task = {
  id: 'task-1',
  text: 'Купить молоко',
  status: 'pending',
  version: 3,
  author_id: 'member-1',
  author_name: 'Анна',
  source_type: 'text',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};
const statuses = ['pending', 'in_progress', 'completed'] as const;

test('a drop on another column targets that column', () => {
  expect(dropTarget(statuses, task, 'in_progress')).toBe('in_progress');
});

test('a drop on the same column changes nothing', () => {
  expect(dropTarget(statuses, task, 'pending')).toBeNull();
});

test('a drop outside the columns or of nothing changes nothing', () => {
  expect(dropTarget(statuses, task, undefined)).toBeNull();
  expect(dropTarget(statuses, task, 'unknown-column')).toBeNull();
  expect(dropTarget(statuses, null, 'completed')).toBeNull();
});
