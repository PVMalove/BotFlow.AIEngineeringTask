"""Service composition root; each process owns its infrastructure lifecycle."""

from dataclasses import dataclass

from aio_pika.abc import AbstractChannel, AbstractQueue
from botflow_platform.consumer import QueueConsumer
from botflow_platform.messaging import ConfirmedPublisher, declare_task_topology
from botflow_platform.outbox import RelayOutbox, SqlOutboxTx, sql_transactions
from botflow_platform.resources import Resources, open_resources
from botflow_platform.settings import Settings
from dependency_injector import containers, providers

from application.commands.create_task import CreateTaskFromCommand
from infrastructure.db.tables import outbox
from infrastructure.db.task_store import SqlTaskCommandStore


@dataclass(frozen=True, slots=True)
class TaskWorker:
    """RabbitMQ commands -> PostgreSQL (task + result) -> RabbitMQ results."""

    consumer: QueueConsumer
    relay: RelayOutbox[SqlOutboxTx]


def build_worker(resources: Resources) -> TaskWorker:
    create = CreateTaskFromCommand(SqlTaskCommandStore(resources.sessions))

    async def commands_queue(channel: AbstractChannel) -> AbstractQueue:
        _, commands, _ = await declare_task_topology(channel)
        return commands

    return TaskWorker(
        consumer=QueueConsumer(resources.rabbitmq, commands_queue, create.handle),
        relay=RelayOutbox(
            sql_transactions(resources.sessions, outbox),
            ConfirmedPublisher(resources.rabbitmq, declare_task_topology),
        ),
    )


class Container(containers.DeclarativeContainer):
    settings = providers.Singleton(Settings, service="tasks")
    resources = providers.Resource(open_resources, settings=settings)
