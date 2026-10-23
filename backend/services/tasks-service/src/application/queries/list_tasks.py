"""ListTasks query: the board, newest first, keyset-paginated."""

from dataclasses import dataclass

from kernel_domain.result import Result
from kernel_platform.pagination import (
    InvalidCursorError,
    Page,
    PageInfo,
    decode_cursor,
    encode_cursor,
)

from application.ports import TaskReader
from contracts.task import TaskView
from domain.errors import TaskErrors
from domain.value_objects.task_status import TaskStatus


@dataclass(frozen=True)
class ListTasksQuery:
    status: TaskStatus | None
    limit: int
    cursor: str | None


class ListTasksQueryHandler:
    def __init__(self, reader: TaskReader) -> None:
        self._reader = reader

    async def execute(self, query: ListTasksQuery) -> Result[Page[TaskView]]:
        try:
            after = decode_cursor(query.cursor) if query.cursor else None
        except InvalidCursorError:
            return Result[Page[TaskView]].fail(TaskErrors.invalid_cursor())
        rows = await self._reader.list_page(query.status, after, query.limit + 1)
        items = rows[: query.limit]
        has_more = len(rows) > query.limit
        next_cursor = (
            encode_cursor(items[-1].created_at, items[-1].id.value)
            if has_more
            else None
        )
        return Result[Page[TaskView]].ok(
            Page(
                items=[TaskView.from_domain(task) for task in items],
                page_info=PageInfo(next_cursor=next_cursor, has_more=has_more),
            )
        )
