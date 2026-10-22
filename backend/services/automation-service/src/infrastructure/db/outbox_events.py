"""Domain events that leave the aggregate: outbox rows and notification intents."""

from datetime import datetime
from typing import Any
from uuid import UUID

from kernel_domain.domain_event import DomainEvent
from kernel_platform.outbox import enqueue
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from domain.entities.notification_intent import NotificationIntent
from domain.events.job_domain_event import (
    JobCompleted,
    JobRestarted,
    TextJobAccepted,
    VoiceJobAccepted,
    VoiceJobFailed,
    VoiceTranscribed,
)
from domain.notices import task_created_notice
from domain.value_objects.intent_id import IntentId
from infrastructure.db.intent_repository import SqlIntentRepository
from infrastructure.db.tables import notification_intents, outbox
from infrastructure.messaging.envelopes import (
    create_task_envelope,
    transcribe_envelope,
    transcribe_outbox_id_for,
)

TASK_RESULT = "task.result"
TRANSCRIPTION_RESULT = "transcription.result"


async def stage_automation_event(session: AsyncSession, event: DomainEvent) -> None:
    """Store what an event sends on, in the transaction that changed the job."""
    now = event.occurred_on_utc
    match event:
        case TextJobAccepted() | VoiceTranscribed():
            entry_id, routing_key, envelope = create_task_envelope(
                job_id=event.job_id.value,
                bot_id=event.bot_id,
                chat_id=event.chat_id,
                message_id=event.message_id,
                author_id=event.author_id,
                author_telegram_id=event.author_telegram_id,
                author_name=event.author_name,
                text=event.text,
                received_at=event.received_at,
            )
            await _enqueue(
                session, event, entry_id, "CreateTask", routing_key, envelope
            )
        case VoiceJobAccepted():
            entry_id, routing_key, envelope = transcribe_envelope(
                job_id=event.job_id.value, received_at=event.received_at
            )
            await _enqueue(
                session, event, entry_id, "TranscribeVoice", routing_key, envelope
            )
        case JobCompleted():
            await _add_intent(
                session,
                NotificationIntent.result(
                    IntentId.for_result(event.job_id.value),
                    chat_id=event.chat_id,
                    member_id=event.author_id,
                    kind=TASK_RESULT,
                    body=task_created_notice(event.text),
                ),
                now,
            )
        case VoiceJobFailed():
            await _add_intent(
                session,
                NotificationIntent.result(
                    IntentId.for_result(event.job_id.value),
                    chat_id=event.chat_id,
                    member_id=event.author_id,
                    kind=TRANSCRIPTION_RESULT,
                    body=event.notice,
                ),
                now,
            )
        case JobRestarted() if event.voice:
            # The failure notice owns the one result-intent id; a later result needs it.
            await session.execute(
                delete(notification_intents).where(
                    notification_intents.c.id
                    == IntentId.for_result(event.job_id.value).value,
                    notification_intents.c.kind == TRANSCRIPTION_RESULT,
                )
            )
            await session.execute(
                update(outbox)
                .where(outbox.c.id == transcribe_outbox_id_for(event.job_id.value))
                .values(
                    published_at=None,
                    attempts=0,
                    next_attempt_at=now,
                    last_error=None,
                )
            )
        case _:
            return


async def _enqueue(
    session: AsyncSession,
    event: DomainEvent,
    entry_id: UUID,
    message_type: str,
    routing_key: str,
    envelope: dict[str, Any],
) -> None:
    await enqueue(
        session,
        outbox,
        entry_id=entry_id,
        type=message_type,
        routing_key=routing_key,
        payload=envelope,
        correlation_id=event.aggregate_id(),
        now=event.occurred_on_utc,
    )


async def _add_intent(
    session: AsyncSession, intent: NotificationIntent, now: datetime
) -> None:
    await SqlIntentRepository(session, lambda _: None, lambda: now).add(intent)
