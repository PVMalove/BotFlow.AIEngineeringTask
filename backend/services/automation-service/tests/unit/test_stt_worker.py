"""The STT worker loop: model before readiness, strictly one record at a time."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from application.commands.transcribe_voice import (
    TranscribeOutcome,
    TranscribeResult,
    TranscribeVoiceCommand,
)
from application.ports import QueuedVoice, StuckWorkerError
from infrastructure.stt.runner import ReadinessMarker, SttConfig, run_worker

COMPLETED = TranscribeResult(TranscribeOutcome.COMPLETED)
TIMED_OUT = TranscribeResult(TranscribeOutcome.FAILED, "timeout")


class Queue:
    """Scripted stream; stops the worker once the script is used up."""

    def __init__(self, log: list[str], stop: asyncio.Event) -> None:
        self.log = log
        self.stop = stop
        self.idle: list[list[QueuedVoice]] = []
        self.fresh: list[list[QueuedVoice]] = []
        self.acked: list[str] = []
        self.read_error: Exception | None = None

    async def ensure_group(self) -> None:
        self.log.append("group")

    async def claim_idle(self, count: int) -> list[QueuedVoice]:
        self.log.append(f"claim_idle({count})")
        return self.idle.pop(0) if self.idle else []

    async def read(self, count: int, block_ms: int) -> list[QueuedVoice]:
        self.log.append(f"read({count})")
        if self.read_error:
            error, self.read_error = self.read_error, None
            raise error
        if not self.fresh:
            self.stop.set()
            return []
        return self.fresh.pop(0)

    async def ack(self, entry_id: str) -> None:
        self.log.append(f"ack({entry_id})")
        self.acked.append(entry_id)


class Handler:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.results: dict[UUID, TranscribeResult | Exception] = {}
        self.seen: list[UUID] = []
        self.on_execute: Callable[[], None] | None = None

    async def execute(self, command: TranscribeVoiceCommand) -> TranscribeResult:
        job_id = command.job_id
        self.log.append("execute")
        self.seen.append(job_id)
        if self.on_execute:
            self.on_execute()
        outcome = self.results.get(job_id, COMPLETED)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.log: list[str] = []
        self.stop = asyncio.Event()
        self.queue = Queue(self.log, self.stop)
        self.handler = Handler(self.log)
        self.marker = ReadinessMarker(tmp_path / "ready")
        self.exits = 0
        self.lines: list[str] = []
        self.sleeps: list[float] = []

    async def load(self) -> Handler:
        self.log.append("model loaded")
        self.log.append(f"marker before load: {self.marker.is_set()}")
        return self.handler

    def exit_process(self) -> None:
        self.log.append("exit")
        self.exits += 1

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    async def run(self) -> None:
        await run_worker(
            marker=self.marker,
            load=self.load,
            queue=self.queue,
            stop=self.stop,
            exit_process=self.exit_process,
            sleep=self.sleep,
            log=self.lines.append,
        )


async def test_the_model_is_loaded_before_the_group_and_the_marker(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    seen_marker: list[bool] = []

    original = h.queue.ensure_group

    async def group() -> None:
        seen_marker.append(h.marker.is_set())
        await original()

    h.queue.ensure_group = group  # type: ignore[method-assign]

    await h.run()

    assert h.log[:3] == ["model loaded", "marker before load: False", "group"]
    assert seen_marker == [False]  # not ready while the group is being created
    assert h.log[3] == "claim_idle(1)"


async def test_a_stale_marker_is_removed_before_the_model_loads(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    h.marker.set()  # left by the previous run of this container

    await h.run()

    assert "marker before load: False" in h.log


async def test_the_marker_exists_while_serving_and_goes_away_at_the_end(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    states: list[bool] = []
    job_id = uuid4()
    h.queue.fresh = [[QueuedVoice("1-0", job_id)]]
    h.handler.on_execute = lambda: states.append(h.marker.is_set())

    await h.run()

    assert states == [True] and not h.marker.is_set()


async def test_one_record_is_processed_and_acknowledged_before_the_next_is_read(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    first, second = uuid4(), uuid4()
    h.queue.fresh = [[QueuedVoice("1-0", first)], [QueuedVoice("2-0", second)]]

    await h.run()

    tail = h.log[h.log.index("group") + 1 :]
    assert tail == [
        "claim_idle(1)",
        "read(1)",
        "execute",
        "ack(1-0)",
        "claim_idle(1)",
        "read(1)",
        "execute",
        "ack(2-0)",
        "claim_idle(1)",
        "read(1)",
    ]
    assert h.handler.seen == [first, second]


async def test_a_stuck_record_is_taken_over_before_new_ones_are_read(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    stuck = uuid4()
    h.queue.idle = [[QueuedVoice("0-1", stuck)]]

    await h.run()

    assert h.handler.seen == [stuck]
    assert h.queue.acked == ["0-1"]
    assert "read(1)" in h.log and h.log.index("ack(0-1)") < h.log.index("read(1)")


async def test_an_entry_without_a_job_is_acknowledged_without_work(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    h.queue.fresh = [[QueuedVoice("1-0", None)]]

    await h.run()

    assert h.handler.seen == [] and h.queue.acked == ["1-0"]


async def test_a_failing_record_stays_pending_and_the_worker_goes_on(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    bad, good = uuid4(), uuid4()
    h.handler.results[bad] = OSError("pg down: password=secret")
    h.queue.fresh = [[QueuedVoice("1-0", bad)], [QueuedVoice("2-0", good)]]

    await h.run()

    assert h.queue.acked == ["2-0"]
    assert h.sleeps == [5.0]
    assert all("secret" not in line for line in h.lines)


async def test_a_stream_outage_pauses_the_worker_without_a_crash(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    h.queue.read_error = ConnectionError("redis password=secret")

    await h.run()

    assert h.sleeps == [5.0] and all("secret" not in line for line in h.lines)


async def test_after_a_timeout_the_record_is_acknowledged_and_the_process_replaced(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    slow, next_one = uuid4(), uuid4()
    h.handler.results[slow] = TIMED_OUT
    h.queue.fresh = [[QueuedVoice("1-0", slow)], [QueuedVoice("2-0", next_one)]]

    await h.run()

    assert h.queue.acked == ["1-0"]  # the final error is already committed
    assert h.exits == 1
    assert h.handler.seen == [slow]  # the next record is left for the new process
    assert h.log.index("ack(1-0)") < h.log.index("exit")
    assert not h.marker.is_set()


async def test_when_timeout_handling_throws_an_error_the_process_is_still_replaced(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    stuck, next_one = uuid4(), uuid4()
    h.handler.results[stuck] = StuckWorkerError("failed to record timeout in db")
    h.queue.fresh = [[QueuedVoice("1-0", stuck)], [QueuedVoice("2-0", next_one)]]

    await h.run()

    assert h.exits == 1
    assert h.handler.seen == [stuck]  # does not process next_one
    assert not h.marker.is_set()


async def test_a_stop_request_ends_the_loop_between_records(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.stop.set()

    await h.run()

    assert h.handler.seen == [] and not h.marker.is_set()


def test_the_marker_is_a_file(tmp_path: Path) -> None:
    marker = ReadinessMarker(tmp_path / "ready")

    assert not marker.is_set()
    marker.set()
    assert (tmp_path / "ready").is_file() and marker.is_set()
    marker.clear()
    marker.clear()  # clearing twice is fine
    assert not marker.is_set()


def test_the_defaults_follow_the_validated_settings() -> None:
    config = SttConfig.from_environ({})

    assert config.model == "turbo"
    assert config.models_dir == Path("/models")
    assert config.timeout_seconds == 120.0
    assert config.ready_file == Path("/tmp/whisper-ready")


def test_the_environment_overrides_the_defaults() -> None:
    config = SttConfig.from_environ(
        {
            "BOTFLOW_WHISPER_MODEL": "small",
            "BOTFLOW_WHISPER_MODELS_DIR": "/data/models",
            "BOTFLOW_STT_TIMEOUT_SECONDS": "90",
            "BOTFLOW_WHISPER_READY_FILE": "/run/ready",
        }
    )

    assert config.model == "small"
    assert config.models_dir == Path("/data/models")
    assert config.timeout_seconds == 90.0
    assert config.ready_file == Path("/run/ready")


@pytest.mark.parametrize("value", ["0", "-5", "soon"])
def test_a_nonsense_timeout_is_refused(value: str) -> None:
    with pytest.raises(ValueError):
        SttConfig.from_environ({"BOTFLOW_STT_TIMEOUT_SECONDS": value})
