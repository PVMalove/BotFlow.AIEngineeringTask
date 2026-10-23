import { PointerSensor, useSensor, useSensors } from '@dnd-kit/core';
import type {
  DragEndEvent,
  DragOverEvent,
  DragStartEvent,
} from '@dnd-kit/core';
import { useRef, useState } from 'react';
import type { Task, TaskStatus } from './api';

// The column a drop moves the task to; none when it lands outside or where it was.
export function dropTarget(
  statuses: readonly TaskStatus[],
  task: Task | null,
  overId: unknown,
): TaskStatus | null {
  const target = statuses.find((status) => status === overId) ?? null;
  return task && target && task.status !== target ? target : null;
}

// Drag-and-drop state; a completed drop is handed to `onDrop` with the drag-start
// snapshot of the task.
export function useBoardDrag(
  statuses: readonly TaskStatus[],
  onDrop: (task: Task, target: TaskStatus) => void,
) {
  const [dragged, setDragged] = useState<Task | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const suppressClick = useRef(false);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
  );

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

  function endDrag(event: DragEndEvent) {
    setDragged(null);
    setOver(null);
    // Reset only on the next user activation; the trailing click stays suppressed.
    suppressClick.current = true;
    const target = dropTarget(statuses, dragged, event.over?.id);
    if (dragged && target) onDrop(dragged, target);
  }

  return {
    dragged,
    over,
    sensors,
    startDrag,
    overDrag,
    cancelDrag,
    endDrag,
    isClickSuppressed: () => suppressClick.current,
    activateTask: () => {
      suppressClick.current = false;
    },
  };
}
