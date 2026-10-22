"""Service composition root; each process owns its infrastructure lifecycle."""

from botflow_platform.resources import open_database, open_resources
from botflow_platform.settings import Settings
from dependency_injector import containers, providers


class Container(containers.DeclarativeContainer):
    settings = providers.Singleton(Settings, service="identity")
    resources = providers.Resource(open_resources, settings=settings)
    # CLI processes need only the database, not RabbitMQ.
    database = providers.Resource(open_database, settings=settings)
