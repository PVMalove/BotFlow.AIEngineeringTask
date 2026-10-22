"""CreateTask command: one source message becomes at most one Pending task."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from botflow_platform.consumer import InvalidMessage
from botflow_platform.messaging import TASK_CREATED_ROUTING_KEY

from application.ports import NewTask, TaskCommandStore, TaskCreatedMessage
from domain.tasks import created_message_id_for, source_key, task_id_for

COMMAND_TYPE = "CreateTask"
RESULT_TYPE = "TaskCreated"
ENVELOPE_VERSION = 1
MAX_TEXT = 4096
MAX_NAME = 64


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError
    return value


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= limit:
        raise ValueError
    return value


class CreateTaskFromCommand:
    def __init__(
        self,
        store: TaskCommandStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._clock = clock

    async def execute(
        self, message_id: str | None, message_type: str | None, body: bytes
    ) -> bool:
        """True when this message was new; a storage failure propagates."""
        if message_id is None or message_type != COMMAND_TYPE:
            raise InvalidMessage
        task = self._parse(body)
        return await self._store.create(message_id, task, self._created(task))

    async def handle(
        self, message_id: str | None, message_type: str | None, body: bytes
    ) -> None:
        """Queue-consumer entry point."""
        await self.execute(message_id, message_type, body)

    def _parse(self, body: bytes) -> NewTask:
        try:
            envelope = json.loads(body)
            if envelope["envelope_version"] != ENVELOPE_VERSION:
                raise ValueError
            payload: dict[str, Any] = envelope["payload"]
            source = source_key(
                _integer(payload["bot_id"]),
                _integer(payload["chat_id"]),
                _integer(payload["message_id"]),
            )
            return NewTask(
                id=task_id_for(source),
                source_key=source,
                correlation_id=UUID(payload["job_id"]),
                text=_text(payload["text"], MAX_TEXT),
                author_id=UUID(payload["author_id"]),
                author_telegram_id=_integer(payload["author_telegram_id"]),
                author_name=_text(payload["author_name"], MAX_NAME),
                created_at=self._clock(),
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            raise InvalidMessage from None

    def _created(self, task: NewTask) -> TaskCreatedMessage:
        message_id = created_message_id_for(task.id)
        return TaskCreatedMessage(
            id=message_id,
            correlation_id=task.correlation_id,
            type=RESULT_TYPE,
            routing_key=TASK_CREATED_ROUTING_KEY,
            payload={
                "envelope_version": ENVELOPE_VERSION,
                "message_id": str(message_id),
                "type": RESULT_TYPE,
                "correlation_id": str(task.correlation_id),
                "occurred_at": task.created_at.isoformat(),
                "payload": {
                    "job_id": str(task.correlation_id),
                    "task_id": str(task.id),
                    "source_key": task.source_key,
                },
            },
        )
