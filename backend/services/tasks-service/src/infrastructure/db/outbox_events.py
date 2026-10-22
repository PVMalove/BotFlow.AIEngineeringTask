"""Domain events of the Task aggregate that leave the service through the outbox."""

from typing import Any
from uuid import UUID, uuid5

from kernel_domain.domain_event import DomainEvent
from kernel_platform.messaging import TASK_CREATED_ROUTING_KEY
from kernel_platform.outbox import enqueue
from sqlalchemy.ext.asyncio import AsyncSession

from domain.events.task_domain_event import TaskCreated
from infrastructure.db.tables import outbox

ENVELOPE_VERSION = 1
RESULT_TYPE = "TaskCreated"
# The result id is a function of the task id: a replayed event is the same row.
_RESULT_NAMESPACE = UUID("6f0c0b5e-3a54-4c5b-9d0e-1d2a7b7c9e02")


async def stage_task_event(session: AsyncSession, event: DomainEvent) -> None:
    """Store the message of an event in the transaction that changed the task."""
    if not isinstance(event, TaskCreated):
        return  # the other task events stay inside the service
    message_id = uuid5(_RESULT_NAMESPACE, str(event.task_id.value))
    envelope: dict[str, Any] = {
        "envelope_version": ENVELOPE_VERSION,
        "message_id": str(message_id),
        "type": RESULT_TYPE,
        "correlation_id": str(event.correlation_id),
        "occurred_at": event.occurred_on_utc.isoformat(),
        "payload": {
            "job_id": str(event.correlation_id),
            "task_id": str(event.task_id.value),
            "source_key": event.source_key,
        },
    }
    await enqueue(
        session,
        outbox,
        entry_id=message_id,
        type=RESULT_TYPE,
        routing_key=TASK_CREATED_ROUTING_KEY,
        payload=envelope,
        correlation_id=event.correlation_id,
        now=event.occurred_on_utc,
    )
