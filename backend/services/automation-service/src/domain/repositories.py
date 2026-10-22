"""Persistence contracts of the Job and NotificationIntent aggregates."""

from typing import Protocol
from uuid import UUID

from domain.entities.job import Job
from domain.entities.notification_intent import NotificationIntent
from domain.job_limits import JobCounts
from domain.value_objects.intent_id import IntentId
from domain.value_objects.job_id import JobId


class JobRepository(Protocol):
    async def lock_acceptance(self) -> None:
        """Serialize acceptance so the limits cannot be overshot by racing updates."""
        ...

    async def count_unfinished(self, author_id: UUID) -> JobCounts: ...

    async def get_by_source(
        self, bot_id: int, chat_id: int, message_id: int
    ) -> Job | None: ...

    async def get(self, job_id: JobId, *, lock: bool = False) -> Job | None:
        """`lock` serializes concurrent changes to the same job."""
        ...

    async def add(self, job: Job) -> None: ...

    async def update(self, job: Job) -> None:
        """Store the changed state; the job's events are staged by the commit."""
        ...


class IntentRepository(Protocol):
    async def get(self, intent_id: IntentId) -> NotificationIntent | None: ...

    async def add(self, intent: NotificationIntent) -> bool:
        """Store a new intent; False when its id already exists."""
        ...

    async def pending(self, limit: int) -> list[IntentId]: ...

    async def update(self, intent: NotificationIntent) -> None: ...


class InboxRepository(Protocol):
    async def claim(self, message_id: str) -> bool:
        """Record a consumed message; False for a redelivery."""
        ...
