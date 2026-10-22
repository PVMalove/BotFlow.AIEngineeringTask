"""The CreateTask outbox message, built the same way for text and for voice."""

from datetime import datetime
from typing import Any
from uuid import UUID

from kernel_platform.messaging import TASK_CREATE_ROUTING_KEY

from application.ports import OutboxMessage
from domain.jobs import outbox_id_for

COMMAND_TYPE = "CreateTask"
ENVELOPE_VERSION = 1


def create_task_message(
    *,
    job_id: UUID,
    bot_id: int,
    chat_id: int,
    message_id: int,
    author_id: UUID,
    author_telegram_id: int,
    author_name: str,
    text: str,
    received_at: datetime,
) -> OutboxMessage:
    """The deterministic outbox id keeps a repeated hand-off to one message."""
    entry_id = outbox_id_for(job_id)
    payload: dict[str, Any] = {
        "job_id": str(job_id),
        "bot_id": bot_id,
        "chat_id": chat_id,
        "message_id": message_id,
        "author_id": str(author_id),
        "author_telegram_id": author_telegram_id,
        "author_name": author_name,
        "text": text,
    }
    return OutboxMessage(
        id=entry_id,
        correlation_id=job_id,
        type=COMMAND_TYPE,
        routing_key=TASK_CREATE_ROUTING_KEY,
        payload={
            "envelope_version": ENVELOPE_VERSION,
            "message_id": str(entry_id),
            "type": COMMAND_TYPE,
            "correlation_id": str(job_id),
            "occurred_at": received_at.isoformat(),
            "payload": payload,
        },
    )
