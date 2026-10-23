"""Boundaries used by the Telegram login and join commands."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from kernel_domain.result import Result

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
