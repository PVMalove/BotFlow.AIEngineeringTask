"""Queue adapter: a CreateTask envelope becomes a CreateTaskCommand."""

import json
from typing import Any
from uuid import UUID

from kernel_platform.consumer import InvalidMessage
from kernel_platform.envelope import ENVELOPE_VERSION, as_int, as_str

from application.commands.create_task import (
    CreateTaskCommand,
    CreateTaskCommandHandler,
)

COMMAND_TYPE = "CreateTask"


class CreateTaskConsumer:
    def __init__(self, handler: CreateTaskCommandHandler) -> None:
        self._handler = handler

    async def handle(
        self, message_id: str | None, message_type: str | None, body: bytes
    ) -> None:
        """Queue-consumer entry point; a storage failure propagates for requeue."""
        if message_id is None or message_type != COMMAND_TYPE:
            raise InvalidMessage
        result = await self._handler.execute(_parse(message_id, body))
        if result.is_err:
            raise InvalidMessage  # a message that can never work


def _parse(message_id: str, body: bytes) -> CreateTaskCommand:
    try:
        envelope = json.loads(body)
        if envelope["envelope_version"] != ENVELOPE_VERSION:
            raise ValueError
        payload: dict[str, Any] = envelope["payload"]
        return CreateTaskCommand(
            message_id=message_id,
            correlation_id=UUID(payload["job_id"]),
            bot_id=as_int(payload["bot_id"]),
            chat_id=as_int(payload["chat_id"]),
            source_message_id=as_int(payload["message_id"]),
            text=as_str(payload["text"]),
            author_id=UUID(payload["author_id"]),
            author_telegram_id=as_int(payload["author_telegram_id"]),
            author_name=as_str(payload["author_name"]),
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        raise InvalidMessage from None
