"""TranscribeVoice: every stage and error branch against in-memory fakes."""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from botflow_platform.retry import RetryPolicy

from application.commands.transcribe_voice import (
    FAILURES,
    TranscribeOutcome,
    TranscribeVoice,
)
from application.ports import (
    AudioMissing,
    AudioTooLarge,
    AudioTooLong,
    CorruptedAudio,
    EmptyAudio,
    OutboxMessage,
    StuckWorkerError,
    TelegramFileUnavailable,
    Transcript,
    TranscriptionTimeout,
    TransientIoError,
    VoiceWork,
)
from domain.jobs import outbox_id_for

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
JOB_ID = uuid4()
AUTHOR_ID = uuid4()
AUDIO = b"OggS-audio-bytes"


def work(**overrides: Any) -> VoiceWork:
    values: dict[str, Any] = dict(
        job_id=JOB_ID,
        bot_id=123,
        chat_id=4_200_000_001,
        message_id=7,
        author_id=AUTHOR_ID,
        author_telegram_id=4_200_000_001,
        author_name="Ann (@ann)",
        received_at=NOW,
        file_id="AwACfile",
        mime_type="audio/ogg",
        stage="queued",
        s3_key=None,
        attempts=1,
    )
    values.update(overrides)
    return VoiceWork(**values)


class Log:
    def __init__(self) -> None:
        self.events: list[str] = []


class Store:
    def __init__(self, log: Log) -> None:
        self.log = log
        self.work: VoiceWork | None = work()
        self.begin_error: Exception | None = None
        self.begins: list[tuple[UUID, datetime, bool]] = []
        self.checkpoints: list[tuple[UUID, str, int, str, datetime]] = []
        self.completed: list[dict[str, Any]] = []
        self.failed: list[dict[str, Any]] = []
        self.fail_error: Exception | None = None

    async def begin(
        self, job_id: UUID, now: datetime, cold_start: bool
    ) -> VoiceWork | None:
        self.log.events.append("begin")
        if self.begin_error:
            raise self.begin_error
        self.begins.append((job_id, now, cold_start))
        return self.work

    async def checkpoint(
        self, job_id: UUID, key: str, audio_bytes: int, sha256: str, now: datetime
    ) -> None:
        self.log.events.append("checkpoint")
        self.checkpoints.append((job_id, key, audio_bytes, sha256, now))

    async def complete(
        self,
        work: VoiceWork,
        transcript: Transcript,
        create_task: OutboxMessage,
        stt_started_at: datetime,
        stt_finished_at: datetime,
        now: datetime,
    ) -> bool:
        self.log.events.append("complete")
        self.completed.append(
            dict(
                work=work,
                transcript=transcript,
                create_task=create_task,
                stt_started_at=stt_started_at,
                stt_finished_at=stt_finished_at,
                now=now,
            )
        )
        return True

    async def fail(
        self,
        work: VoiceWork,
        error_code: str,
        body: str,
        now: datetime,
        stt_started_at: datetime | None = None,
        stt_finished_at: datetime | None = None,
    ) -> bool:
        self.log.events.append("fail")
        if self.fail_error:
            raise self.fail_error
        self.failed.append(
            dict(
                work=work,
                code=error_code,
                body=body,
                stt_started_at=stt_started_at,
                stt_finished_at=stt_finished_at,
            )
        )
        return True


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
    def __init__(self) -> None:
        self.log = Log()
        self.store = Store(self.log)
        self.telegram = Telegram(self.log)
        self.audio = Audio(self.log)
        self.stt = Stt(self.log)
        self.sleeps: list[float] = []
        self.ticks = 0

    def clock(self) -> datetime:
        self.ticks += 1
        return NOW + timedelta(seconds=self.ticks)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    @property
    def sut(self) -> TranscribeVoice:
        return TranscribeVoice(
            self.store,
            self.telegram,
            self.audio,
            self.stt,
            clock=self.clock,
            sleep=self.sleep,
            policy=RetryPolicy(jitter=0),
        )


async def test_the_stages_run_in_order_and_the_job_completes_once() -> None:
    h = Harness()

    result = await h.sut.execute(JOB_ID)

    assert result.outcome is TranscribeOutcome.COMPLETED and result.error_code is None
    assert h.log.events == ["begin", "fetch", "put", "checkpoint", "stt", "complete"]
    assert h.telegram.calls == ["AwACfile"]
    assert h.audio.puts == [(JOB_ID, AUDIO, "audio/ogg")]
    assert h.stt.heard == [AUDIO]
    assert h.store.failed == []


