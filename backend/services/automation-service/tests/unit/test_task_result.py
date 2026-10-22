"""TaskCreated: one transaction records the result; its text goes to the author."""

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import pytest
from botflow_platform.consumer import InvalidMessage

from application.commands.deliver_notification import DeliverNotification, Delivery
from application.commands.process_task_result import ProcessTaskResult
from application.ports import (
    JobSummary,
    ResultOutcome,
    TelegramMember,
)
from domain.notifications import IntentStatus, NotificationIntent

JOB = uuid4()
TASK = uuid4()
AUTHOR = uuid4()
CHAT = 4_200_000_001
MESSAGE = str(uuid4())


class Results:
    def __init__(self) -> None:
        self.recorded: list[tuple[str, UUID, UUID, NotificationIntent]] = []
        self.outcome = ResultOutcome.RECORDED
        self.job = JobSummary(JOB, CHAT, AUTHOR, "Купить молоко")
        self.failure: Exception | None = None

    async def record(
        self,
        message_id: str,
        job_id: UUID,
        task_id: UUID,
        intent_for: Callable[[JobSummary], NotificationIntent],
    ) -> ResultOutcome:
        if self.failure:
            raise self.failure
        self.recorded.append((message_id, job_id, task_id, intent_for(self.job)))
        return self.outcome


def created(**overrides: Any) -> bytes:
    payload: dict[str, Any] = {
        "job_id": str(JOB),
        "task_id": str(TASK),
        "source_key": "1:2:3",
    }
    payload.update(overrides)
    return json.dumps(
        {
            "envelope_version": 1,
            "message_id": MESSAGE,
            "type": "TaskCreated",
            "correlation_id": str(JOB),
            "occurred_at": "2026-01-01T12:00:00+00:00",
            "payload": payload,
        }
    ).encode()


async def test_a_result_completes_the_job_and_stores_one_intent_for_its_author() -> (
    None
):
    results = Results()

    stored = await ProcessTaskResult(results).execute(MESSAGE, "TaskCreated", created())

    assert stored is True
    ((message_id, job_id, task_id, intent),) = results.recorded
    assert (message_id, job_id, task_id) == (MESSAGE, JOB, TASK)
    assert (intent.chat_id, intent.member_id, intent.kind) == (
        CHAT,
        AUTHOR,
        "task.result",
    )
    assert intent.access_version == 0
    assert intent.body == "Задача создана: Купить молоко"


async def test_a_long_text_is_cut_in_the_notification() -> None:
    results = Results()
    results.job = JobSummary(JOB, CHAT, AUTHOR, "я" * 300)

    await ProcessTaskResult(results).execute(MESSAGE, "TaskCreated", created())

    body = results.recorded[0][3].body
    assert body is not None and len(body) <= len("Задача создана: ") + 101
    assert body.endswith("…")


async def test_the_intent_id_depends_only_on_the_job() -> None:
    first, second = Results(), Results()

    await ProcessTaskResult(first).execute(MESSAGE, "TaskCreated", created())
    await ProcessTaskResult(second).execute(str(uuid4()), "TaskCreated", created())

    assert first.recorded[0][3].id == second.recorded[0][3].id


async def test_a_redelivered_result_reports_nothing_new() -> None:
    results = Results()
    results.outcome = ResultOutcome.DUPLICATE

    assert (
        await ProcessTaskResult(results).execute(MESSAGE, "TaskCreated", created())
        is False
    )


async def test_a_result_for_an_unknown_job_can_never_work() -> None:
    results = Results()
    results.outcome = ResultOutcome.UNKNOWN_JOB

    with pytest.raises(InvalidMessage):
        await ProcessTaskResult(results).execute(MESSAGE, "TaskCreated", created())


@pytest.mark.parametrize(
    "message_id, type_, body",
    [
        (None, "TaskCreated", created()),
        (MESSAGE, "CreateTask", created()),
        (MESSAGE, "TaskCreated", b"nope"),
        (MESSAGE, "TaskCreated", created(job_id="x")),
        (MESSAGE, "TaskCreated", created(task_id=7)),
    ],
)
async def test_a_malformed_message_is_invalid(
    message_id: str | None, type_: str, body: bytes
) -> None:
    results = Results()

    with pytest.raises(InvalidMessage):
        await ProcessTaskResult(results).execute(message_id, type_, body)

    assert results.recorded == []


async def test_a_storage_failure_propagates_so_the_message_is_requeued() -> None:
    results = Results()
    results.failure = OSError("pg down")

    with pytest.raises(OSError):
        await ProcessTaskResult(results).execute(MESSAGE, "TaskCreated", created())


# ---- the notification carries the stored text -------------------------------


class Store:
    def __init__(self, intent: NotificationIntent) -> None:
        self.intent = intent
        self.status: IntentStatus | None = None

    async def get(self, intent_id: UUID) -> NotificationIntent | None:
        return self.intent

    async def settle(self, intent_id: UUID, status: IntentStatus) -> None:
        self.status = status

    async def record_failure(self, intent_id: UUID, max_attempts: int) -> IntentStatus:
        raise AssertionError("not used")


class Identity:
    async def find_member(self, telegram_id: int) -> TelegramMember | None:
        return TelegramMember(CHAT, "member", "approved", 1, AUTHOR)


class Messages:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


def result_intent(body: str | None) -> NotificationIntent:
    return NotificationIntent(
        id=uuid4(),
        chat_id=CHAT,
        member_id=AUTHOR,
        kind="task.result",
        access_version=0,
        status=IntentStatus.ENQUEUED,
        body=body,
    )


async def test_the_result_notification_sends_the_stored_body() -> None:
    store, messages = Store(result_intent("Задача создана: Купить молоко")), Messages()
    sut = DeliverNotification(store, Identity(), messages)  # type: ignore[arg-type]

    assert await sut.execute(uuid4()) is Delivery.DONE

    assert messages.sent == [(CHAT, "Задача создана: Купить молоко")]
    assert store.status is IntentStatus.SENT


async def test_a_result_notification_without_a_body_is_failed_not_sent() -> None:
    store, messages = Store(result_intent(None)), Messages()
    sut = DeliverNotification(store, Identity(), messages)  # type: ignore[arg-type]

    await sut.execute(uuid4())

    assert messages.sent == [] and store.status is IntentStatus.FAILED
