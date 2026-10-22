from dataclasses import dataclass
from datetime import datetime

from domain.value_objects.job_status import VoiceStage


@dataclass
class VoiceMessage:
    """The voice part of a job: Telegram's file identity (never a URL) and progress."""

    file_id: str
    file_unique_id: str
    duration_seconds: int
    file_size_bytes: int | None  # Telegram may omit it
    mime_type: str | None
    stage: VoiceStage = VoiceStage.QUEUED
    s3_key: str | None = None
    audio_bytes: int | None = None
    audio_sha256: str | None = None
    language: str | None = None
    error_code: str | None = None
    attempts: int = 0
    cold_start: bool = False
    started_at: datetime | None = None
    stored_at: datetime | None = None
    stt_started_at: datetime | None = None
    stt_finished_at: datetime | None = None

    @property
    def unfinished(self) -> bool:
        return self.stage in (VoiceStage.QUEUED, VoiceStage.STORED)
