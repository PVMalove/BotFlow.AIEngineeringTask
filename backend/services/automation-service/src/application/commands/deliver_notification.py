"""Deliver a queued notification, unless access has moved on."""

from enum import StrEnum
from uuid import UUID

from application.ports import (
    IdentityGateway,
    IntentStore,
    Messenger,
    NotificationQueue,
)
from domain.notifications import (
    IntentStatus,
    UnknownNotificationKind,
    Verdict,
    delivery_verdict,
)

MAX_ATTEMPTS = 3
TEXTS = {
    "access.approved": "Заявка одобрена. Для входа в веб-панель отправьте /login.",
    "access.rejected": "Заявка отклонена. Подать её снова можно командой /join.",
    "access.unblocked": "Доступ восстановлен. Для входа в веб-панель отправьте /login.",
}


class Delivery(StrEnum):
    DONE = "done"  # settled in PostgreSQL; the stream entry may be acknowledged
    RETRY = "retry"  # leave the entry pending; it is claimed again when idle


class DeliverNotification:
    def __init__(
        self, store: IntentStore, identity: IdentityGateway, messenger: Messenger
    ) -> None:
        self._store = store
        self._identity = identity
        self._messenger = messenger

    async def execute(self, intent_id: UUID) -> Delivery:
        intent = await self._store.get(intent_id)
        if intent is None or intent.status not in {
            IntentStatus.PENDING,
            IntentStatus.ENQUEUED,
        }:
            return Delivery.DONE  # unknown, or a duplicate stream entry
        try:
            member = await self._identity.find_member(intent.chat_id)
            verdict = delivery_verdict(
                intent.kind,
                intent.access_version,
                member.status if member else None,
                member.access_version if member else 0,
            )
        except UnknownNotificationKind:
            await self._store.settle(intent_id, IntentStatus.FAILED)
            return Delivery.DONE
        except Exception:
            return await self._failed(intent_id)
        if verdict is not Verdict.SEND:
            await self._store.settle(
                intent_id,
                IntentStatus.SUPPRESSED_STALE
                if verdict is Verdict.STALE
                else IntentStatus.SUPPRESSED_BLOCKED,
            )
            return Delivery.DONE
        text = TEXTS.get(intent.kind)
        if text is None:  # a result kind: its slice supplies the text
            await self._store.settle(intent_id, IntentStatus.FAILED)
            return Delivery.DONE
        try:
            await self._messenger.send(intent.chat_id, text)
        except Exception:
            return await self._failed(intent_id)
        await self._store.settle(intent_id, IntentStatus.SENT)
        return Delivery.DONE

    async def _failed(self, intent_id: UUID) -> Delivery:
        status = await self._store.record_failure(intent_id, MAX_ATTEMPTS)
        return Delivery.DONE if status is IntentStatus.FAILED else Delivery.RETRY


class RunNotifier:
    def __init__(
        self,
        queue: NotificationQueue,
        deliver: DeliverNotification,
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
                or await self._deliver.execute(entry.intent_id) is Delivery.DONE
            ):
                await self._queue.ack(entry.entry_id)
        return len(entries)
