"""QueueConsumer: ack after the handler, a jittered pause before a requeue."""

from typing import Any

from kernel_platform.consumer import InvalidMessage, QueueConsumer


class Message:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.message_id: str | None = "m1"
        self.type: str | None = "CreateTask"
        self.body = b"{}"

    async def ack(self) -> None:
        self.log.append("ack")

    async def nack(self, *, requeue: bool) -> None:
        self.log.append(f"nack requeue={requeue}")

    async def reject(self, *, requeue: bool) -> None:
        self.log.append(f"reject requeue={requeue}")


class Harness:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.failure: Exception | None = None
        self.sleeps: list[float] = []

    async def handler(
        self, message_id: str | None, type: str | None, body: bytes
    ) -> None:
        self.log.append(f"handle {message_id} {type}")
        if self.failure:
            raise self.failure

    async def sleep(self, seconds: float) -> None:
        self.log.append("sleep")
        self.sleeps.append(seconds)

    def consumer(self, rng: Any = lambda: 0.5) -> QueueConsumer:
        return QueueConsumer(
            connection=None,  # type: ignore[arg-type]
            declare=None,  # type: ignore[arg-type]
            handler=self.handler,
            pause_seconds=5.0,
            rng=rng,
            sleep=self.sleep,
        )


async def test_a_handled_message_is_acknowledged_after_the_handler_returned() -> None:
    harness = Harness()

    await harness.consumer().handle(Message(harness.log))  # type: ignore[arg-type]

    assert harness.log == ["handle m1 CreateTask", "ack"]


async def test_an_invalid_message_is_rejected_without_requeue() -> None:
    harness = Harness()
    harness.failure = InvalidMessage()

    await harness.consumer().handle(Message(harness.log))  # type: ignore[arg-type]

    assert harness.log == ["handle m1 CreateTask", "reject requeue=False"]
    assert harness.sleeps == []


async def test_a_temporary_failure_pauses_before_the_requeue_to_avoid_a_busy_loop() -> (
    None
):
    harness = Harness()
    harness.failure = OSError("pg down")

    await harness.consumer().handle(Message(harness.log))  # type: ignore[arg-type]

    assert harness.log == ["handle m1 CreateTask", "sleep", "nack requeue=True"]
    assert harness.sleeps == [5.0]  # rng 0.5 means no jitter shift


async def test_the_pause_is_jittered_within_twenty_percent() -> None:
    harness = Harness()
    harness.failure = OSError("pg down")

    await harness.consumer(rng=lambda: 1.0).handle(Message(harness.log))  # type: ignore[arg-type]

    assert harness.sleeps == [6.0]
