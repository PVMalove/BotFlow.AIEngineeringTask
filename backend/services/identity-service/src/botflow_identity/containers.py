"""Composition root. Resource wiring is added alongside concrete adapters."""

from dependency_injector import containers, providers


class Container(containers.DeclarativeContainer):
    config = providers.Configuration()
