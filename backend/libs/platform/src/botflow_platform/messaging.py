"""RabbitMQ topology and confirmed publishing shared by Identity and Automation.

Both sides declare the same durable exchange, queue and binding, so an event is
routable even when the consumer has never started; a message that still has no
queue is returned (`mandatory`) and surfaces as a publish error, never as a loss.
"""

from aio_pika import DeliveryMode, ExchangeType, Message
from aio_pika.abc import (
    AbstractChannel,
    AbstractConnection,
    AbstractExchange,
    AbstractQueue,
)

IDENTITY_EVENTS_EXCHANGE = "botflow.identity"
ACCESS_DECIDED_ROUTING_KEY = "member.access-decided"
AUTOMATION_IDENTITY_QUEUE = "botflow.automation.identity-events"
_PUBLISH_TIMEOUT_SECONDS = 10


async def declare_identity_events(
    channel: AbstractChannel,
) -> tuple[AbstractExchange, AbstractQueue]:
    """Idempotently declare the Identity event exchange and Automation's queue."""
    exchange = await channel.declare_exchange(
        IDENTITY_EVENTS_EXCHANGE, ExchangeType.TOPIC, durable=True
    )
    queue = await channel.declare_queue(AUTOMATION_IDENTITY_QUEUE, durable=True)
    await queue.bind(exchange, routing_key="member.*")
    return exchange, queue


class ConfirmedPublisher:
    """Persistent, mandatory publishes that return only after the broker confirms."""

    def __init__(self, connection: AbstractConnection) -> None:
        self._connection = connection
        self._exchange: AbstractExchange | None = None

    async def publish(
        self, routing_key: str, body: bytes, *, message_id: str, type: str
    ) -> None:
        try:
            if self._exchange is None:
                channel = await self._connection.channel(publisher_confirms=True)
                self._exchange, _ = await declare_identity_events(channel)
            await self._exchange.publish(
                Message(
                    body,
                    delivery_mode=DeliveryMode.PERSISTENT,
                    message_id=message_id,
                    type=type,
                    content_type="application/json",
                ),
                routing_key,
                mandatory=True,
                timeout=_PUBLISH_TIMEOUT_SECONDS,
            )
        except BaseException:
            self._exchange = None  # a failed channel is reopened next time
            raise
