"""Transaction contract of the tasks service."""

from typing import Protocol

from kernel_platform.unit_of_work import UnitOfWork

from domain.repositories import InboxRepository, TaskRepository


class TasksUnitOfWork(UnitOfWork, Protocol):
    tasks: TaskRepository
    inbox: InboxRepository
