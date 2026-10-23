"""RabbitMQ topology and confirmed publishing shared by the services.

Both sides declare the same durable exchange, queue and binding, so a message is
routable even when the consumer has never started; a message that still has no
queue is returned (`mandatory`) and surfaces as a publish error, never as a loss.
"""

from collections.abc import Awaitable, Callable
from typing import Any

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
TASKS_EXCHANGE = "botflow.tasks"
TASK_CREATE_ROUTING_KEY = "task.create"
TASK_CREATED_ROUTING_KEY = "task.created"
TASK_COMMANDS_QUEUE = "botflow.tasks.commands"
TASK_RESULTS_QUEUE = "botflow.automation.task-results"
_PUBLISH_TIMEOUT_SECONDS = 10

# Declares a topology on a channel; the first element is the exchange to publish to.
TopologyDeclarer = Callable[
    [AbstractChannel], Awaitable[tuple[AbstractExchange, *tuple[Any, ...]]]
]


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


async def declare_task_topology(
    channel: AbstractChannel,
) -> tuple[AbstractExchange, AbstractQueue, AbstractQueue]:
    """Idempotently declare the task exchange with Tasks' and Automation's queues."""
    exchange = await channel.declare_exchange(
        TASKS_EXCHANGE, ExchangeType.TOPIC, durable=True
    )
    commands = await channel.declare_queue(TASK_COMMANDS_QUEUE, durable=True)
    await commands.bind(exchange, routing_key=TASK_CREATE_ROUTING_KEY)
    results = await channel.declare_queue(TASK_RESULTS_QUEUE, durable=True)
    await results.bind(exchange, routing_key=TASK_CREATED_ROUTING_KEY)
    return exchange, commands, results


class ConfirmedPublisher:
    """Persistent, mandatory publishes that return only after the broker confirms."""

    def __init__(
        self,
        connection: AbstractConnection,
        declare: TopologyDeclarer = declare_identity_events,
    ) -> None:
        self._connection = connection
        self._declare = declare
        self._exchange: AbstractExchange | None = None

    async def publish(
        self, routing_key: str, body: bytes, *, message_id: str, type: str
    ) -> None:
        try:
            if self._exchange is None:
                # Without on_return_raises aio-pika swallows a returned message.
                channel = await self._connection.channel(
                    publisher_confirms=True, on_return_raises=True
                )
                self._exchange = (await self._declare(channel))[0]
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
