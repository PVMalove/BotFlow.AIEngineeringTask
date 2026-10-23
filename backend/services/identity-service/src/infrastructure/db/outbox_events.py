"""Domain events of the Member aggregate that leave the service through the outbox."""

from kernel_domain.domain_event import DomainEvent
from sqlalchemy.ext.asyncio import AsyncSession

from domain.events.member_domain_event import (
    MemberApproved,
    MemberEvent,
    MemberRejected,
    MemberUnblocked,
)
from domain.outbox import ACCESS_DECIDED, OutboxEntry
from infrastructure.db.outbox import SqlOutbox

# A block deliberately has no notification: the blocked author hears nothing more.
_NOTIFIED_AS: dict[type[DomainEvent], str] = {
    MemberApproved: "approved",
    MemberRejected: "rejected",
    MemberUnblocked: "unblocked",
}


async def stage_identity_event(session: AsyncSession, event: DomainEvent) -> None:
    """Store the notification intent in the transaction of the decision."""
    entry = outbox_entry_for(event)
    if entry is not None:
        await SqlOutbox(session).add(entry)


def outbox_entry_for(event: DomainEvent) -> OutboxEntry | None:
    """The message an event sends to other services; None for an internal one."""
    decision = _NOTIFIED_AS.get(type(event))
    if decision is None or not isinstance(event, MemberEvent):
        return None
    return OutboxEntry(
        id=event.id,
        type=ACCESS_DECIDED,
        payload={
            "member_id": str(event.member_id.value),
            "telegram_id": event.telegram_id,
            "decision": decision,
            "access_version": event.access_version,
            "decided_at": event.occurred_on_utc.isoformat(),
        },
        created_at=event.occurred_on_utc,
        next_attempt_at=event.occurred_on_utc,
    )
