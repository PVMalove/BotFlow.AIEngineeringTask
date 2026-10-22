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
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from botflow_platform.retry import RetryPolicy

from application.commands.create_task_message import create_task_message
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
    Transcript,
    TranscriptionTimeout,
    TransientIoError,
    VoiceStore,
    VoiceWork,
)
from domain.jobs import VoiceStage

MAX_ATTEMPTS = 3  # processing attempts of one record before it is failed
MAX_TEXT = 4096  # what Tasks accepts as a task text

# error_code -> what the author reads. Every notice says no task was created.
FAILURES: dict[str, str] = {
    "empty_audio": (
        "Не удалось распознать речь: запись пустая или в ней нет слов. "
        "Задача не создана. Запишите сообщение ещё раз."
    ),
    "corrupted_audio": (
        "Не удалось прочитать голосовое сообщение: файл повреждён. "
        "Задача не создана. Отправьте сообщение ещё раз."
    ),
    "too_long": (
        "Голосовое сообщение длиннее 60 секунд. "
        "Задача не создана. Отправьте более короткое."
    ),
    "too_large": (
        "Голосовое сообщение больше 5 МиБ. Задача не создана. Отправьте более короткое."
    ),
    "timeout": (
        "Распознавание заняло слишком много времени. "
        "Задача не создана. Отправьте сообщение покороче."
    ),
    "file_unavailable": (
        "Telegram больше не отдаёт этот файл. "
        "Задача не создана. Отправьте сообщение ещё раз."
    ),
    "unavailable": (
        "Сервисы распознавания временно недоступны. "
        "Задача не создана. Повторите отправку позже."
    ),
    "text_too_long": (
        "Распознанный текст слишком длинный для задачи. "
        "Задача не создана. Отправьте более короткое сообщение."
    ),
    "failed": (
        "Не удалось обработать голосовое сообщение. "
        "Задача не создана. Повторите отправку позже."
    ),
}

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


def _code_of(error: Exception) -> str:
    for kind, code in _FINAL_ERRORS:
        if isinstance(error, kind):
            return code
    raise error


class TranscribeVoice:
    def __init__(
        self,
        store: VoiceStore,
        telegram: TelegramFiles,
        audio: AudioStore,
        stt: SpeechToText,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        policy: RetryPolicy | None = None,
    ) -> None:
        self._store = store
        self._telegram = telegram
        self._audio = audio
        self._stt = stt
        self._clock = clock
        self._sleep = sleep
        self._policy = policy or RetryPolicy()
        self._served = False

    async def execute(self, job_id: UUID) -> TranscribeResult:
        """Store failures and unexpected errors propagate: the entry stays pending."""
        cold_start = not self._served
        work = await self._store.begin(job_id, self._clock(), cold_start)
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
        message = create_task_message(
            job_id=work.job_id,
            bot_id=work.bot_id,
            chat_id=work.chat_id,
            message_id=work.message_id,
            author_id=work.author_id,
            author_telegram_id=work.author_telegram_id,
            author_name=work.author_name,
            text=text,
            received_at=work.received_at,
        )
        done = await self._store.complete(
            work,
            Transcript(text, transcript.language),
            message,
            started,
            finished,
            self._clock(),
        )
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
        await self._store.checkpoint(
            work.job_id,
            key,
            len(audio),
            hashlib.sha256(audio).hexdigest(),
            self._clock(),
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
        failed = await self._store.fail(
            work, code, FAILURES[code], self._clock(), started, finished
        )
        if not failed:
            return TranscribeResult(TranscribeOutcome.SKIPPED)
        return TranscribeResult(TranscribeOutcome.FAILED, code)