async def test_the_checkpoint_names_the_object_its_size_and_checksum() -> None:
    h = Harness()

    await h.sut.execute(JOB_ID)

    ((job_id, key, size, sha, _),) = h.store.checkpoints
    assert (job_id, key, size) == (JOB_ID, f"voice/{JOB_ID}", len(AUDIO))
    assert sha == hashlib.sha256(AUDIO).hexdigest()


async def test_the_transcript_becomes_the_create_task_of_the_text_path() -> None:
    h = Harness()

    await h.sut.execute(JOB_ID)

    (done,) = h.store.completed
    message: OutboxMessage = done["create_task"]
    assert done["transcript"] == Transcript("Купить молоко", "ru")
    assert message.id == outbox_id_for(JOB_ID)
    assert (message.type, message.routing_key) == ("CreateTask", "task.create")
    assert message.correlation_id == JOB_ID
    assert message.payload["payload"] == {
        "job_id": str(JOB_ID),
        "bot_id": 123,
        "chat_id": 4_200_000_001,
        "message_id": 7,
        "author_id": str(AUTHOR_ID),
        "author_telegram_id": 4_200_000_001,
        "author_name": "Ann (@ann)",
        "text": "Купить молоко",
        "source_type": "voice",
    }
    assert message.payload["occurred_at"] == NOW.isoformat()


async def test_the_transcript_is_trimmed_before_it_becomes_the_task_text() -> None:
    h = Harness()
    h.stt.transcript = Transcript("  Позвонить маме \n", "ru")

    await h.sut.execute(JOB_ID)

    (done,) = h.store.completed
    assert done["create_task"].payload["payload"]["text"] == "Позвонить маме"
    assert done["transcript"].text == "Позвонить маме"


async def test_the_stt_window_is_measured_around_recognition_only() -> None:
    h = Harness()

    await h.sut.execute(JOB_ID)

    (done,) = h.store.completed
    assert done["stt_started_at"] < done["stt_finished_at"] <= done["now"]
    ((_, begin_at, _),) = h.store.begins
    assert begin_at < h.store.checkpoints[0][4] < done["stt_started_at"]


async def test_a_resumed_record_reads_the_object_and_never_asks_telegram() -> None:
    h = Harness()
    h.store.work = work(stage="stored", s3_key=f"voice/{JOB_ID}")

    result = await h.sut.execute(JOB_ID)

    assert result.outcome is TranscribeOutcome.COMPLETED
    assert h.log.events == ["begin", "get", "stt", "complete"]
    assert h.telegram.calls == []


async def test_a_checkpointed_object_that_vanished_is_fetched_again() -> None:
    h = Harness()
    h.store.work = work(stage="stored", s3_key=f"voice/{JOB_ID}")
    h.audio.get_errors = [AudioMissing()]

    result = await h.sut.execute(JOB_ID)

    assert result.outcome is TranscribeOutcome.COMPLETED
    assert h.log.events == [
        "begin",
        "get",
        "fetch",
        "put",
        "checkpoint",
        "stt",
        "complete",
    ]


async def test_a_finished_or_unknown_job_is_only_acknowledged() -> None:
    h = Harness()
    h.store.work = None

    result = await h.sut.execute(JOB_ID)

    assert result.outcome is TranscribeOutcome.SKIPPED
    assert h.log.events == ["begin"]


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

    result = await h.sut.execute(JOB_ID)

    assert result.outcome is TranscribeOutcome.FAILED and result.error_code == code
    assert h.log.events[-1] == "fail" and "complete" not in h.log.events
    (failed,) = h.store.failed
    assert failed["code"] == code and failed["body"] == FAILURES[code]
    assert failed["stt_started_at"] is not None
    assert failed["stt_finished_at"] is not None


@pytest.mark.parametrize("code", sorted(FAILURES))
def test_every_failure_has_an_understandable_russian_notice(code: str) -> None:
    body = FAILURES[code]

    assert any("а" <= ch.lower() <= "я" for ch in body)
    assert "задача не создана" in body.lower()
    assert len(body) <= 300


async def test_only_a_timeout_asks_the_worker_to_restart() -> None:
    timed_out = Harness()
    timed_out.stt.error = TranscriptionTimeout()
    empty = Harness()
    empty.stt.error = EmptyAudio()

    assert (await timed_out.sut.execute(JOB_ID)).restart_required is True
    assert (await empty.sut.execute(JOB_ID)).restart_required is False
    assert (await Harness().sut.execute(JOB_ID)).restart_required is False


@pytest.mark.parametrize("text", ["", "   \n"])
async def test_an_empty_transcript_is_an_empty_audio_error(text: str) -> None:
    h = Harness()
    h.stt.transcript = Transcript(text, "ru")

    result = await h.sut.execute(JOB_ID)

    assert result.error_code == "empty_audio" and h.store.completed == []


