"""AcceptTextMessage: save the job and its outbox row first, ACK strictly after."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from application.commands.accept_text import AcceptTextDto, AcceptTextMessage
from application.ports import (
    Accepted,
    AcceptOutcome,
    IdentityUnavailable,
    NewJob,
    OutboxMessage,
    TelegramMember,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
MEMBER_ID = uuid4()
USER = 4_200_000_001


class Log:
    def __init__(self) -> None:
        self.events: list[str] = []


class Identity:
    member: TelegramMember | None = TelegramMember(
        USER, "member", "approved", 1, MEMBER_ID
    )
    failure: Exception | None = None

    async def find_member(self, telegram_id: int) -> TelegramMember | None:
        if self.failure:
            raise self.failure
        return self.member


class Messages:
    def __init__(self, log: Log) -> None:
        self.log = log
        self.sent: list[tuple[int, str]] = []
        self.failure: Exception | None = None

    async def send(self, chat_id: int, text: str) -> None:
        self.log.events.append("send")
        if self.failure:
            raise self.failure
        self.sent.append((chat_id, text))


class Jobs:
    def __init__(self, log: Log) -> None:
        self.log = log
        self.accepted: list[tuple[NewJob, OutboxMessage]] = []
        self.acked: list[tuple[UUID, datetime]] = []
        self.outcome = AcceptOutcome.ACCEPTED
        self.already_acked = False
        self.failure: Exception | None = None

    async def accept(self, job: NewJob, message: OutboxMessage) -> Accepted:
        if self.failure:
            raise self.failure
        self.log.events.append("committed")
        self.accepted.append((job, message))
        return Accepted(self.outcome, job.id, self.already_acked)

    async def mark_acked(self, job_id: UUID, at: datetime) -> None:
        self.log.events.append("acked_at")
        self.acked.append((job_id, at))


class Harness:
    def __init__(self) -> None:
        self.log = Log()
        self.identity = Identity()
        self.messages = Messages(self.log)
        self.jobs = Jobs(self.log)
        self.clock_value = NOW + timedelta(milliseconds=300)

    @property
    def sut(self) -> AcceptTextMessage:
        return AcceptTextMessage(
            self.identity, self.jobs, self.messages, clock=lambda: self.clock_value
        )


def dto(**overrides: Any) -> AcceptTextDto:
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
    return AcceptTextDto(**values)


async def test_the_positive_ack_is_sent_only_after_the_job_is_committed() -> None:
    h = Harness()

    await h.sut.execute(dto())

    assert h.log.events == ["committed", "send", "acked_at"]
    assert h.messages.sent == [
        (USER, "Принято. Создаю задачу, сообщу, когда она будет готова.")
    ]


async def test_the_stored_job_carries_the_author_source_text_and_latency_marks() -> (
    None
):
    h = Harness()

    await h.sut.execute(dto())

    ((job, message),) = h.jobs.accepted
    assert (job.bot_id, job.chat_id, job.message_id) == (123, USER, 7)
    assert (job.author_id, job.author_telegram_id) == (MEMBER_ID, USER)
    assert (job.author_name, job.text) == ("Ann (@ann)", "Купить молоко")
    assert job.received_at == NOW
    assert job.message_date == NOW - timedelta(seconds=1)
    assert h.jobs.acked == [(job.id, NOW + timedelta(milliseconds=300))]
    assert message.correlation_id == job.id


async def test_the_create_task_envelope_names_the_job_and_the_source() -> None:
    h = Harness()

    await h.sut.execute(dto())

    ((job, message),) = h.jobs.accepted
    envelope = message.payload
    assert envelope["envelope_version"] == 1
    assert envelope["type"] == "CreateTask" == message.type
    assert envelope["message_id"] == str(message.id)
    assert envelope["correlation_id"] == str(job.id)
    assert message.routing_key == "task.create"
    assert envelope["payload"] == {
        "job_id": str(job.id),
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

    await h.sut.execute(dto())
    await h.sut.execute(dto())

    (first_job, first), (second_job, second) = h.jobs.accepted
    assert first_job.id == second_job.id and first.id == second.id


async def test_a_new_message_with_the_same_text_is_a_new_job() -> None:
    h = Harness()

    await h.sut.execute(dto(message_id=7))
    await h.sut.execute(dto(message_id=8))

    assert h.jobs.accepted[0][0].id != h.jobs.accepted[1][0].id


async def test_a_duplicate_of_an_acknowledged_job_sends_nothing() -> None:
    h = Harness()
    h.jobs.outcome = AcceptOutcome.DUPLICATE
    h.jobs.already_acked = True

    await h.sut.execute(dto())

    assert h.messages.sent == [] and h.jobs.acked == []


async def test_a_duplicate_that_was_never_acknowledged_is_acknowledged_now() -> None:
    h = Harness()
    h.jobs.outcome = AcceptOutcome.DUPLICATE
    h.jobs.already_acked = False

    await h.sut.execute(dto())

    assert len(h.messages.sent) == 1 and len(h.jobs.acked) == 1


async def test_over_the_limit_the_author_is_told_and_no_ack_is_recorded() -> None:
    h = Harness()
    h.jobs.outcome = AcceptOutcome.LIMIT

    await h.sut.execute(dto())

    assert h.messages.sent == [
        (USER, "Слишком много задач в обработке. Повторите отправку позже.")
    ]
    assert h.jobs.acked == []


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

    await h.sut.execute(dto())

    assert h.jobs.accepted == []
    assert h.messages.sent == [
        (USER, "Доступ не предоставлен. Обратитесь к администратору.")
    ]


async def test_an_unavailable_identity_gets_a_retry_message_and_no_job() -> None:
    h = Harness()
    h.identity.failure = IdentityUnavailable()

    await h.sut.execute(dto())

    assert h.jobs.accepted == []
    assert h.messages.sent == [
        (USER, "Сервис временно недоступен. Повторите отправку позже.")
    ]


async def test_a_member_without_an_id_is_treated_as_unavailable() -> None:
    h = Harness()
    h.identity.member = TelegramMember(USER, "member", "approved", 1, None)

    await h.sut.execute(dto())

    assert h.jobs.accepted == []
    assert len(h.messages.sent) == 1


async def test_a_storage_failure_propagates_and_nothing_is_acknowledged() -> None:
    h = Harness()
    h.jobs.failure = OSError("pg down")

    with pytest.raises(OSError):
        await h.sut.execute(dto())

    assert h.messages.sent == [] and h.jobs.acked == []


async def test_a_failed_reply_does_not_undo_the_saved_job() -> None:
    h = Harness()
    h.messages.failure = RuntimeError("telegram down")

    await h.sut.execute(dto())  # must not raise: the message is already stored

    assert len(h.jobs.accepted) == 1
    assert h.jobs.acked == []  # no acked_at without a delivered ACK
