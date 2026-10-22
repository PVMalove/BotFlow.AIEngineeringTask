"""TaskCreated: one transaction records the result; its text goes to the author."""

import json
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from conftest import NOW, FakeAutomationUnitOfWork, World
from kernel_platform.consumer import InvalidMessage

from application.commands.record_task_result import (
    RecordTaskResultCommand,
    RecordTaskResultCommandHandler,
)
from domain.entities.job import Job
from domain.value_objects.intent_id import IntentId
from domain.value_objects.intent_status import IntentStatus
from domain.value_objects.job_id import JobId
from domain.value_objects.job_status import JobStatus
from infrastructure.messaging.task_result_consumer import TaskResultConsumer

AUTHOR = uuid4()
CHAT = 4_200_000_001
TASK = uuid4()
MESSAGE = str(uuid4())


def accepted_job(text: str | None = "Купить молоко", message_id: int = 7) -> Job:
    job = Job.accept_text(
        bot_id=123,
        chat_id=CHAT,
        message_id=message_id,
        author_id=AUTHOR,
        author_telegram_id=CHAT,
        author_name="Ann (@ann)",
        text=text or "x",
        message_date=NOW,
        received_at=NOW,
        now=NOW,
    )
    job.text = text
    job.pull_events()
    return job


class Harness:
    def __init__(self, job: Job | None = None) -> None:
        self.world = World()
        self.job = job or accepted_job()
        self.world.jobs[self.job.id] = self.job
        self.failure: Exception | None = None
        self.handler = RecordTaskResultCommandHandler(
            self._unit_of_work, clock=lambda: NOW + timedelta(seconds=5)
        )
        self.consumer = TaskResultConsumer(self.handler)

    @property
    def job_id(self) -> str:
        return str(self.job.id.value)

    def stored(self) -> Job:
        return self.world.jobs[self.job.id]

    def _unit_of_work(self) -> FakeAutomationUnitOfWork:
        if self.failure:
            raise self.failure
        return FakeAutomationUnitOfWork(self.world)


def created(job_id: str, **overrides: Any) -> bytes:
    payload: dict[str, Any] = {
        "job_id": job_id,
        "task_id": str(TASK),
        "source_key": "1:2:3",
    }
    payload.update(overrides)
    return json.dumps(
        {
            "envelope_version": 1,
            "message_id": MESSAGE,
            "type": "TaskCreated",
            "correlation_id": job_id,
            "occurred_at": "2026-01-01T12:00:00+00:00",
            "payload": payload,
        }
    ).encode()


async def test_a_result_completes_the_job_and_stores_one_intent_for_its_author() -> (
    None
):
    h = Harness()

    await h.consumer.handle(MESSAGE, "TaskCreated", created(h.job_id))

    job = h.stored()
    assert (job.status, job.task_id) == (JobStatus.COMPLETED, TASK)
    assert job.updated_at == NOW + timedelta(seconds=5)
    intent = h.world.intents[IntentId.for_result(job.id.value)]
    assert (intent.chat_id, intent.member_id, intent.kind) == (
        CHAT,
        AUTHOR,
        "task.result",
    )
    assert intent.access_version == 0 and intent.status is IntentStatus.PENDING
    assert intent.body == "Задача создана: Купить молоко"
    assert MESSAGE in h.world.inbox


async def test_a_long_text_is_cut_in_the_notification() -> None:
    h = Harness(accepted_job("я" * 300))

    await h.consumer.handle(MESSAGE, "TaskCreated", created(h.job_id))

    body = h.world.intents[IntentId.for_result(h.job.id.value)].body
    assert body is not None and len(body) <= len("Задача создана: ") + 101
    assert body.endswith("…")


async def test_a_nullable_job_text_produces_a_fallback_notification() -> None:
    h = Harness(accepted_job(None))

    await h.consumer.handle(MESSAGE, "TaskCreated", created(h.job_id))

    body = h.world.intents[IntentId.for_result(h.job.id.value)].body
    assert body == "Задача создана"


async def test_the_intent_id_depends_only_on_the_job() -> None:
    first, second = Harness(), Harness()

    await first.consumer.handle(MESSAGE, "TaskCreated", created(first.job_id))
    await second.consumer.handle(str(uuid4()), "TaskCreated", created(second.job_id))

    assert list(first.world.intents) == list(second.world.intents)


async def test_a_redelivered_result_reports_nothing_new() -> None:
    h = Harness()
    command = RecordTaskResultCommand(MESSAGE, h.job.id.value, TASK)
    await h.handler.execute(command)

    again = await h.handler.execute(command)

    assert again.is_ok and again.value is False
    assert len(h.world.intents) == 1


async def test_a_late_result_completes_a_job_that_failed_after_its_retries() -> None:
    h = Harness()
    h.stored().fail_delivery("exhausted", NOW)

    await h.consumer.handle(MESSAGE, "TaskCreated", created(h.job_id))

    job = h.stored()
    assert (job.status, job.task_id, job.last_error) == (
        JobStatus.COMPLETED,
        TASK,
        None,
    )
    assert len(h.world.intents) == 1


async def test_a_second_result_for_a_completed_job_stores_no_second_intent() -> None:
    h = Harness()
    await h.consumer.handle(MESSAGE, "TaskCreated", created(h.job_id))

    await h.consumer.handle(str(uuid4()), "TaskCreated", created(h.job_id))

    assert len(h.world.intents) == 1


async def test_a_result_for_an_unknown_job_can_never_work() -> None:
    h = Harness()
    ghost = str(JobId.from_source(9, 9, 9).value)

    with pytest.raises(InvalidMessage):
        await h.consumer.handle(MESSAGE, "TaskCreated", created(ghost))

    assert h.world.inbox == set()


@pytest.mark.parametrize(
    "message_id, type_, body",
    [
        (None, "TaskCreated", "valid"),
        (MESSAGE, "CreateTask", "valid"),
        (MESSAGE, "TaskCreated", b"nope"),
        (MESSAGE, "TaskCreated", "bad job id"),
        (MESSAGE, "TaskCreated", "bad task id"),
    ],
)
async def test_a_malformed_message_is_invalid(
    message_id: str | None, type_: str, body: bytes | str
) -> None:
    h = Harness()
    if body == "valid":
        body = created(h.job_id)
    elif body == "bad job id":
        body = created("x")
    elif body == "bad task id":
        body = created(h.job_id, task_id=7)
    assert isinstance(body, bytes)

    with pytest.raises(InvalidMessage):
        await h.consumer.handle(message_id, type_, body)

    assert h.stored().status is JobStatus.ACCEPTED


async def test_a_storage_failure_propagates_so_the_message_is_requeued() -> None:
    h = Harness()
    h.failure = OSError("pg down")

    with pytest.raises(OSError):
        await h.consumer.handle(MESSAGE, "TaskCreated", created(h.job_id))
