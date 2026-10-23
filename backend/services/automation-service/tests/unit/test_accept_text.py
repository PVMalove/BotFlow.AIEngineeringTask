"""AcceptTextCommandHandler: the job and outbox row first, the ACK strictly after."""

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from conftest import (
    NOW,
    FakeIdentity,
    FakeMessenger,
    Log,
    LoggedUnitOfWork,
    World,
)

from application.commands.accept_text import AcceptTextCommand, AcceptTextCommandHandler
from application.ports import IdentityUnavailable, TelegramMember
from domain.entities.job import Job
from domain.value_objects.job_id import JobId
from domain.value_objects.job_status import JobKind, JobStatus

MEMBER_ID = uuid4()
USER = 4_200_000_001


class Harness:
    def __init__(self) -> None:
        self.log = Log()
        self.world = World()
        self.identity = FakeIdentity(USER, MEMBER_ID)
        self.messages = FakeMessenger(self.log)
        self.storage_failure: Exception | None = None
        self.sut = AcceptTextCommandHandler(
            self.identity,
            self._unit_of_work,
            self.messages,
            clock=lambda: NOW + timedelta(milliseconds=300),
        )

    def only_job(self) -> Job:
        assert len(self.world.jobs) == 1
        return next(iter(self.world.jobs.values()))

    def _unit_of_work(self) -> LoggedUnitOfWork:
        if self.storage_failure:
            raise self.storage_failure
        return LoggedUnitOfWork(self.world, self.log)


def command(**overrides: Any) -> AcceptTextCommand:
    values: dict[str, Any] = dict(
        bot_id=123,
        chat_id=USER,
        message_id=7,
        telegram_id=USER,
        text="Купить молоко",
        display_name="Ann (@ann)",
        message_date=NOW - timedelta(seconds=1),
        received_at=NOW,
    )
    values.update(overrides)
    return AcceptTextCommand(**values)


async def test_the_positive_ack_is_sent_only_after_the_job_is_committed() -> None:
    h = Harness()

    await h.sut.execute(command())

    assert h.log.events == ["committed", "send", "committed"]
    assert h.messages.sent == [
        (USER, "Принято. Создаю задачу, сообщу, когда она будет готова.")
    ]


async def test_the_stored_job_carries_the_author_source_text_and_latency_marks() -> (
    None
):
    h = Harness()

    await h.sut.execute(command())

    job = h.only_job()
    assert (job.bot_id, job.chat_id, job.message_id) == (123, USER, 7)
    assert (job.author_id, job.author_telegram_id) == (MEMBER_ID, USER)
    assert (job.author_name, job.text) == ("Ann (@ann)", "Купить молоко")
    assert (job.kind, job.status) == (JobKind.TEXT, JobStatus.ACCEPTED)
    assert job.received_at == NOW
    assert job.message_date == NOW - timedelta(seconds=1)
    assert job.acked_at == NOW + timedelta(milliseconds=300)


async def test_the_create_task_envelope_names_the_job_and_the_source() -> None:
    h = Harness()

    await h.sut.execute(command())

    job = h.only_job()
    ((message_id, (kind, envelope)),) = h.world.outbox.items()
    assert envelope["envelope_version"] == 1
    assert envelope["type"] == "CreateTask" == kind
    assert envelope["message_id"] == str(message_id)
    assert envelope["correlation_id"] == str(job.id.value)
    assert envelope["payload"] == {
        "job_id": str(job.id.value),
        "bot_id": 123,
        "chat_id": USER,
        "message_id": 7,
        "author_id": str(MEMBER_ID),
        "author_telegram_id": USER,
        "author_name": "Ann (@ann)",
        "text": "Купить молоко",
    }


async def test_the_same_message_always_gets_the_same_job_and_outbox_identity() -> None:
    h = Harness()

    await h.sut.execute(command())
    await h.sut.execute(command())

    job = h.only_job()
    assert job.id == JobId.from_source(123, USER, 7)
    assert len(h.world.outbox) == 1


async def test_a_new_message_with_the_same_text_is_a_new_job() -> None:
    h = Harness()

    await h.sut.execute(command(message_id=7))
    await h.sut.execute(command(message_id=8))

    assert len(h.world.jobs) == 2 and len(h.world.outbox) == 2


async def test_a_duplicate_of_an_acknowledged_job_sends_nothing() -> None:
    h = Harness()
    await h.sut.execute(command())
    h.messages.sent.clear()

    await h.sut.execute(command())

    assert h.messages.sent == []


async def test_a_duplicate_that_was_never_acknowledged_is_acknowledged_now() -> None:
    h = Harness()
    h.messages.failure = RuntimeError("telegram down")
    await h.sut.execute(command())
    assert h.only_job().acked_at is None
    h.messages.failure = None

    await h.sut.execute(command())

    assert len(h.messages.sent) == 1
    assert h.only_job().acked_at == NOW + timedelta(milliseconds=300)
    assert len(h.world.outbox) == 1  # the redelivery stored nothing new


async def test_over_the_limit_the_author_is_told_and_nothing_is_stored() -> None:
    h = Harness()
    for number in range(10):
        job = Job.accept_text(
            bot_id=1,
            chat_id=USER,
            message_id=number,
            author_id=MEMBER_ID,
            author_telegram_id=USER,
            author_name="Ann",
            text="x",
            message_date=NOW,
            received_at=NOW,
            now=NOW,
        )
        h.world.jobs[job.id] = job

    await h.sut.execute(command())

    assert h.messages.sent == [
        (USER, "Слишком много задач в обработке. Повторите отправку позже.")
    ]
    assert len(h.world.jobs) == 10 and h.world.outbox == {}


@pytest.mark.parametrize(
    "member",
    [
        None,
        TelegramMember(USER, "member", "blocked", 2, MEMBER_ID),
        TelegramMember(USER, "member", "pending", 1, MEMBER_ID),
    ],
)
async def test_a_person_without_access_gets_a_refusal_and_no_job(
    member: TelegramMember | None,
) -> None:
    h = Harness()
    h.identity.member = member

    await h.sut.execute(command())

    assert h.world.jobs == {}
    assert h.messages.sent == [
        (USER, "Доступ не предоставлен. Обратитесь к администратору.")
    ]


async def test_an_unavailable_identity_gets_a_retry_message_and_no_job() -> None:
    h = Harness()
    h.identity.failure = IdentityUnavailable()

    await h.sut.execute(command())

    assert h.world.jobs == {}
    assert h.messages.sent == [
        (USER, "Сервис временно недоступен. Повторите отправку позже.")
    ]


async def test_a_member_without_an_id_is_treated_as_unavailable() -> None:
    h = Harness()
    h.identity.member = TelegramMember(USER, "member", "approved", 1, None)

    await h.sut.execute(command())

    assert h.world.jobs == {}
    assert len(h.messages.sent) == 1


async def test_a_storage_failure_propagates_and_nothing_is_acknowledged() -> None:
    h = Harness()
    h.storage_failure = OSError("pg down")

    with pytest.raises(OSError):
        await h.sut.execute(command())

    assert h.messages.sent == [] and h.world.jobs == {}


async def test_a_failed_reply_does_not_undo_the_saved_job() -> None:
    h = Harness()
    h.messages.failure = RuntimeError("telegram down")

    await h.sut.execute(command())  # must not raise: the message is already stored

    assert h.only_job().acked_at is None  # no acked_at without a delivered ACK
