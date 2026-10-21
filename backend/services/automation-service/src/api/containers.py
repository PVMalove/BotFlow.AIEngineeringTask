"""Service composition root; each process owns its infrastructure lifecycle."""

from botflow_platform.resources import open_resources
from botflow_platform.settings import Settings
from dependency_injector import containers, providers


class Container(containers.DeclarativeContainer):
    settings = providers.Singleton(Settings, service="automation")
    resources = providers.Resource(open_resources, settings=settings)
