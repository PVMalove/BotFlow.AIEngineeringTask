"""Hand stored intents to the notification stream."""

from collections.abc import Callable

from application.ports import NotificationQueue
from domain.unit_of_work import AutomationUnitOfWork


class RelayIntents:
    def __init__(
        self,
        uow_factory: Callable[[], AutomationUnitOfWork],
        queue: NotificationQueue,
        batch_size: int = 50,
    ) -> None:
        self._uow_factory = uow_factory
        self._queue = queue
        self._batch_size = batch_size

    async def execute(self) -> int:
        """Add pending intents to the stream; return how many were handed over."""
        async with self._uow_factory() as uow:
            pending = await uow.intents.pending(self._batch_size)
        handed_over = 0
        for intent_id in pending:
            # A crash between the two steps re-adds the intent; delivery is idempotent.
            await self._queue.add(intent_id.value)
            async with self._uow_factory() as uow:
                intent = await uow.intents.get(intent_id)
                if intent is not None and intent.mark_enqueued():
                    await uow.intents.update(intent)
                    await uow.commit()
            handed_over += 1
        return handed_over
