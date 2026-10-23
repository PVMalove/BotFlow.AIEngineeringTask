"""TranscribeVoice: one voice job from Telegram file to a CreateTask outbox row.

Stages: begin -> download -> store the object -> checkpoint -> recognise -> complete.
Every store call is one short transaction; none is open while the file is
downloaded, uploaded or recognised. A checkpointed record resumes from the stored
object and never asks Telegram again. A final error ends the job in ONE
transaction with a notice to the author and no task.
"""

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from kernel_platform.clock import utcnow
from kernel_platform.retry import RetryPolicy

from application.ports import (
    AudioMissing,
    AudioStore,
    AudioTooLarge,
    AudioTooLong,
    CorruptedAudio,
    EmptyAudio,
    SpeechToText,
    StuckWorkerError,
    TelegramFiles,
    TelegramFileUnavailable,
    TranscriptionTimeout,
    TransientIoError,
    VoiceWork,
)
from domain.notices import VOICE_FAILURES
from domain.unit_of_work import AutomationUnitOfWork
from domain.value_objects.job_id import JobId
from domain.value_objects.job_status import VoiceStage

MAX_ATTEMPTS = 3  # processing attempts of one record before it is failed
MAX_TEXT = 4096  # what Tasks accepts as a task text

_FINAL_ERRORS: tuple[tuple[type[Exception], str], ...] = (
    (TelegramFileUnavailable, "file_unavailable"),
    (AudioTooLarge, "too_large"),
    (TransientIoError, "unavailable"),  # retries are exhausted when it gets here
    (EmptyAudio, "empty_audio"),
    (CorruptedAudio, "corrupted_audio"),
    (AudioTooLong, "too_long"),
    (TranscriptionTimeout, "timeout"),
)


class TranscribeOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"  # unknown or already finished: only acknowledge


@dataclass(frozen=True, slots=True)
class TranscribeResult:
    outcome: TranscribeOutcome
    error_code: str | None = None

    @property
    def restart_required(self) -> bool:
        """A timed-out decode cannot be stopped: the process must be replaced."""
        return self.error_code == "timeout"


@dataclass(frozen=True)
class TranscribeVoiceCommand:
    job_id: UUID


