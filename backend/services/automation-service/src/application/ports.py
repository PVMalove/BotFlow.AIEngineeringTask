"""Boundaries used by the Telegram login and join commands."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from kernel_domain.result import Result

from domain.notifications import IntentStatus, NotificationIntent
from domain.value_objects.join_outcome import JoinOutcome


@dataclass(frozen=True, slots=True)
class TelegramMember:
    telegram_id: int
    role: str
    status: str
    access_version: int = 0
    member_id: UUID | None = None  # Identity's stable member id


class IdentityUnavailable(Exception):
    """The identity service cannot complete the operation."""


class MemberDirectory(Protocol):
    async def find_member(self, telegram_id: int) -> TelegramMember | None: ...


class IdentityGateway(Protocol):
    async def find_member(self, telegram_id: int) -> TelegramMember | None: ...

    async def issue_login_code(self, telegram_id: int) -> Result[str]:
        """A denied or rate-limited request is an error; raises IdentityUnavailable."""
        ...

    async def submit_join_request(
        self, telegram_id: int, display_name: str | None
    ) -> Result[JoinOutcome]: ...


class Messenger(Protocol):
    async def send(self, chat_id: int, text: str) -> None: ...


class UpdateInbox(Protocol):
    async def claim(self, update_id: int) -> bool: ...


class IntentStore(Protocol):
    async def store_event(self, message_id: str, intent: NotificationIntent) -> bool:
        """Inbox record and intent in ONE transaction; False for a redelivery."""
        ...

    async def pending(self, limit: int) -> list[UUID]: ...

    async def mark_enqueued(self, intent_id: UUID) -> None: ...

    async def get(self, intent_id: UUID) -> NotificationIntent | None: ...

    async def settle(self, intent_id: UUID, status: IntentStatus) -> None: ...

    async def record_failure(self, intent_id: UUID, max_attempts: int) -> IntentStatus:
        """Count a failed delivery; FAILED once `max_attempts` is reached."""
        ...


@dataclass(frozen=True, slots=True)
class QueuedIntent:
    entry_id: str
    intent_id: UUID | None  # None for an entry that names no intent


class NotificationQueue(Protocol):
    async def add(self, intent_id: UUID) -> None: ...

    async def claim_idle(self, count: int) -> list[QueuedIntent]: ...

    async def read(self, count: int, block_ms: int) -> list[QueuedIntent]: ...

    async def ack(self, entry_id: str) -> None: ...


class TelegramFileUnavailable(Exception):
    """Telegram no longer serves this file: a retry cannot help."""


class AudioTooLarge(Exception):
    """The audio is above the 5 MiB limit, by Telegram's size or by the bytes read."""


class AudioMissing(Exception):
    """The checkpointed object is not in the audio store."""


class TransientIoError(Exception):
    """A network or storage failure worth retrying; carries no driver message."""


class EmptyAudio(Exception):
    """No speech was found in the audio."""


class CorruptedAudio(Exception):
    """The audio cannot be decoded."""


class AudioTooLong(Exception):
    """The audio is longer than a voice task may be."""


class TranscriptionTimeout(Exception):
    """Speech recognition did not finish in time."""

    restart_required: bool = True


class StuckWorkerError(Exception):
    """The decode thread timed out and could not settle; the process must exit."""

    restart_required: bool = True


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    language: str | None  # detected by the model, e.g. "ru" or "hy"


class TelegramFiles(Protocol):
    async def fetch(self, file_id: str) -> bytes:
        """The whole file in memory; no URL or token leaves the adapter."""
        ...


class AudioStore(Protocol):
    async def put(self, job_id: UUID, data: bytes, content_type: str | None) -> str:
        """Store under a key derived from the job; the key is returned."""
        ...

    async def get(self, job_id: UUID) -> bytes: ...


class SpeechToText(Protocol):
    async def transcribe(self, audio: bytes) -> Transcript: ...


@dataclass(frozen=True, slots=True)
class VoiceWork:
    """What the STT worker needs to process one voice job."""

    job_id: UUID
    bot_id: int
    chat_id: int
    message_id: int
    author_id: UUID
    author_telegram_id: int
    author_name: str
    received_at: datetime
    file_id: str
    mime_type: str | None
    stage: str  # a VoiceStage value: queued or stored here
    s3_key: str | None
    attempts: int  # including this one


