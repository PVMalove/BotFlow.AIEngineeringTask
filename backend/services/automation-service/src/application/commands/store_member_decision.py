"""Consumer side of Identity events: inbox and notification intent in one step."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from kernel_domain.result import Result

from domain.entities.notification_intent import NotificationIntent
from domain.errors import AutomationErrors
from domain.unit_of_work import AutomationUnitOfWork
from domain.value_objects.intent_id import IntentId

_DECISIONS = {"approved", "rejected", "unblocked"}


@dataclass(frozen=True)
class StoreMemberDecisionCommand:
    event_id: UUID  # the broker message id: inbox and intent share this key
    member_id: UUID
    telegram_id: int
    decision: str
    access_version: int


class StoreMemberDecisionCommandHandler:
    def __init__(self, uow_factory: Callable[[], AutomationUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def execute(self, command: StoreMemberDecisionCommand) -> Result[bool]:
        """True when a new intent was stored; False for a redelivered message."""
        if (
            command.decision not in _DECISIONS
            or command.telegram_id <= 0
            or command.access_version < 1
        ):
            return Result[bool].fail(AutomationErrors.invalid_decision())
        async with self._uow_factory() as uow:
            if not await uow.inbox.claim(str(command.event_id)):
                return Result[bool].ok(False)  # the first copy already made its intent
            await uow.intents.add(
                NotificationIntent.access_decision(
                    IntentId(command.event_id),
                    chat_id=command.telegram_id,
                    member_id=command.member_id,
                    decision=command.decision,
                    access_version=command.access_version,
                )
            )
            await uow.commit()  # the broker message is acknowledged only after this
        return Result[bool].ok(True)
