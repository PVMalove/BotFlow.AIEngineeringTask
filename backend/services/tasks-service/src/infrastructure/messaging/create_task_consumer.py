"""Queue adapter: a CreateTask envelope becomes a CreateTaskCommand."""

import json
from typing import Any
from uuid import UUID

from kernel_platform.consumer import InvalidMessage

from application.commands.create_task import (
    CreateTaskCommand,
    CreateTaskCommandHandler,
)

COMMAND_TYPE = "CreateTask"
ENVELOPE_VERSION = 1


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
            bot_id=_integer(payload["bot_id"]),
            chat_id=_integer(payload["chat_id"]),
            source_message_id=_integer(payload["message_id"]),
            text=_string(payload["text"]),
            author_id=UUID(payload["author_id"]),
            author_telegram_id=_integer(payload["author_telegram_id"]),
            author_name=_string(payload["author_name"]),
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        raise InvalidMessage from None


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError
    return value


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError
    return value
