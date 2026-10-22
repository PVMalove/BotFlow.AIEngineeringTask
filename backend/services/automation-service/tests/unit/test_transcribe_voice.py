"""TranscribeVoiceCommandHandler: every stage and error branch, in memory."""

import hashlib
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from conftest import NOW, Log, LoggedUnitOfWork, World
from kernel_platform.retry import RetryPolicy

from application.commands.transcribe_voice import (
    TranscribeOutcome,
    TranscribeVoiceCommand,
    TranscribeVoiceCommandHandler,
)
from application.ports import (
    AudioMissing,
    AudioTooLarge,
    AudioTooLong,
    CorruptedAudio,
    EmptyAudio,
    StuckWorkerError,
    TelegramFileUnavailable,
    Transcript,
    TranscriptionTimeout,
    TransientIoError,
)
from domain.entities.job import Job
from domain.entities.voice_message import VoiceMessage
from domain.notices import VOICE_FAILURES
from domain.value_objects.intent_id import IntentId
from domain.value_objects.job_id import JobId
from domain.value_objects.job_status import JobStatus, VoiceStage
from infrastructure.messaging.envelopes import outbox_id_for

AUTHOR_ID = uuid4()
CHAT = 4_200_000_001
AUDIO = b"OggS-audio-bytes"


def voice_job(message_id: int = 7, **voice: Any) -> Job:
    job = Job.accept_voice(
        VoiceMessage("AwACfile", "uniq", 12, 48_000, "audio/ogg", **voice),
        bot_id=123,
        chat_id=CHAT,
        message_id=message_id,
        author_id=AUTHOR_ID,
        author_telegram_id=CHAT,
        author_name="Ann (@ann)",
        message_date=NOW,
        received_at=NOW,
        now=NOW,
    )
    job.pull_events()  # accepted long ago: its CreateTask hand-off is not under test
    return job


JOB = voice_job()
JOB_ID = JOB.id.value


class Telegram:
    def __init__(self, log: Log) -> None:
        self.log = log
        self.errors: list[Exception] = []  # raised one per call, then success
        self.calls: list[str] = []

    async def fetch(self, file_id: str) -> bytes:
        self.log.events.append("fetch")
        self.calls.append(file_id)
        if self.errors:
            raise self.errors.pop(0)
        return AUDIO


class Audio:
    def __init__(self, log: Log) -> None:
        self.log = log
        self.put_errors: list[Exception] = []
        self.get_errors: list[Exception] = []
        self.puts: list[tuple[UUID, bytes, str | None]] = []

    async def put(self, job_id: UUID, data: bytes, content_type: str | None) -> str:
        self.log.events.append("put")
        if self.put_errors:
            raise self.put_errors.pop(0)
        self.puts.append((job_id, data, content_type))
        return f"voice/{job_id}"

    async def get(self, job_id: UUID) -> bytes:
        self.log.events.append("get")
        if self.get_errors:
            raise self.get_errors.pop(0)
        return AUDIO


class Stt:
    def __init__(self, log: Log) -> None:
        self.log = log
        self.error: Exception | None = None
        self.transcript = Transcript("Купить молоко", "ru")
        self.heard: list[bytes] = []

    async def transcribe(self, audio: bytes) -> Transcript:
        self.log.events.append("stt")
        self.heard.append(audio)
        if self.error:
            raise self.error
        return self.transcript


