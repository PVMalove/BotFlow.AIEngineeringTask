"""Deliver a queued notification, unless access has moved on."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from application.ports import IdentityGateway, Messenger, NotificationQueue
from domain.entities.notification_intent import NotificationIntent
from domain.unit_of_work import AutomationUnitOfWork
from domain.value_objects.intent_id import IntentId
from domain.value_objects.intent_status import IntentStatus, Verdict

MAX_ATTEMPTS = 3
TEXTS = {
    "access.approved": "Заявка одобрена. Для входа в веб-панель отправьте /login.",
    "access.rejected": "Заявка отклонена. Подать её снова можно командой /join.",
    "access.unblocked": "Доступ восстановлен. Для входа в веб-панель отправьте /login.",
}


class Delivery(StrEnum):
    DONE = "done"  # settled in PostgreSQL; the stream entry may be acknowledged
    RETRY = "retry"  # leave the entry pending; it is claimed again when idle


@dataclass(frozen=True)
class DeliverNotificationCommand:
    intent_id: UUID


class DeliverNotificationCommandHandler:
    def __init__(
        self,
        uow_factory: Callable[[], AutomationUnitOfWork],
        identity: IdentityGateway,
        messenger: Messenger,
    ) -> None:
        self._uow_factory = uow_factory
        self._identity = identity
        self._messenger = messenger

    async def execute(self, command: DeliverNotificationCommand) -> Delivery:
        intent_id = IntentId(command.intent_id)
        async with self._uow_factory() as uow:
            intent = await uow.intents.get(intent_id)
        if intent is None or not intent.is_open:
            return Delivery.DONE  # unknown, or a duplicate stream entry
        try:
            member = await self._identity.find_member(intent.chat_id)
        except Exception:
            return await self._failed(intent_id)
        verdict = intent.delivery_verdict(
            member.status if member else None, member.access_version if member else 0
        )
        if verdict.is_err:
            await self._settle(intent_id, IntentStatus.FAILED)
            return Delivery.DONE
        if verdict.value is not Verdict.SEND:
            await self._settle(
                intent_id,
                IntentStatus.SUPPRESSED_STALE
                if verdict.value is Verdict.STALE
                else IntentStatus.SUPPRESSED_BLOCKED,
            )
            return Delivery.DONE
        return await self._send(intent)

    async def _send(self, intent: NotificationIntent) -> Delivery:
        text = intent.body or TEXTS.get(intent.kind)
        if text is None:  # a kind with a rule but no text to send
            await self._settle(intent.id, IntentStatus.FAILED)
            return Delivery.DONE
        try:
            await self._messenger.send(intent.chat_id, text)
        except Exception:
            return await self._failed(intent.id)
        await self._settle(intent.id, IntentStatus.SENT)
        return Delivery.DONE

    async def _settle(self, intent_id: IntentId, status: IntentStatus) -> None:
        async with self._uow_factory() as uow:
            intent = await uow.intents.get(intent_id)
            if intent is not None and intent.settle(status):
                await uow.intents.update(intent)
                await uow.commit()

    async def _failed(self, intent_id: IntentId) -> Delivery:
        async with self._uow_factory() as uow:
            intent = await uow.intents.get(intent_id)
            if intent is None:
                return Delivery.DONE
            status = intent.record_failure(MAX_ATTEMPTS)
            await uow.intents.update(intent)
            await uow.commit()
        return Delivery.DONE if status is IntentStatus.FAILED else Delivery.RETRY


class RunNotifier:
    def __init__(
        self,
        queue: NotificationQueue,
        deliver: DeliverNotificationCommandHandler,
        batch_size: int = 10,
    ) -> None:
        self._queue = queue
        self._deliver = deliver
        self._batch_size = batch_size

    async def execute(self, block_ms: int = 1000) -> int:
        """Handle one batch of stuck then new entries; return how many were seen."""
        entries = await self._queue.claim_idle(self._batch_size)
        entries += await self._queue.read(self._batch_size, block_ms)
        for entry in entries:
            if (
                entry.intent_id is None
                or await self._deliver.execute(
                    DeliverNotificationCommand(entry.intent_id)
                )
                is Delivery.DONE
            ):
                await self._queue.ack(entry.entry_id)
        return len(entries)
