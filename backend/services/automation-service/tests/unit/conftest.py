"""In-memory ports of the automation service: transaction semantics, no database.

Work happens on a copy of the world; a commit stages the events of the tracked
aggregates (outbox rows and notification intents) and then publishes the copy.
"""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self
from uuid import UUID

from kernel_domain.domain_event import DomainEvent
from kernel_domain.entity import Entity

from application.ports import TelegramMember
from domain.entities.job import Job
from domain.entities.notification_intent import NotificationIntent
from domain.events.job_domain_event import (
    JobCompleted,
    JobRestarted,
    TextJobAccepted,
    VoiceJobAccepted,
    VoiceJobFailed,
    VoiceTranscribed,
)
from domain.job_limits import JobCounts
from domain.notices import task_created_notice
from domain.repositories import InboxRepository, IntentRepository, JobRepository
from domain.value_objects.intent_id import IntentId
from domain.value_objects.intent_status import IntentStatus
from domain.value_objects.job_id import JobId
from domain.value_objects.job_status import JobKind, JobStatus
from infrastructure.messaging.envelopes import (
    create_task_envelope,
    transcribe_envelope,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@dataclass
class World:
    jobs: dict[JobId, Job] = field(default_factory=dict)
    intents: dict[IntentId, NotificationIntent] = field(default_factory=dict)
    inbox: set[str] = field(default_factory=set)
    outbox: dict[UUID, tuple[str, dict[str, Any]]] = field(default_factory=dict)
    commits: int = 0


class FakeJobs:
    def __init__(self, data: World, track: Callable[[Entity[Any]], None]) -> None:
        self._data = data
        self._track = track

    async def lock_acceptance(self) -> None:
        return None

    async def count_unfinished(self, author_id: UUID) -> JobCounts:
        open_jobs = [
            j for j in self._data.jobs.values() if j.status is JobStatus.ACCEPTED
        ]
        voice = [j for j in open_jobs if j.kind is JobKind.VOICE]
        return JobCounts(
            len(open_jobs),
            len([j for j in open_jobs if j.author_id == author_id]),
            len(voice),
            len([j for j in voice if j.author_id == author_id]),
        )

    async def get_by_source(
        self, bot_id: int, chat_id: int, message_id: int
    ) -> Job | None:
        return next(
            (
                deepcopy(j)
                for j in self._data.jobs.values()
                if (j.bot_id, j.chat_id, j.message_id) == (bot_id, chat_id, message_id)
            ),
            None,
        )

    async def get(self, job_id: JobId, *, lock: bool = False) -> Job | None:
        job = self._data.jobs.get(job_id)
        return deepcopy(job) if job else None

    async def add(self, job: Job) -> None:
        self._data.jobs[job.id] = deepcopy(job)
        self._track(job)

    async def update(self, job: Job) -> None:
        self._data.jobs[job.id] = deepcopy(job)
        self._track(job)


class FakeIntents:
    def __init__(self, data: World, track: Callable[[Entity[Any]], None]) -> None:
        self._data = data
        self._track = track

    async def get(self, intent_id: IntentId) -> NotificationIntent | None:
        intent = self._data.intents.get(intent_id)
        return deepcopy(intent) if intent else None

    async def add(self, intent: NotificationIntent) -> bool:
        if intent.id in self._data.intents:
            return False
        self._data.intents[intent.id] = deepcopy(intent)
        self._track(intent)
        return True

    async def pending(self, limit: int) -> list[IntentId]:
        return [
            i.id
            for i in self._data.intents.values()
            if i.status is IntentStatus.PENDING
        ][:limit]

    async def update(self, intent: NotificationIntent) -> None:
        self._data.intents[intent.id] = deepcopy(intent)
        self._track(intent)


class FakeInbox:
    def __init__(self, data: World) -> None:
        self._data = data

    async def claim(self, message_id: str) -> bool:
        if message_id in self._data.inbox:
            return False
        self._data.inbox.add(message_id)
        return True


class FakeAutomationUnitOfWork:
    def __init__(self, world: World) -> None:
        self._world = world
        self._working = World()
        self._tracked: list[Entity[Any]] = []
        self.jobs: JobRepository = FakeJobs(self._working, self._tracked.append)
        self.intents: IntentRepository = FakeIntents(
            self._working, self._tracked.append
        )
        self.inbox: InboxRepository = FakeInbox(self._working)

    async def __aenter__(self) -> Self:
        self._working.jobs = deepcopy(self._world.jobs)
        self._working.intents = deepcopy(self._world.intents)
        self._working.inbox = set(self._world.inbox)
        self._working.outbox = deepcopy(self._world.outbox)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        for entity in self._tracked:
            for event in entity.pull_events():
                self._stage(event)
        self._world.jobs = deepcopy(self._working.jobs)
        self._world.intents = deepcopy(self._working.intents)
        self._world.inbox = set(self._working.inbox)
        self._world.outbox = deepcopy(self._working.outbox)
        self._world.commits += 1

    async def rollback(self) -> None:
        return None

    def _stage(self, event: DomainEvent) -> None:
        """What the SQL adapter stores for an event, in the working copy."""
        data = self._working
        match event:
            case TextJobAccepted() | VoiceTranscribed():
                entry_id, _, envelope = create_task_envelope(
                    job_id=event.job_id.value,
                    bot_id=event.bot_id,
                    chat_id=event.chat_id,
                    message_id=event.message_id,
                    author_id=event.author_id,
                    author_telegram_id=event.author_telegram_id,
                    author_name=event.author_name,
                    text=event.text,
                    received_at=event.received_at,
                )
                data.outbox[entry_id] = ("CreateTask", envelope)
            case VoiceJobAccepted():
                entry_id, _, envelope = transcribe_envelope(
                    job_id=event.job_id.value, received_at=event.received_at
                )
                data.outbox[entry_id] = ("TranscribeVoice", envelope)
            case JobCompleted():
                intent_id = IntentId.for_result(event.job_id.value)
                data.intents.setdefault(
                    intent_id,
                    NotificationIntent.result(
                        intent_id,
                        chat_id=event.chat_id,
                        member_id=event.author_id,
                        kind="task.result",
                        body=task_created_notice(event.text),
                    ),
                )
            case VoiceJobFailed():
                intent_id = IntentId.for_result(event.job_id.value)
                data.intents.setdefault(
                    intent_id,
                    NotificationIntent.result(
                        intent_id,
                        chat_id=event.chat_id,
                        member_id=event.author_id,
                        kind="transcription.result",
                        body=event.notice,
                    ),
                )
            case JobRestarted():
                data.intents.pop(IntentId.for_result(event.job_id.value), None)
            case _:
                return


class Clock:
    def __init__(self) -> None:
        self.current = NOW

    def now(self) -> datetime:
        return self.current


class Log:
    def __init__(self) -> None:
        self.events: list[str] = []


class LoggedUnitOfWork(FakeAutomationUnitOfWork):
    """Records every commit, to tell what happened before and after it."""

    def __init__(self, world: World, log: Log) -> None:
        super().__init__(world)
        self._log = log

    async def commit(self) -> None:
        await super().commit()
        self._log.events.append("committed")


class FakeIdentity:
    def __init__(self, user: int, member_id: UUID) -> None:
        self.member: TelegramMember | None = TelegramMember(
            user, "member", "approved", 1, member_id
        )
        self.failure: Exception | None = None

    async def find_member(self, telegram_id: int) -> TelegramMember | None:
        if self.failure:
            raise self.failure
        return self.member


class FakeMessenger:
    def __init__(self, log: Log) -> None:
        self.log = log
        self.sent: list[tuple[int, str]] = []
        self.failure: Exception | None = None

    async def send(self, chat_id: int, text: str) -> None:
        self.log.events.append("send")
        if self.failure:
            raise self.failure
        self.sent.append((chat_id, text))
