from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from kernel_domain.domain_event import DomainEvent

from domain.value_objects.job_id import JobId
from domain.value_objects.job_status import JobKind


@dataclass(frozen=True, kw_only=True)
class JobEvent(DomainEvent):
    """Common ancestor of the job events: they all name their job."""

    aggregate_type: str = "Job"

    job_id: JobId

    def aggregate_id(self) -> UUID:
        return self.job_id.value

    def to_payload(self) -> dict[str, object]:
        return {"job_id": str(self.job_id.value)}


@dataclass(frozen=True, kw_only=True)
class JobSource(JobEvent):
    """The Telegram message a job came from, and its author."""

    bot_id: int
    chat_id: int
    message_id: int
    author_id: UUID
    author_telegram_id: int
    author_name: str
    received_at: datetime


@dataclass(frozen=True, kw_only=True)
class TextJobAccepted(JobSource):
    """A text message became a job; its CreateTask is handed to the outbox."""

    event_type: str = "job.text-accepted"

    text: str
    source_type: JobKind = JobKind.TEXT


@dataclass(frozen=True, kw_only=True)
class VoiceJobAccepted(JobSource):
    """A voice message became a job; its TranscribeVoice is handed to the outbox."""

    event_type: str = "job.voice-accepted"


@dataclass(frozen=True, kw_only=True)
class VoiceTranscribed(JobSource):
    """The transcript is the task text: CreateTask is handed to the outbox."""

    event_type: str = "job.voice-transcribed"

    text: str
    source_type: JobKind = JobKind.VOICE


@dataclass(frozen=True, kw_only=True)
class JobCompleted(JobEvent):
    """Tasks created the task: the author is told."""

    event_type: str = "job.completed"

    chat_id: int
    author_id: UUID
    text: str | None


@dataclass(frozen=True, kw_only=True)
class VoiceJobFailed(JobEvent):
    """A final error ended a voice job: the author is told that no task exists."""

    event_type: str = "job.voice-failed"

    chat_id: int
    author_id: UUID
    error_code: str
    notice: str


@dataclass(frozen=True, kw_only=True)
class JobRestarted(JobEvent):
    """A failed job runs again: its stale failure notice and outbox row are reset.

    `voice` tells which row is reset: TranscribeVoice (voice) or CreateTask.
    """

    event_type: str = "job.restarted"

    voice: bool