class Harness:
    def __init__(self, job: Job | None = JOB) -> None:
        self.log = Log()
        self.world = World()
        if job is not None:
            self.world.jobs[job.id] = job
        self.telegram = Telegram(self.log)
        self.audio = Audio(self.log)
        self.stt = Stt(self.log)
        self.sleeps: list[float] = []
        self.ticks = 0
        self.begin_error: Exception | None = None
        self.fail_after_calls: int | None = None  # the n-th transaction onwards fails
        self.calls = 0
        self.sut = TranscribeVoiceCommandHandler(
            self._unit_of_work,
            self.telegram,
            self.audio,
            self.stt,
            clock=self.clock,
            sleep=self.sleep,
            policy=RetryPolicy(jitter=0),
        )

    def clock(self) -> Any:
        self.ticks += 1
        return NOW + timedelta(seconds=self.ticks)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def job(self, job_id: JobId | None = None) -> Job:
        return self.world.jobs[job_id or JOB.id]

    async def run(self, job_id: UUID = JOB_ID) -> Any:
        return await self.sut.execute(TranscribeVoiceCommand(job_id))

    def _unit_of_work(self) -> LoggedUnitOfWork:
        self.calls += 1
        if self.begin_error and self.calls == 1:
            raise self.begin_error
        if self.fail_after_calls is not None and self.calls >= self.fail_after_calls:
            raise RuntimeError("db connection lost")
        return LoggedUnitOfWork(self.world, self.log)


async def test_the_stages_run_in_order_and_the_job_completes_once() -> None:
    h = Harness()

    result = await h.run()

    assert result.outcome is TranscribeOutcome.COMPLETED and result.error_code is None
    assert h.log.events == [
        "committed",  # begin
        "fetch",
        "put",
        "committed",  # checkpoint
        "stt",
        "committed",  # complete
    ]
    assert h.telegram.calls == ["AwACfile"]
    assert h.audio.puts == [(JOB_ID, AUDIO, "audio/ogg")]
    assert h.stt.heard == [AUDIO]
    voice = h.job().voice
    assert voice is not None and voice.stage is VoiceStage.DONE


async def test_the_checkpoint_names_the_object_its_size_and_checksum() -> None:
    h = Harness()

    await h.run()

    voice = h.job().voice
    assert voice is not None
    assert (voice.s3_key, voice.audio_bytes) == (f"voice/{JOB_ID}", len(AUDIO))
    assert voice.audio_sha256 == hashlib.sha256(AUDIO).hexdigest()
    assert voice.stored_at is not None


async def test_the_transcript_becomes_the_create_task_of_the_text_path() -> None:
    h = Harness()

    await h.run()

    job = h.job()
    assert job.text == "Купить молоко"
    assert job.voice is not None and job.voice.language == "ru"
    kind, envelope = h.world.outbox[outbox_id_for(JOB_ID)]
    assert kind == "CreateTask" == envelope["type"]
    assert envelope["correlation_id"] == str(JOB_ID)
    assert envelope["payload"] == {
        "job_id": str(JOB_ID),
        "bot_id": 123,
        "chat_id": CHAT,
        "message_id": 7,
        "author_id": str(AUTHOR_ID),
        "author_telegram_id": CHAT,
        "author_name": "Ann (@ann)",
        "text": "Купить молоко",
    }
    assert envelope["occurred_at"] == NOW.isoformat()


async def test_the_transcript_is_trimmed_before_it_becomes_the_task_text() -> None:
    h = Harness()
    h.stt.transcript = Transcript("  Позвонить маме \n", "ru")

    await h.run()

    _, envelope = h.world.outbox[outbox_id_for(JOB_ID)]
    assert envelope["payload"]["text"] == "Позвонить маме"
    assert h.job().text == "Позвонить маме"


async def test_the_stt_window_is_measured_around_recognition_only() -> None:
    h = Harness()

    await h.run()

    voice = h.job().voice
    assert voice is not None
    assert voice.started_at is not None and voice.stored_at is not None
    assert voice.stt_started_at is not None and voice.stt_finished_at is not None
    assert voice.started_at < voice.stored_at < voice.stt_started_at
    assert voice.stt_started_at < voice.stt_finished_at


async def test_a_resumed_record_reads_the_object_and_never_asks_telegram() -> None:
    h = Harness(voice_job(stage=VoiceStage.STORED, s3_key=f"voice/{JOB_ID}"))

    result = await h.run()

    assert result.outcome is TranscribeOutcome.COMPLETED
    assert h.log.events == ["committed", "get", "stt", "committed"]
    assert h.telegram.calls == []


