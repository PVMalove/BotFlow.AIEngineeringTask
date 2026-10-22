"""Generic RabbitMQ consumer: acknowledge only after the handler's commit."""

import asyncio
import random
from collections.abc import Awaitable, Callable

from aio_pika.abc import (
    AbstractChannel,
    AbstractConnection,
    AbstractIncomingMessage,
    AbstractQueue,
)

from botflow_platform.retry import Rng, jittered

Handler = Callable[[str | None, str | None, bytes], Awaitable[None]]
QueueDeclarer = Callable[[AbstractChannel], Awaitable[AbstractQueue]]


class InvalidMessage(Exception):
    """The message can never be handled; redelivery would not help."""


class QueueConsumer:
    def __init__(
        self,
        connection: AbstractConnection,
        declare: QueueDeclarer,
        handler: Handler,
        *,
        prefetch: int = 10,
        pause_seconds: float = 5.0,
        rng: Rng = random.random,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._connection = connection
        self._declare = declare
        self._handler = handler
        self._prefetch = prefetch
        self._pause_seconds = pause_seconds
        self._rng = rng
        self._sleep = sleep

    async def run(self) -> None:
        """Consume until cancelled."""
        channel = await self._connection.channel()
        await channel.set_qos(prefetch_count=self._prefetch)
        queue = await self._declare(channel)
        async with queue.iterator() as messages:
            async for message in messages:
                await self.handle(message)

    async def handle(self, message: AbstractIncomingMessage) -> None:
        try:
            await self._handler(message.message_id, message.type, message.body)
        except InvalidMessage:
            await message.reject(requeue=False)  # no dead-letter exchange (ADR-0021)
        except Exception:
            # Storage is unavailable: pause first so the requeue is not a busy loop.
            await self._sleep(jittered(self._pause_seconds, rng=self._rng))
            await message.nack(requeue=True)
        else:
            await message.ack()  # after the commit; a redelivery is deduplicated
