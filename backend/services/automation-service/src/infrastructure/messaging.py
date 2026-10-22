"""RabbitMQ consumer of Identity events: acknowledge only after the intent is stored."""

import asyncio

from aio_pika.abc import AbstractConnection, AbstractIncomingMessage
from botflow_platform.messaging import declare_identity_events

from application.commands.process_identity_event import (
    InvalidEvent,
    ProcessIdentityEvent,
)


class IdentityEventConsumer:
    def __init__(
        self,
        connection: AbstractConnection,
        process: ProcessIdentityEvent,
        retry_delay: float = 1.0,
    ) -> None:
        self._connection = connection
        self._process = process
        self._retry_delay = retry_delay

    async def run(self) -> None:
        """Consume until cancelled."""
        channel = await self._connection.channel()
        await channel.set_qos(prefetch_count=10)
        _, queue = await declare_identity_events(channel)
        async with queue.iterator() as messages:
            async for message in messages:
                await self.handle(message)

    async def handle(self, message: AbstractIncomingMessage) -> None:
        try:
            await self._process.execute(message.message_id, message.type, message.body)
        except InvalidEvent:
            await message.reject(requeue=False)  # redelivery cannot fix it
        except Exception:
            # Storage is unavailable: keep the message and try again shortly.
            await message.nack(requeue=True)
            await asyncio.sleep(self._retry_delay)
        else:
            await message.ack()  # after the commit; a redelivery is deduplicated