class TranscribeVoiceCommandHandler:
    def __init__(
        self,
        uow_factory: Callable[[], AutomationUnitOfWork],
        telegram: TelegramFiles,
        audio: AudioStore,
        stt: SpeechToText,
        *,
        clock: Callable[[], datetime] = utcnow,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        policy: RetryPolicy | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._telegram = telegram
        self._audio = audio
        self._stt = stt
        self._clock = clock
        self._sleep = sleep
        self._policy = policy or RetryPolicy()
        self._served = False

    async def execute(self, command: TranscribeVoiceCommand) -> TranscribeResult:
        """Store failures and unexpected errors propagate: the entry stays pending."""
        cold_start = not self._served
        work = await self._begin(command.job_id, cold_start)
        if work is None:
            return TranscribeResult(TranscribeOutcome.SKIPPED)
        self._served = True
        if work.attempts > MAX_ATTEMPTS:
            return await self._fail(work, "failed")
        try:
            audio = await self._audio_of(work)
        except (
            TelegramFileUnavailable,
            AudioTooLarge,
            TransientIoError,
        ) as error:
            return await self._fail(work, _code_of(error))
        started = self._clock()
        try:
            transcript = await self._stt.transcribe(audio)
        except (
            EmptyAudio,
            CorruptedAudio,
            AudioTooLong,
            AudioTooLarge,
        ) as error:
            return await self._fail(work, _code_of(error), started)
        except TranscriptionTimeout as error:
            try:
                return await self._fail(work, _code_of(error), started)
            except Exception as exc:
                raise StuckWorkerError(exc) from exc
        finished = self._clock()
        text = transcript.text.strip()
        if not text:
            return await self._fail(work, "empty_audio", started, finished)
        if len(text) > MAX_TEXT:
            return await self._fail(work, "text_too_long", started, finished)
        done = await self._complete(work, transcript.language, text, started, finished)
        return TranscribeResult(
            TranscribeOutcome.COMPLETED if done else TranscribeOutcome.SKIPPED
        )

    async def _audio_of(self, work: VoiceWork) -> bytes:
        if work.stage == VoiceStage.STORED.value and work.s3_key is not None:
            try:
                return await self._retry(lambda: self._audio.get(work.job_id))
            except AudioMissing:
                pass  # the checkpoint outlived its object: fetch the file again
        audio = await self._retry(lambda: self._telegram.fetch(work.file_id))
        key = await self._retry(
            lambda: self._audio.put(work.job_id, audio, work.mime_type)
        )
        await self._checkpoint(
            work.job_id, key, len(audio), hashlib.sha256(audio).hexdigest()
        )
        return audio

    async def _retry[T](self, call: Callable[[], Awaitable[T]]) -> T:
        """Transient failures wait 5 s, then 30 s; the third failure is final."""
        failed = 0
        while True:
            try:
                return await call()
            except TransientIoError:
                failed += 1
                delay = self._policy.delay_after(failed)
                if delay is None:
                    raise
                await self._sleep(delay.total_seconds())

    async def _fail(
        self,
        work: VoiceWork,
        code: str,
        started: datetime | None = None,
        finished: datetime | None = None,
    ) -> TranscribeResult:
        if started is not None and finished is None:
            finished = self._clock()
        failed = await self._record_failure(work, code, started, finished)
        if not failed:
            return TranscribeResult(TranscribeOutcome.SKIPPED)
        return TranscribeResult(TranscribeOutcome.FAILED, code)

    async def _begin(self, job_id: UUID, cold_start: bool) -> VoiceWork | None:
        """Count this attempt; None for an unknown job or one already finished."""
        async with self._uow_factory() as uow:
            job = await uow.jobs.get(JobId(job_id), lock=True)
            if job is None or job.voice is None:
                return None
            if not job.begin_voice_attempt(self._clock(), cold_start):
                return None
            await uow.jobs.update(job)
            await uow.commit()
        return VoiceWork(
            job_id=job_id,
            bot_id=job.bot_id,
            chat_id=job.chat_id,
            message_id=job.message_id,
            author_id=job.author_id,
            author_telegram_id=job.author_telegram_id,
            author_name=job.author_name,
            received_at=job.received_at,
            file_id=job.voice.file_id,
            mime_type=job.voice.mime_type,
            stage=job.voice.stage.value,
            s3_key=job.voice.s3_key,
            attempts=job.voice.attempts,
        )

    async def _checkpoint(
        self, job_id: UUID, key: str, audio_bytes: int, sha256: str
    ) -> None:
        async with self._uow_factory() as uow:
            job = await uow.jobs.get(JobId(job_id), lock=True)
            if job is not None and job.checkpoint_audio(
                key, audio_bytes, sha256, self._clock()
            ):
                await uow.jobs.update(job)
                await uow.commit()

    async def _complete(
        self,
        work: VoiceWork,
        language: str | None,
        text: str,
        started: datetime,
        finished: datetime,
    ) -> bool:
        """Text, stage and the CreateTask outbox row in ONE transaction."""
        async with self._uow_factory() as uow:
            job = await uow.jobs.get(JobId(work.job_id), lock=True)
            if job is None or not job.complete_transcription(
                text, language, started, finished, self._clock()
            ):
                return False
            await uow.jobs.update(job)
            await uow.commit()
        return True

    async def _record_failure(
        self,
        work: VoiceWork,
        code: str,
        started: datetime | None,
        finished: datetime | None,
    ) -> bool:
        """Job, voice record and author notice in ONE transaction; slot freed."""
        async with self._uow_factory() as uow:
            job = await uow.jobs.get(JobId(work.job_id), lock=True)
            if job is None or not job.fail_voice(
                code, VOICE_FAILURES[code], self._clock(), started, finished
            ):
                return False
            await uow.jobs.update(job)
            await uow.commit()
        return True


def _code_of(error: Exception) -> str:
    for kind, code in _FINAL_ERRORS:
        if isinstance(error, kind):
            return code
    raise error
