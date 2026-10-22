"""Queue adapter: an Identity access decision becomes a StoreMemberDecisionCommand."""

import json
from uuid import UUID

from aio_pika.abc import AbstractChannel, AbstractQueue
from kernel_platform.consumer import InvalidMessage
from kernel_platform.messaging import declare_identity_events

from application.commands.store_member_decision import (
    StoreMemberDecisionCommand,
    StoreMemberDecisionCommandHandler,
)

ACCESS_DECIDED = "member.access-decided"


class IdentityEventConsumer:
    def __init__(self, handler: StoreMemberDecisionCommandHandler) -> None:
        self._handler = handler

    async def handle(
        self, message_id: str | None, message_type: str | None, body: bytes
    ) -> None:
        """Queue-consumer entry point; a storage failure propagates for requeue."""
        result = await self._handler.execute(_parse(message_id, message_type, body))
        if result.is_err:
            raise InvalidMessage  # redelivery cannot fix it


async def declare_queue(channel: AbstractChannel) -> AbstractQueue:
    _, queue = await declare_identity_events(channel)
    return queue


def _parse(
    message_id: str | None, message_type: str | None, body: bytes
) -> StoreMemberDecisionCommand:
    try:
        if message_type != ACCESS_DECIDED or message_id is None:
            raise ValueError
        payload = json.loads(body)
        return StoreMemberDecisionCommand(
            event_id=UUID(message_id),
            member_id=UUID(payload["member_id"]),
            telegram_id=_integer(payload["telegram_id"]),
            decision=_string(payload["decision"]),
            access_version=_integer(payload["access_version"]),
        )
    except (ValueError, KeyError, TypeError):
        raise InvalidMessage from None


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError
    return value


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError
    return value
