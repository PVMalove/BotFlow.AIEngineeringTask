"""Board queries: every approved member sees every task of the shared board."""

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from application.ports import (
    InvalidCursor,
    MemberAuthenticator,
    PageCursor,
    TaskNotFound,
    TaskReader,
)
from application.session import authenticate
from domain.tasks import Task, TaskStatus

DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def encode_cursor(cursor: PageCursor) -> str:
    raw = json.dumps(
        {"c": cursor.created_at.isoformat(), "i": str(cursor.id)},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(value: str) -> PageCursor:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        data = json.loads(raw)
        created_at = datetime.fromisoformat(data["c"])
        if created_at.tzinfo is None:
            raise ValueError
        return PageCursor(created_at, UUID(data["i"]))
    except (ValueError, KeyError, TypeError):
        raise InvalidCursor from None


@dataclass(frozen=True, slots=True)
class TaskPage:
    items: list[Task]
    next_cursor: str | None
    has_more: bool


class ListTasks:
    def __init__(self, auth: MemberAuthenticator, reader: TaskReader) -> None:
        self._auth = auth
        self._reader = reader

    async def execute(
        self,
        token: str | None,
        *,
        status: TaskStatus | None = None,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> TaskPage:
        await authenticate(self._auth, token)
        after = decode_cursor(cursor) if cursor else None
        rows = await self._reader.list_page(status, after, limit + 1)
        items = rows[:limit]
        has_more = len(rows) > limit
        next_cursor = (
            encode_cursor(PageCursor(items[-1].created_at, items[-1].id))
            if has_more
            else None
        )
        return TaskPage(items, next_cursor, has_more)


class GetTask:
    def __init__(self, auth: MemberAuthenticator, reader: TaskReader) -> None:
        self._auth = auth
        self._reader = reader

    async def execute(self, token: str | None, task_id: UUID) -> Task:
        await authenticate(self._auth, token)
        task = await self._reader.get(task_id)
        if task is None:
            raise TaskNotFound
        return task
