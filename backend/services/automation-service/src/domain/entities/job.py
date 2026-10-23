from datetime import datetime
from uuid import UUID

from kernel_domain.entity import Entity

from domain.entities.voice_message import VoiceMessage
from domain.events.job_domain_event import (
    JobCompleted,
    JobRestarted,
    TextJobAccepted,
    VoiceJobAccepted,
    VoiceJobFailed,
    VoiceTranscribed,
)
from domain.value_objects.job_id import JobId
from domain.value_objects.job_status import JobKind, JobStatus, VoiceStage


class Job(Entity[JobId]):
    """Accepted work whose slot frees when it completes or finally fails.

    A text job carries its text; a voice job gets it from the transcript.
    """

    def __init__(
        self,
        id: JobId,
        *,
        bot_id: int,
        chat_id: int,
        message_id: int,
        author_id: UUID,
        author_telegram_id: int,
        author_name: str,  # snapshot from Telegram, at most 64 characters
        kind: JobKind,
        text: str | None,
        status: JobStatus,
        task_id: UUID | None,
        last_error: str | None,
        message_date: datetime,  # when Telegram says the author sent it
        received_at: datetime,  # when this process received the update
        acked_at: datetime | None,
        created_at: datetime,
        updated_at: datetime,
        voice: VoiceMessage | None,
    ) -> None:
        super().__init__(id)
        self.bot_id = bot_id
        self.chat_id = chat_id
        self.message_id = message_id
        self.author_id = author_id
        self.author_telegram_id = author_telegram_id
        self.author_name = author_name
        self.kind = kind
        self.text = text
        self.status = status
        self.task_id = task_id
        self.last_error = last_error
        self.message_date = message_date
        self.received_at = received_at
        self.acked_at = acked_at
        self.created_at = created_at
        self.updated_at = updated_at
        self.voice = voice

    @classmethod
    def accept_text(
        cls,
        *,
        bot_id: int,
        chat_id: int,
        message_id: int,
        author_id: UUID,
        author_telegram_id: int,
        author_name: str,
        text: str,
        message_date: datetime,
        received_at: datetime,
        now: datetime,
    ) -> "Job":
        job = cls._new(
            JobKind.TEXT,
            text,
            None,
            bot_id=bot_id,
            chat_id=chat_id,
            message_id=message_id,
            author_id=author_id,
            author_telegram_id=author_telegram_id,
            author_name=author_name,
            message_date=message_date,
            received_at=received_at,
            now=now,
        )
        job.add_domain_event(
            TextJobAccepted(
                job_id=job.id,
                bot_id=bot_id,
                chat_id=chat_id,
                message_id=message_id,
                author_id=author_id,
                author_telegram_id=author_telegram_id,
                author_name=author_name,
                received_at=received_at,
                text=text,
            )
        )
        return job

    @classmethod
    def accept_voice(
        cls,
        voice: VoiceMessage,
        *,
        bot_id: int,
        chat_id: int,
        message_id: int,
        author_id: UUID,
        author_telegram_id: int,
        author_name: str,
        message_date: datetime,
        received_at: datetime,
        now: datetime,
    ) -> "Job":
        job = cls._new(
            JobKind.VOICE,
            None,
            voice,
            bot_id=bot_id,
            chat_id=chat_id,
            message_id=message_id,
            author_id=author_id,
            author_telegram_id=author_telegram_id,
            author_name=author_name,
            message_date=message_date,
            received_at=received_at,
            now=now,
        )
        job.add_domain_event(
            VoiceJobAccepted(
                job_id=job.id,
                bot_id=bot_id,
                chat_id=chat_id,
                message_id=message_id,
                author_id=author_id,
                author_telegram_id=author_telegram_id,
                author_name=author_name,
                received_at=received_at,
            )
        )
        return job

    @classmethod
    def reconstitute(
        cls,
        id: JobId,
        *,
        bot_id: int,
        chat_id: int,
        message_id: int,
        author_id: UUID,
        author_telegram_id: int,
        author_name: str,
        kind: JobKind,
        text: str | None,
        status: JobStatus,
        task_id: UUID | None,
        last_error: str | None,
        message_date: datetime,
        received_at: datetime,
        acked_at: datetime | None,
        created_at: datetime,
        updated_at: datetime,
        voice: VoiceMessage | None,
    ) -> "Job":
        return cls(
            id,
            bot_id=bot_id,
            chat_id=chat_id,
            message_id=message_id,
            author_id=author_id,
            author_telegram_id=author_telegram_id,
            author_name=author_name,
            kind=kind,
            text=text,
            status=status,
            task_id=task_id,
            last_error=last_error,
            message_date=message_date,
            received_at=received_at,
            acked_at=acked_at,
            created_at=created_at,
            updated_at=updated_at,
            voice=voice,
        )

    def mark_acked(self, at: datetime) -> None:
        """The author heard that the message is safe; recorded once."""
        if self.acked_at is None:
            self.acked_at = at

    def record_result(self, task_id: UUID, now: datetime) -> bool:
        """Tasks created the task. A job that failed after its retries completes as
        well: a late result wins. False when it was already completed."""
        if self.status is JobStatus.COMPLETED:
            return False
        self.status = JobStatus.COMPLETED
        self.task_id = task_id
        self.last_error = None
        self.updated_at = now
        self.add_domain_event(
            JobCompleted(
                job_id=self.id,
                chat_id=self.chat_id,
                author_id=self.author_id,
                text=self.text,
            )
        )
        return True

    def fail_delivery(self, error: str, now: datetime) -> bool:
        """The hand-off of the job is exhausted: it fails and its slot is freed."""
        if self.status is not JobStatus.ACCEPTED:
            return False
        self.status = JobStatus.FAILED
        self.last_error = error
        self.updated_at = now
        return True

    def restart_delivery(self, now: datetime) -> bool:
        """A failed job whose hand-off is replayed becomes accepted again."""
        if self.status is not JobStatus.FAILED:
            return False
        self.status = JobStatus.ACCEPTED
        self.updated_at = now
        return True

    def begin_voice_attempt(self, now: datetime, cold_start: bool) -> bool:
        """Count one processing attempt; False for a job that is already finished."""
        voice = self._unfinished_voice()
        if voice is None:
            return False
        voice.attempts += 1
        if voice.started_at is None:
            # The first attempt fixes whether the record met a cold worker.
            voice.cold_start = cold_start
            voice.started_at = now
        return True

    def checkpoint_audio(
        self, key: str, audio_bytes: int, sha256: str, now: datetime
    ) -> bool:
        """The audio is in the store: a restart resumes from it, not from Telegram."""
        voice = self._unfinished_voice()
        if voice is None:
            return False
        voice.stage = VoiceStage.STORED
        voice.s3_key = key
        voice.audio_bytes = audio_bytes
        voice.audio_sha256 = sha256
        voice.stored_at = now
        return True

    def complete_transcription(
        self,
        text: str,
        language: str | None,
        stt_started_at: datetime,
        stt_finished_at: datetime,
        now: datetime,
    ) -> bool:
        """The transcript becomes the task text and CreateTask is handed on."""
        voice = self._unfinished_voice()
        if voice is None:
            return False
        voice.stage = VoiceStage.DONE
        voice.language = language
        voice.stt_started_at = stt_started_at
        voice.stt_finished_at = stt_finished_at
        self.text = text
        self.updated_at = now
        self.add_domain_event(
            VoiceTranscribed(
                job_id=self.id,
                bot_id=self.bot_id,
                chat_id=self.chat_id,
                message_id=self.message_id,
                author_id=self.author_id,
                author_telegram_id=self.author_telegram_id,
                author_name=self.author_name,
                received_at=self.received_at,
                text=text,
            )
        )
        return True

    def fail_voice(
        self,
        error_code: str,
        notice: str,
        now: datetime,
        stt_started_at: datetime | None = None,
        stt_finished_at: datetime | None = None,
    ) -> bool:
        """A final error ends the job; the author is told. False if already ended."""
        voice = self._unfinished_voice()
        if voice is None:
            return False
        voice.stage = VoiceStage.FAILED
        voice.error_code = error_code
        voice.stt_started_at = stt_started_at or voice.stt_started_at
        voice.stt_finished_at = stt_finished_at or voice.stt_finished_at
        if self.status is JobStatus.ACCEPTED:
            self.status = JobStatus.FAILED
            self.last_error = error_code
            self.updated_at = now
        self.add_domain_event(
            VoiceJobFailed(
                job_id=self.id,
                chat_id=self.chat_id,
                author_id=self.author_id,
                error_code=error_code,
                notice=notice,
            )
        )
        return True

    def restart_voice(self, now: datetime) -> bool:
        """Run a failed voice job again from the stored object, or from Telegram."""
        voice = self.voice
        if voice is None or voice.stage is not VoiceStage.FAILED:
            return False
        voice.stage = (
            VoiceStage.STORED if voice.s3_key is not None else VoiceStage.QUEUED
        )
        voice.error_code = None
        voice.attempts = 0
        voice.started_at = None
        voice.cold_start = False
        if self.status is JobStatus.FAILED:
            self.status = JobStatus.ACCEPTED
            self.last_error = None
            self.updated_at = now
        self.add_domain_event(JobRestarted(job_id=self.id, voice=True))
        return True

    @classmethod
    def _new(
        cls,
        kind: JobKind,
        text: str | None,
        voice: VoiceMessage | None,
        *,
        bot_id: int,
        chat_id: int,
        message_id: int,
        author_id: UUID,
        author_telegram_id: int,
        author_name: str,
        message_date: datetime,
        received_at: datetime,
        now: datetime,
    ) -> "Job":
        return cls(
            JobId.from_source(bot_id, chat_id, message_id),
            bot_id=bot_id,
            chat_id=chat_id,
            message_id=message_id,
            author_id=author_id,
            author_telegram_id=author_telegram_id,
            author_name=author_name,
            kind=kind,
            text=text,
            status=JobStatus.ACCEPTED,
            task_id=None,
            last_error=None,
            message_date=message_date,
            received_at=received_at,
            acked_at=None,
            created_at=now,
            updated_at=now,
            voice=voice,
        )

    def _unfinished_voice(self) -> VoiceMessage | None:
        """The voice part, while the job is accepted and the record is not finished."""
        voice = self.voice
        if self.status is not JobStatus.ACCEPTED or voice is None:
            return None
        return voice if voice.unfinished else None
