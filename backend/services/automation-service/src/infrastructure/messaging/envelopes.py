"""The broker messages a job sends to other processes, and their deterministic ids."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid5

from kernel_platform.messaging import TASK_CREATE_ROUTING_KEY

ENVELOPE_VERSION = 1
CREATE_TASK = "CreateTask"
TRANSCRIBE_VOICE = "TranscribeVoice"
VOICE_ROUTING_KEY = "voice.transcribe"

# Fixed namespaces make outbox ids a function of the job, so a redelivered or
# replayed update addresses the same outbox row. The CreateTask row of a voice job
# comes later than its TranscribeVoice row, hence two namespaces.
_OUTBOX_NAMESPACE = UUID("2c9d6f4e-5a0b-4b57-8e57-0e6f1b3a7d02")
_TRANSCRIBE_NAMESPACE = UUID("2c9d6f4e-5a0b-4b57-8e57-0e6f1b3a7d04")


def outbox_id_for(job_id: UUID) -> UUID:
    return uuid5(_OUTBOX_NAMESPACE, str(job_id))


def transcribe_outbox_id_for(job_id: UUID) -> UUID:
    return uuid5(_TRANSCRIBE_NAMESPACE, str(job_id))


def create_task_envelope(
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
) -> tuple[UUID, str, dict[str, Any]]:
    """Outbox id, routing key and envelope of the CreateTask command."""
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
    return (
        entry_id,
        TASK_CREATE_ROUTING_KEY,
        _envelope(entry_id, CREATE_TASK, job_id, received_at, payload),
    )


def transcribe_envelope(
    *, job_id: UUID, received_at: datetime
) -> tuple[UUID, str, dict[str, Any]]:
    """The audio is addressed through the job's voice record, never by a URL."""
    entry_id = transcribe_outbox_id_for(job_id)
    return (
        entry_id,
        VOICE_ROUTING_KEY,
        _envelope(
            entry_id, TRANSCRIBE_VOICE, job_id, received_at, {"job_id": str(job_id)}
        ),
    )


def _envelope(
    entry_id: UUID,
    message_type: str,
    job_id: UUID,
    occurred_at: datetime,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "envelope_version": ENVELOPE_VERSION,
        "message_id": str(entry_id),
        "type": message_type,
        "correlation_id": str(job_id),
        "occurred_at": occurred_at.isoformat(),
        "payload": payload,
    }