async def test_a_transcript_longer_than_a_task_may_be_is_refused() -> None:
    h = Harness()
    h.stt.transcript = Transcript("я" * 4097, "ru")

    result = await h.sut.execute(JOB_ID)

    assert result.error_code == "text_too_long" and h.store.completed == []


async def test_a_file_telegram_no_longer_serves_fails_before_any_storage() -> None:
    h = Harness()
    h.telegram.errors = [TelegramFileUnavailable()]

    result = await h.sut.execute(JOB_ID)

    assert result.error_code == "file_unavailable"
    assert h.log.events == ["begin", "fetch", "fail"]
    assert h.sleeps == []  # a final error is not retried


async def test_a_download_above_the_limit_fails_as_too_large() -> None:
    h = Harness()
    h.telegram.errors = [AudioTooLarge()]

    result = await h.sut.execute(JOB_ID)

    assert result.error_code == "too_large" and "put" not in h.log.events


async def test_transient_download_errors_are_retried_after_5_and_30_seconds() -> None:
    h = Harness()
    h.telegram.errors = [TransientIoError(), TransientIoError()]

    result = await h.sut.execute(JOB_ID)

    assert result.outcome is TranscribeOutcome.COMPLETED
    assert h.sleeps == [5.0, 30.0]
    assert h.log.events.count("fetch") == 3


async def test_transient_download_errors_exhaust_into_an_unavailable_failure() -> None:
    h = Harness()
    h.telegram.errors = [TransientIoError() for _ in range(3)]

    result = await h.sut.execute(JOB_ID)

    assert result.error_code == "unavailable"
    assert h.sleeps == [5.0, 30.0]
    assert h.log.events == ["begin", "fetch", "fetch", "fetch", "fail"]


async def test_transient_storage_errors_are_retried_and_then_fail_the_job() -> None:
    retried = Harness()
    retried.audio.put_errors = [TransientIoError()]
    exhausted = Harness()
    exhausted.audio.put_errors = [TransientIoError() for _ in range(3)]

    assert (await retried.sut.execute(JOB_ID)).outcome is TranscribeOutcome.COMPLETED
    assert (await exhausted.sut.execute(JOB_ID)).error_code == "unavailable"
    assert "checkpoint" not in exhausted.log.events


async def test_a_resumed_read_that_keeps_failing_ends_as_unavailable() -> None:
    h = Harness()
    h.store.work = work(stage="stored", s3_key=f"voice/{JOB_ID}")
    h.audio.get_errors = [TransientIoError() for _ in range(3)]

    result = await h.sut.execute(JOB_ID)

    assert result.error_code == "unavailable" and h.telegram.calls == []


async def test_a_record_that_keeps_crashing_is_failed_instead_of_looping() -> None:
    h = Harness()
    h.store.work = work(attempts=4)

    result = await h.sut.execute(JOB_ID)

    assert result.error_code == "failed"
    assert h.log.events == ["begin", "fail"]


async def test_a_storage_failure_on_begin_propagates_so_the_entry_stays_pending() -> (
    None
):
    h = Harness()
    h.store.begin_error = OSError("pg down")

    with pytest.raises(OSError):
        await h.sut.execute(JOB_ID)

    assert h.log.events == ["begin"]


async def test_an_unexpected_recognition_error_propagates_without_a_verdict() -> None:
    h = Harness()
    h.stt.error = RuntimeError("model crashed")

    with pytest.raises(RuntimeError):
        await h.sut.execute(JOB_ID)

    assert h.store.failed == [] and h.store.completed == []


async def test_store_failure_during_timeout_raises_stuck_worker_error() -> None:
    h = Harness()
    h.stt.error = TranscriptionTimeout()
    h.store.fail_error = RuntimeError("db connection lost")

    with pytest.raises(StuckWorkerError) as exc_info:
        await h.sut.execute(JOB_ID)

    assert exc_info.value.restart_required is True


async def test_a_skipped_record_does_not_consume_cold_start() -> None:
    h = Harness()
    h.store.work = None
    sut = h.sut

    skipped = await sut.execute(JOB_ID)
    assert skipped.outcome is TranscribeOutcome.SKIPPED

    h.store.work = work()
    done = await sut.execute(JOB_ID)
    assert done.outcome is TranscribeOutcome.COMPLETED

    assert [cold for _, _, cold in h.store.begins] == [True, True]


async def test_only_the_first_record_of_a_process_is_marked_cold() -> None:
    h = Harness()
    sut = h.sut

    await sut.execute(JOB_ID)
    await sut.execute(JOB_ID)

    assert [cold for _, _, cold in h.store.begins] == [True, False]
