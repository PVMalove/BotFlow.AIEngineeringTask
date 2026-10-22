"""Consumer side of Identity events: inbox and notification intent in one step."""

from dataclasses import dataclass
from uuid import UUID

from kernel_domain.result import Result

from application.ports import IntentStore
from domain.errors import AutomationErrors
from domain.notifications import NotificationIntent

_DECISIONS = {"approved", "rejected", "unblocked"}


@dataclass(frozen=True)
class StoreMemberDecisionCommand:
    event_id: UUID  # the broker message id: inbox and intent share this key
    member_id: UUID
    telegram_id: int
    decision: str
    access_version: int


class StoreMemberDecisionCommandHandler:
    def __init__(self, store: IntentStore) -> None:
        self._store = store

    async def execute(self, command: StoreMemberDecisionCommand) -> Result[bool]:
        """True when a new intent was stored; False for a redelivered message."""
        if (
            command.decision not in _DECISIONS
            or command.telegram_id <= 0
            or command.access_version < 1
        ):
            return Result[bool].fail(AutomationErrors.invalid_decision())
        intent = NotificationIntent(
            id=command.event_id,
            chat_id=command.telegram_id,
            member_id=command.member_id,
            kind=f"access.{command.decision}",
            access_version=command.access_version,
        )
        return Result[bool].ok(await self._store.store_event(str(intent.id), intent))
