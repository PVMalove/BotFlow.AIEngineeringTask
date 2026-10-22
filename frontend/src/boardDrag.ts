import type { KeyboardCoordinateGetter } from '@dnd-kit/core';

const statuses = ['pending', 'in_progress', 'completed'];

// Navigate by column rather than by arbitrary pixel increments, including the
// vertical mobile layout. The library still owns drag state, focus and scrolling.
export const columnKeyboardCoordinates: KeyboardCoordinateGetter = (
  event,
  { currentCoordinates, context },
) => {
  const direction =
    event.code === 'ArrowRight' || event.code === 'ArrowDown'
      ? 1
      : event.code === 'ArrowLeft' || event.code === 'ArrowUp'
        ? -1
        : 0;
  if (!direction || !context.collisionRect) return;
  event.preventDefault();
  const current = String(
    context.over?.id ?? context.active?.data.current?.task.status,
  );
  const next = statuses[statuses.indexOf(current) + direction];
  if (!next) return;
  const target = context.droppableRects.get(next);
  if (!target) return;
  const moving = context.collisionRect;
  return {
    x:
      currentCoordinates.x +
      target.left +
      target.width / 2 -
      moving.left -
      moving.width / 2,
    y:
      currentCoordinates.y +
      target.top +
      target.height / 2 -
      moving.top -
      moving.height / 2,
  };
};
