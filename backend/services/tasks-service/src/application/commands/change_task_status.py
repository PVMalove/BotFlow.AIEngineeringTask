"""ChangeTaskStatus command: the author or an admin moves a task between statuses."""

from uuid import UUID

from application.ports import (
    Forbidden,
    MemberAuthenticator,
    TaskNotFound,
    TaskReader,
    TaskStatusStore,
)
from application.session import authenticate
from domain.tasks import Task, TaskStatus, may_change_status


class ChangeTaskStatus:
    def __init__(
        self, auth: MemberAuthenticator, reader: TaskReader, store: TaskStatusStore
    ) -> None:
        self._auth = auth
        self._reader = reader
        self._store = store

    async def execute(
        self,
        token: str | None,
        task_id: UUID,
        target: TaskStatus,
        expected_version: int,
    ) -> Task:
        actor = await authenticate(self._auth, token)
        task = await self._reader.get(task_id)
        if task is None:
            raise TaskNotFound
        if not may_change_status(actor, task):
            raise Forbidden
        # Only the store can compare the version atomically with the update.
        return await self._store.change(task_id, target, expected_version)