async def test_a_checkpointed_object_that_vanished_is_fetched_again() -> None:
    h = Harness(voice_job(stage=VoiceStage.STORED, s3_key=f"voice/{JOB_ID}"))
    h.audio.get_errors = [AudioMissing()]

    result = await h.run()

    assert result.outcome is TranscribeOutcome.COMPLETED
    assert h.log.events == [
        "committed",
        "get",
        "fetch",
        "put",
        "committed",
        "stt",
        "committed",
    ]


async def test_a_finished_or_unknown_job_is_only_acknowledged() -> None:
    h = Harness(job=None)

    result = await h.run()

    assert result.outcome is TranscribeOutcome.SKIPPED
    assert h.log.events == []


async def test_an_already_finished_voice_is_skipped_without_work() -> None:
    h = Harness(voice_job(stage=VoiceStage.DONE))

    result = await h.run()

    assert result.outcome is TranscribeOutcome.SKIPPED
    assert h.telegram.calls == [] and h.stt.heard == []


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (EmptyAudio(), "empty_audio"),
        (CorruptedAudio(), "corrupted_audio"),
        (AudioTooLong(), "too_long"),
        (AudioTooLarge(), "too_large"),
        (TranscriptionTimeout(), "timeout"),
    ],
)
async def test_a_recognition_error_fails_the_job_with_a_clear_notice(
    error: Exception, code: str
) -> None:
    h = Harness()
    h.stt.error = error

    result = await h.run()

    assert result.outcome is TranscribeOutcome.FAILED and result.error_code == code
    job = h.job()
    assert job.status is JobStatus.FAILED and job.voice is not None
    assert (job.voice.stage, job.voice.error_code) == (VoiceStage.FAILED, code)
    assert job.voice.stt_started_at is not None
    assert job.voice.stt_finished_at is not None
    assert h.world.outbox == {}  # no task
    notice = h.world.intents[IntentId.for_result(JOB_ID)]
    assert notice.body == VOICE_FAILURES[code]
    assert (notice.chat_id, notice.kind) == (CHAT, "transcription.result")


@pytest.mark.parametrize("code", sorted(VOICE_FAILURES))
def test_every_failure_has_an_understandable_russian_notice(code: str) -> None:
    body = VOICE_FAILURES[code]

    assert any("а" <= ch.lower() <= "я" for ch in body)
    assert "задача не создана" in body.lower()
    assert len(body) <= 300


async def test_only_a_timeout_asks_the_worker_to_restart() -> None:
    timed_out = Harness()
    timed_out.stt.error = TranscriptionTimeout()
    empty = Harness()
    empty.stt.error = EmptyAudio()

    assert (await timed_out.run()).restart_required is True
    assert (await empty.run()).restart_required is False
    assert (await Harness().run()).restart_required is False


@pytest.mark.parametrize("text", ["", "   \n"])
async def test_an_empty_transcript_is_an_empty_audio_error(text: str) -> None:
    h = Harness()
    h.stt.transcript = Transcript(text, "ru")

    result = await h.run()

    assert result.error_code == "empty_audio" and h.world.outbox == {}


async def test_a_transcript_longer_than_a_task_may_be_is_refused() -> None:
    h = Harness()
    h.stt.transcript = Transcript("я" * 4097, "ru")

    result = await h.run()

    assert result.error_code == "text_too_long" and h.world.outbox == {}


async def test_a_file_telegram_no_longer_serves_fails_before_any_storage() -> None:
    h = Harness()
    h.telegram.errors = [TelegramFileUnavailable()]

    result = await h.run()

    assert result.error_code == "file_unavailable"
    assert h.log.events == ["committed", "fetch", "committed"]
    assert h.sleeps == []  # a final error is not retried


