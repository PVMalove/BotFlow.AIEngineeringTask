"""Transaction contract of the automation service."""

from typing import Protocol

from kernel_platform.unit_of_work import UnitOfWork

from domain.repositories import InboxRepository, IntentRepository, JobRepository


class AutomationUnitOfWork(UnitOfWork, Protocol):
    jobs: JobRepository
    intents: IntentRepository
    inbox: InboxRepository
