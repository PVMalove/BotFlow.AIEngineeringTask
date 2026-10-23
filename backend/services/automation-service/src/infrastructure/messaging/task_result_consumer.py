"""Queue adapter: a TaskCreated envelope becomes a RecordTaskResultCommand."""

import json
from uuid import UUID

from kernel_platform.consumer import InvalidMessage
from kernel_platform.envelope import ENVELOPE_VERSION

from application.commands.record_task_result import (
    RecordTaskResultCommand,
    RecordTaskResultCommandHandler,
)

RESULT_TYPE = "TaskCreated"


class TaskResultConsumer:
    def __init__(self, handler: RecordTaskResultCommandHandler) -> None:
        self._handler = handler

    async def handle(
        self, message_id: str | None, message_type: str | None, body: bytes
    ) -> None:
        """Queue-consumer entry point; a storage failure propagates for requeue."""
        if message_id is None or message_type != RESULT_TYPE:
            raise InvalidMessage
        result = await self._handler.execute(_parse(message_id, body))
        if result.is_err:
            raise InvalidMessage  # no such job: the message can never work


def _parse(message_id: str, body: bytes) -> RecordTaskResultCommand:
    try:
        envelope = json.loads(body)
        if envelope["envelope_version"] != ENVELOPE_VERSION:
            raise ValueError
        payload = envelope["payload"]
        return RecordTaskResultCommand(
            message_id, UUID(payload["job_id"]), UUID(payload["task_id"])
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        raise InvalidMessage from None