class VoiceStore(Protocol):
    """Every method is one short transaction; none is open during slow work."""

    async def begin(
        self, job_id: UUID, now: datetime, cold_start: bool
    ) -> VoiceWork | None:
        """Count this attempt; None for an unknown job or one already finished."""
        ...

    async def checkpoint(
        self, job_id: UUID, key: str, audio_bytes: int, sha256: str, now: datetime
    ) -> None:
        """The audio is in the store: a restart resumes from it, not from Telegram."""
        ...

    async def complete(
        self,
        work: VoiceWork,
        transcript: Transcript,
        create_task: "OutboxMessage",
        stt_started_at: datetime,
        stt_finished_at: datetime,
        now: datetime,
    ) -> bool:
        """Text, stage and the CreateTask outbox row in ONE transaction."""
        ...

    async def fail(
        self,
        work: VoiceWork,
        error_code: str,
        body: str,
        now: datetime,
        stt_started_at: datetime | None = None,
        stt_finished_at: datetime | None = None,
    ) -> bool:
        """Job, voice record and author notice in ONE transaction; the slot is freed."""
        ...


@dataclass(frozen=True, slots=True)
class QueuedVoice:
    entry_id: str
    job_id: UUID | None  # None for an entry that names no job


class VoiceQueue(Protocol):
    """Voice jobs waiting for the STT worker; one entry at a time."""

    async def ensure_group(self) -> None: ...

    async def claim_idle(self, count: int) -> list[QueuedVoice]: ...

    async def read(self, count: int, block_ms: int) -> list[QueuedVoice]: ...

    async def ack(self, entry_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class NewJob:
    id: UUID
    bot_id: int
    chat_id: int
    message_id: int
    author_id: UUID
    author_telegram_id: int
    author_name: str  # snapshot from Telegram, at most 64 characters
    text: str
    message_date: datetime  # when Telegram says the author sent it
    received_at: datetime  # when this process received the update


@dataclass(frozen=True, slots=True)
class NewVoiceJob:
    """A voice message: only Telegram's file identity is kept, never a file URL."""

    id: UUID
    bot_id: int
    chat_id: int
    message_id: int
    author_id: UUID
    author_telegram_id: int
    author_name: str  # snapshot from Telegram, at most 64 characters
    file_id: str
    file_unique_id: str
    duration_seconds: int
    file_size_bytes: int | None  # Telegram may omit it
    mime_type: str | None
    message_date: datetime  # when Telegram says the author sent it
    received_at: datetime  # when this process received the update


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    """A message written in the business transaction and relayed to RabbitMQ."""

    id: UUID
    correlation_id: UUID
    type: str
    routing_key: str
    payload: dict[str, Any]  # the complete envelope


class AcceptOutcome(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"  # the same bot/chat/message was already accepted
    LIMIT = "limit"  # too many unfinished jobs; nothing was stored
    VOICE_LIMIT = "voice_limit"  # too many unfinished voice jobs; nothing was stored


@dataclass(frozen=True, slots=True)
class Accepted:
    outcome: AcceptOutcome
    job_id: UUID
    acknowledged: bool  # a positive ACK was already recorded for this job


class JobStore(Protocol):
    async def accept(self, job: NewJob, message: OutboxMessage) -> Accepted:
        """Duplicate check, limits, job and outbox row in ONE transaction."""
        ...

    async def mark_acked(self, job_id: UUID, at: datetime) -> None: ...


class VoiceJobStore(Protocol):
    async def accept_voice(self, job: NewVoiceJob, message: OutboxMessage) -> Accepted:
        """The same path as `accept`: job, voice record and outbox row at once."""
        ...

    async def mark_acked(self, job_id: UUID, at: datetime) -> None: ...


@dataclass(frozen=True, slots=True)
class JobSummary:
    id: UUID
    chat_id: int
    author_id: UUID
    text: str | None


class ResultOutcome(StrEnum):
    RECORDED = "recorded"
    DUPLICATE = "duplicate"  # this result was already recorded
    UNKNOWN_JOB = "unknown_job"  # no such job: the message can never work


class TaskResultStore(Protocol):
    async def record(
        self,
        message_id: str,
        job_id: UUID,
        task_id: UUID,
        intent_for: Callable[[JobSummary], NotificationIntent],
    ) -> ResultOutcome:
        """Inbox, job completion and intent in ONE transaction.

        A job that failed after its retries completes as well: a late result wins.
        """
        ...
