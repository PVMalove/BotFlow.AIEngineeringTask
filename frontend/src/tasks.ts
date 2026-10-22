import type { Task } from './api';

function subMillisecond(value: string): number {
  // PostgreSQL timestamps carry microseconds; Date.parse retains only milliseconds.
  return Number((value.match(/\.(\d+)/)?.[1] ?? '').padEnd(6, '0').slice(3, 6));
}

// Refreshed keyset pages can temporarily overlap. Keep one copy and prefer the
// latest version across columns while their independent REST reads complete.
export function boardTasks(tasks: Task[]): Task[] {
  const unique = new Map<string, Task>();
  for (const task of tasks) {
    const previous = unique.get(task.id);
    if (!previous || task.version > previous.version) unique.set(task.id, task);
  }
  return [...unique.values()].sort(
    (a, b) =>
      Date.parse(b.created_at) - Date.parse(a.created_at) ||
      subMillisecond(b.created_at) - subMillisecond(a.created_at) ||
      (a.id < b.id ? 1 : a.id > b.id ? -1 : 0),
  );
}