async def test_a_download_above_the_limit_fails_as_too_large() -> None:
    h = Harness()
    h.telegram.errors = [AudioTooLarge()]

    result = await h.run()

    assert result.error_code == "too_large" and "put" not in h.log.events


async def test_transient_download_errors_are_retried_after_5_and_30_seconds() -> None:
    h = Harness()
    h.telegram.errors = [TransientIoError(), TransientIoError()]

    result = await h.run()

    assert result.outcome is TranscribeOutcome.COMPLETED
    assert h.sleeps == [5.0, 30.0]
    assert h.log.events.count("fetch") == 3


async def test_transient_download_errors_exhaust_into_an_unavailable_failure() -> None:
    h = Harness()
    h.telegram.errors = [TransientIoError() for _ in range(3)]

    result = await h.run()

    assert result.error_code == "unavailable"
    assert h.sleeps == [5.0, 30.0]
    assert h.log.events == ["committed", "fetch", "fetch", "fetch", "committed"]


async def test_transient_storage_errors_are_retried_and_then_fail_the_job() -> None:
    retried = Harness()
    retried.audio.put_errors = [TransientIoError()]
    exhausted = Harness()
    exhausted.audio.put_errors = [TransientIoError() for _ in range(3)]

    assert (await retried.run()).outcome is TranscribeOutcome.COMPLETED
    assert (await exhausted.run()).error_code == "unavailable"
    voice = exhausted.job().voice
    assert voice is not None and voice.s3_key is None  # nothing was checkpointed


async def test_a_resumed_read_that_keeps_failing_ends_as_unavailable() -> None:
    h = Harness(voice_job(stage=VoiceStage.STORED, s3_key=f"voice/{JOB_ID}"))
    h.audio.get_errors = [TransientIoError() for _ in range(3)]

    result = await h.run()

    assert result.error_code == "unavailable" and h.telegram.calls == []


async def test_a_record_that_keeps_crashing_is_failed_instead_of_looping() -> None:
    h = Harness(voice_job(attempts=3))

    result = await h.run()

    assert result.error_code == "failed"
    assert h.log.events == ["committed", "committed"]
    assert h.telegram.calls == []


async def test_a_storage_failure_on_begin_propagates_so_the_entry_stays_pending() -> (
    None
):
    h = Harness()
    h.begin_error = OSError("pg down")

    with pytest.raises(OSError):
        await h.run()

    assert h.log.events == []


async def test_an_unexpected_recognition_error_propagates_without_a_verdict() -> None:
    h = Harness()
    h.stt.error = RuntimeError("model crashed")

    with pytest.raises(RuntimeError):
        await h.run()

    assert h.job().status is JobStatus.ACCEPTED and h.world.outbox == {}


async def test_store_failure_during_timeout_raises_stuck_worker_error() -> None:
    h = Harness()
    h.stt.error = TranscriptionTimeout()
    h.fail_after_calls = 3  # begin and checkpoint pass, the failure record does not

    with pytest.raises(StuckWorkerError) as exc_info:
        await h.run()

    assert exc_info.value.restart_required is True


async def test_a_skipped_record_does_not_consume_cold_start() -> None:
    h = Harness(job=None)

    skipped = await h.run()
    assert skipped.outcome is TranscribeOutcome.SKIPPED

    h.world.jobs[JOB.id] = voice_job()
    done = await h.run()
    assert done.outcome is TranscribeOutcome.COMPLETED

    voice = h.job().voice
    assert voice is not None and voice.cold_start is True


async def test_only_the_first_record_of_a_process_is_marked_cold() -> None:
    first, second = voice_job(7), voice_job(8)
    h = Harness(first)
    h.world.jobs[second.id] = second

    await h.run(first.id.value)
    await h.run(second.id.value)

    assert h.job(first.id).voice is not None and h.job(second.id).voice is not None
    assert h.job(first.id).voice.cold_start is True  # type: ignore[union-attr]
    assert h.job(second.id).voice.cold_start is False  # type: ignore[union-attr]
