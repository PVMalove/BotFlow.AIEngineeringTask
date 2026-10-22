"""Relay of stored events to the broker."""

from datetime import timedelta

from application.ports import Clock, EventPublisher, UnitOfWorkFactory

# Delay before the next try after the 1st and 2nd failure; a 3rd failure stops.
RETRY_DELAYS = (timedelta(seconds=5), timedelta(seconds=30))


class RelayOutbox:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        publisher: EventPublisher,
        clock: Clock,
        batch_size: int = 20,
    ) -> None:
        self._uow_factory = uow_factory
        self._publisher = publisher
        self._clock = clock
        self._batch_size = batch_size

    async def execute(self) -> int:
        """Publish the due entries; return how many were confirmed."""
        now = self._clock()
        published = 0
        async with self._uow_factory() as uow:
            for entry in await uow.outbox.claim_due(now, self._batch_size):
                try:
                    await self._publisher.publish(entry)
                except Exception:
                    failed = entry.attempts + 1
                    delay = (
                        RETRY_DELAYS[failed - 1]
                        if failed <= len(RETRY_DELAYS)
                        else None
                    )
                    await uow.outbox.reschedule(
                        entry.id, failed, now + delay if delay else None
                    )
                else:
                    await uow.outbox.mark_published(entry.id, now)
                    published += 1
            await uow.commit()
        return published
