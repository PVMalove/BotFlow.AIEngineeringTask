"""The task of the shared board and the identity of its source."""

from enum import StrEnum
from uuid import UUID, uuid5

# Fixed namespaces make ids a pure function of their source, so a redelivered or
# replayed command always addresses the same task and the same result message.
_TASK_NAMESPACE = UUID("6f0c0b5e-3a54-4c5b-9d0e-1d2a7b7c9e01")
_RESULT_NAMESPACE = UUID("6f0c0b5e-3a54-4c5b-9d0e-1d2a7b7c9e02")


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


def source_key(bot_id: int, chat_id: int, message_id: int) -> str:
    """The Telegram message a task came from; unique per task."""
    return f"{bot_id}:{chat_id}:{message_id}"


def task_id_for(source: str) -> UUID:
    return uuid5(_TASK_NAMESPACE, source)


def created_message_id_for(task_id: UUID) -> UUID:
    return uuid5(_RESULT_NAMESPACE, str(task_id))
