"""Hand stored intents to the notification stream."""

from application.ports import IntentStore, NotificationQueue


class RelayIntents:
    def __init__(
        self, store: IntentStore, queue: NotificationQueue, batch_size: int = 50
    ) -> None:
        self._store = store
        self._queue = queue
        self._batch_size = batch_size

    async def execute(self) -> int:
        """Add pending intents to the stream; return how many were handed over."""
        handed_over = 0
        for intent_id in await self._store.pending(self._batch_size):
            # A crash between the two calls re-adds the intent; delivery is idempotent.
            await self._queue.add(intent_id)
            await self._store.mark_enqueued(intent_id)
            handed_over += 1
        return handed_over
