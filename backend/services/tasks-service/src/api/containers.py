"""Service composition root; each process owns its infrastructure lifecycle."""

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
from aio_pika.abc import AbstractChannel, AbstractQueue
from dependency_injector import containers, providers
from kernel_platform.consumer import QueueConsumer
from kernel_platform.messaging import ConfirmedPublisher, declare_task_topology
from kernel_platform.outbox import RelayOutbox, SqlOutboxTx, sql_transactions
from kernel_platform.resources import Resources, open_resources
from kernel_platform.settings import Settings

from application.commands.change_task_status import ChangeTaskStatusCommandHandler
from application.commands.create_task import CreateTaskCommandHandler
from application.queries.get_task import GetTaskQueryHandler
from application.queries.list_tasks import ListTasksQueryHandler
from infrastructure.board_signal import RedisBoardSignal
from infrastructure.db.tables import outbox
from infrastructure.db.task_reader import SqlTaskReader
from infrastructure.db.unit_of_work import SqlTasksUnitOfWork
from infrastructure.identity_gateway import HttpIdentityGateway
from infrastructure.messaging.create_task_consumer import CreateTaskConsumer


@dataclass(frozen=True, slots=True)
class TaskWorker:
    """RabbitMQ commands -> PostgreSQL (task + result) -> RabbitMQ results."""

    consumer: QueueConsumer
    relay: RelayOutbox[SqlOutboxTx]


def build_worker(resources: Resources) -> TaskWorker:
    def uow() -> SqlTasksUnitOfWork:
        return SqlTasksUnitOfWork(resources.sessions)

    create = CreateTaskConsumer(CreateTaskCommandHandler(uow))

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


@dataclass(frozen=True, slots=True)
class Services:
    """Use cases the HTTP layer needs, wired to the process resources."""

    identity: HttpIdentityGateway
    list_tasks: ListTasksQueryHandler
    get_task: GetTaskQueryHandler
    change_task_status: ChangeTaskStatusCommandHandler


async def open_services(resources: Resources) -> AsyncIterator[Services]:
    async with httpx.AsyncClient(
        base_url=os.environ.get("BOTFLOW_IDENTITY_URL", "http://identity-api:8000"),
        timeout=5,
        follow_redirects=False,
    ) as client:
        identity = HttpIdentityGateway(client)
        reader = SqlTaskReader(resources.sessions)
        assert resources.redis is not None  # Tasks always opens Redis
        board_signal = RedisBoardSignal(resources.redis)

        def uow() -> SqlTasksUnitOfWork:
            return SqlTasksUnitOfWork(
                resources.sessions, event_handlers=[board_signal.handle]
            )

        yield Services(
            identity=identity,
            list_tasks=ListTasksQueryHandler(reader),
            get_task=GetTaskQueryHandler(reader),
            change_task_status=ChangeTaskStatusCommandHandler(uow),
        )


class Container(containers.DeclarativeContainer):
    settings = providers.Singleton(Settings, service="tasks")
    resources = providers.Resource(open_resources, settings=settings)
    services = providers.Resource(open_services, resources=resources)
