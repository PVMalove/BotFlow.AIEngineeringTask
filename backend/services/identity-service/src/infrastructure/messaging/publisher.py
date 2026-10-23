"""RabbitMQ adapter of the event publisher port."""

import json

from kernel_platform.messaging import ConfirmedPublisher

from domain.outbox import OutboxEntry


class RabbitEventPublisher:
    def __init__(self, publisher: ConfirmedPublisher) -> None:
        self._publisher = publisher

    async def publish(self, entry: OutboxEntry) -> None:
        await self._publisher.publish(
            entry.type,  # the event type is also its routing key
            json.dumps(entry.payload, separators=(",", ":")).encode(),
            message_id=str(entry.id),
            type=entry.type,
        )
