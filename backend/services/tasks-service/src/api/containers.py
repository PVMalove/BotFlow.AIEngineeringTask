"""Service composition root; each process owns its infrastructure lifecycle."""

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
from aio_pika.abc import AbstractChannel, AbstractQueue
from botflow_platform.consumer import QueueConsumer
from botflow_platform.messaging import ConfirmedPublisher, declare_task_topology
from botflow_platform.outbox import RelayOutbox, SqlOutboxTx, sql_transactions
from botflow_platform.resources import Resources, open_resources
from botflow_platform.settings import Settings
from dependency_injector import containers, providers

from application.commands.create_task import CreateTaskFromCommand
from application.queries.tasks import GetTask, ListTasks
from infrastructure.db.tables import outbox
from infrastructure.db.task_reader import SqlTaskReader
from infrastructure.db.task_store import SqlTaskCommandStore
from infrastructure.identity_gateway import HttpIdentityGateway


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


@dataclass(frozen=True, slots=True)
class Services:
    """Use cases the HTTP layer needs, wired to the process resources."""

    list_tasks: ListTasks
    get_task: GetTask


async def open_services(resources: Resources) -> AsyncIterator[Services]:
    async with httpx.AsyncClient(
        base_url=os.environ.get("BOTFLOW_IDENTITY_URL", "http://identity-api:8000"),
        timeout=5,
        follow_redirects=False,
    ) as client:
        identity = HttpIdentityGateway(client)
        reader = SqlTaskReader(resources.sessions)
        yield Services(
            list_tasks=ListTasks(identity, reader), get_task=GetTask(identity, reader)
        )


class Container(containers.DeclarativeContainer):
    settings = providers.Singleton(Settings, service="tasks")
    resources = providers.Resource(open_resources, settings=settings)
    services = providers.Resource(open_services, resources=resources)
