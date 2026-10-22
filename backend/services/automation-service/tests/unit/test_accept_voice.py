"""AcceptVoiceMessage: validate, save the job and outbox row, ACK strictly after."""

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from application.commands.accept_voice import AcceptVoiceDto, AcceptVoiceMessage
from application.ports import (
    Accepted,
    AcceptOutcome,
    IdentityUnavailable,
    NewVoiceJob,
    OutboxMessage,
    TelegramMember,
)
from domain.jobs import job_id_for, outbox_id_for

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
MEMBER_ID = uuid4()
USER = 4_200_000_001
MIB = 1024 * 1024


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
        self.accepted: list[tuple[NewVoiceJob, OutboxMessage]] = []
        self.acked: list[tuple[UUID, datetime]] = []
        self.outcome = AcceptOutcome.ACCEPTED
        self.already_acked = False
        self.failure: Exception | None = None

    async def accept_voice(self, job: NewVoiceJob, message: OutboxMessage) -> Accepted:
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
    def sut(self) -> AcceptVoiceMessage:
        return AcceptVoiceMessage(
            self.identity, self.jobs, self.messages, clock=lambda: self.clock_value
        )


def dto(**overrides: Any) -> AcceptVoiceDto:
    values: dict[str, Any] = dict(
        bot_id=123,
        chat_id=USER,
        message_id=7,
        telegram_id=USER,
        file_id="AwACAgIAAxkBAAI",
        file_unique_id="AgADuniq",
        duration_seconds=12,
        file_size_bytes=48_000,
        mime_type="audio/ogg",
        display_name="Ann (@ann)",
        message_date=NOW - timedelta(seconds=1),
        received_at=NOW,
    )
    values.update(overrides)
    return AcceptVoiceDto(**values)


async def test_the_positive_ack_is_sent_only_after_the_job_is_committed() -> None:
    h = Harness()

    await h.sut.execute(dto())

    assert h.log.events == ["committed", "send", "acked_at"]
    (chat_id, text) = h.messages.sent[0]
    assert chat_id == USER and "голос" in text.lower()


async def test_the_stored_job_carries_the_file_identity_and_latency_marks() -> None:
    h = Harness()

    await h.sut.execute(dto())

    ((job, message),) = h.jobs.accepted
    assert (job.bot_id, job.chat_id, job.message_id) == (123, USER, 7)
    assert (job.author_id, job.author_telegram_id) == (MEMBER_ID, USER)
    assert (job.file_id, job.file_unique_id) == ("AwACAgIAAxkBAAI", "AgADuniq")
    assert (job.duration_seconds, job.file_size_bytes) == (12, 48_000)
    assert job.mime_type == "audio/ogg"
    assert job.received_at == NOW
    assert job.message_date == NOW - timedelta(seconds=1)
    assert h.jobs.acked == [(job.id, NOW + timedelta(milliseconds=300))]
    assert message.correlation_id == job.id


async def test_the_outbox_row_asks_for_transcription_on_the_voice_route() -> None:
    h = Harness()

    await h.sut.execute(dto())

    ((job, message),) = h.jobs.accepted
    envelope = message.payload
    assert message.type == envelope["type"] == "TranscribeVoice"
    assert message.routing_key == "voice.transcribe"
    assert envelope["envelope_version"] == 1
    assert envelope["message_id"] == str(message.id)
    assert envelope["correlation_id"] == str(job.id)
    assert envelope["payload"]["job_id"] == str(job.id)


async def test_the_same_voice_message_always_gets_the_same_job_and_outbox_id() -> None:
    h = Harness()

    await h.sut.execute(dto())
    await h.sut.execute(dto())

    (first_job, first), (second_job, second) = h.jobs.accepted
    assert first_job.id == second_job.id and first.id == second.id


async def test_the_job_id_follows_the_source_key_like_a_text_job() -> None:
    h = Harness()

    await h.sut.execute(dto())

    assert h.jobs.accepted[0][0].id == job_id_for(123, USER, 7)


async def test_the_transcribe_row_leaves_the_create_task_outbox_id_free() -> None:
    h = Harness()

    await h.sut.execute(dto())

    ((job, message),) = h.jobs.accepted
    assert message.id != outbox_id_for(job.id)  # CreateTask of this job uses it later


async def test_a_missing_file_size_is_stored_as_unknown() -> None:
    h = Harness()

    await h.sut.execute(dto(file_size_bytes=None, mime_type=None))

    ((job, _),) = h.jobs.accepted
    assert job.file_size_bytes is None and job.mime_type is None


@pytest.mark.parametrize("duration", [60, 0])
async def test_a_voice_up_to_sixty_seconds_is_accepted(duration: int) -> None:
    h = Harness()

    await h.sut.execute(dto(duration_seconds=duration))

    assert len(h.jobs.accepted) == 1


async def test_a_voice_of_exactly_five_mebibytes_is_accepted() -> None:
    h = Harness()

    await h.sut.execute(dto(file_size_bytes=5 * MIB))

    assert len(h.jobs.accepted) == 1


@pytest.mark.parametrize(
    ("overrides", "word"),
    [
        ({"duration_seconds": 61}, "60"),
        ({"file_size_bytes": 5 * MIB + 1}, "5"),
    ],
)
async def test_an_invalid_voice_is_refused_before_anything_is_stored(
    overrides: dict[str, Any], word: str
) -> None:
    h = Harness()

    await h.sut.execute(dto(**overrides))

    assert h.jobs.accepted == [] and h.jobs.acked == []
    ((chat_id, text),) = h.messages.sent
    assert chat_id == USER and word in text


async def test_over_the_voice_limit_the_author_is_told_and_no_ack_is_recorded() -> None:
    h = Harness()
    h.jobs.outcome = AcceptOutcome.VOICE_LIMIT

    await h.sut.execute(dto())

    ((_, text),) = h.messages.sent
    assert "голос" in text.lower()
    assert h.jobs.acked == []


async def test_over_the_common_limit_the_author_gets_the_common_reply() -> None:
    h = Harness()
    h.jobs.outcome = AcceptOutcome.LIMIT

    await h.sut.execute(dto())

    assert h.messages.sent == [
        (USER, "Слишком много задач в обработке. Повторите отправку позже.")
    ]
    assert h.jobs.acked == []


async def test_a_duplicate_of_an_acknowledged_voice_sends_nothing() -> None:
    h = Harness()
    h.jobs.outcome = AcceptOutcome.DUPLICATE
    h.jobs.already_acked = True

    await h.sut.execute(dto())

    assert h.messages.sent == [] and h.jobs.acked == []


async def test_a_duplicate_that_was_never_acknowledged_is_acknowledged_now() -> None:
    h = Harness()
    h.jobs.outcome = AcceptOutcome.DUPLICATE

    await h.sut.execute(dto())

    assert len(h.messages.sent) == 1 and len(h.jobs.acked) == 1


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


async def test_nothing_persisted_holds_a_telegram_file_url_or_a_token() -> None:
    h = Harness()

    await h.sut.execute(dto())

    ((job, message),) = h.jobs.accepted
    stored = json.dumps([asdict(job), asdict(message)], default=str, ensure_ascii=False)
    assert "api.telegram.org" not in stored
    assert "/file/bot" not in stored
