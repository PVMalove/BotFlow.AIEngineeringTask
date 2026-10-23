"""AcceptVoiceCommandHandler: validate, save the job and outbox row, ACK after."""

import json
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

from application.commands.accept_voice import (
    AcceptVoiceCommand,
    AcceptVoiceCommandHandler,
)
from application.ports import IdentityUnavailable, TelegramMember
from domain.entities.job import Job
from domain.entities.voice_message import VoiceMessage
from domain.value_objects.job_id import JobId
from domain.value_objects.job_status import JobKind, JobStatus, VoiceStage
from infrastructure.messaging.envelopes import outbox_id_for

MEMBER_ID = uuid4()
USER = 4_200_000_001
MIB = 1024 * 1024


class Harness:
    def __init__(self) -> None:
        self.log = Log()
        self.world = World()
        self.identity = FakeIdentity(USER, MEMBER_ID)
        self.messages = FakeMessenger(self.log)
        self.storage_failure: Exception | None = None
        self.sut = AcceptVoiceCommandHandler(
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


def command(**overrides: Any) -> AcceptVoiceCommand:
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
    return AcceptVoiceCommand(**values)


async def test_the_positive_ack_is_sent_only_after_the_job_is_committed() -> None:
    h = Harness()

    await h.sut.execute(command())

    assert h.log.events == ["committed", "send", "committed"]
    (chat_id, text) = h.messages.sent[0]
    assert chat_id == USER and "голос" in text.lower()


async def test_the_stored_job_carries_the_file_identity_and_latency_marks() -> None:
    h = Harness()

    await h.sut.execute(command())

    job = h.only_job()
    assert (job.bot_id, job.chat_id, job.message_id) == (123, USER, 7)
    assert (job.author_id, job.author_telegram_id) == (MEMBER_ID, USER)
    assert (job.kind, job.status, job.text) == (JobKind.VOICE, JobStatus.ACCEPTED, None)
    voice = job.voice
    assert voice is not None
    assert (voice.file_id, voice.file_unique_id) == ("AwACAgIAAxkBAAI", "AgADuniq")
    assert (voice.duration_seconds, voice.file_size_bytes) == (12, 48_000)
    assert (voice.mime_type, voice.stage) == ("audio/ogg", VoiceStage.QUEUED)
    assert job.received_at == NOW
    assert job.message_date == NOW - timedelta(seconds=1)
    assert job.acked_at == NOW + timedelta(milliseconds=300)


async def test_the_outbox_row_asks_for_transcription() -> None:
    h = Harness()

    await h.sut.execute(command())

    job = h.only_job()
    ((message_id, (kind, envelope)),) = h.world.outbox.items()
    assert kind == envelope["type"] == "TranscribeVoice"
    assert envelope["envelope_version"] == 1
    assert envelope["message_id"] == str(message_id)
    assert envelope["correlation_id"] == str(job.id.value)
    assert envelope["payload"]["job_id"] == str(job.id.value)


async def test_the_same_voice_message_always_gets_the_same_job_and_outbox_id() -> None:
    h = Harness()

    await h.sut.execute(command())
    await h.sut.execute(command())

    assert len(h.world.outbox) == 1
    assert h.only_job().id == JobId.from_source(123, USER, 7)


async def test_the_transcribe_row_leaves_the_create_task_outbox_id_free() -> None:
    h = Harness()

    await h.sut.execute(command())

    job = h.only_job()
    assert outbox_id_for(job.id.value) not in h.world.outbox  # CreateTask comes later


async def test_a_missing_file_size_is_stored_as_unknown() -> None:
    h = Harness()

    await h.sut.execute(command(file_size_bytes=None, mime_type=None))

    voice = h.only_job().voice
    assert voice is not None
    assert voice.file_size_bytes is None and voice.mime_type is None


@pytest.mark.parametrize("duration", [60, 0])
async def test_a_voice_up_to_sixty_seconds_is_accepted(duration: int) -> None:
    h = Harness()

    await h.sut.execute(command(duration_seconds=duration))

    assert len(h.world.jobs) == 1


async def test_a_voice_of_exactly_five_mebibytes_is_accepted() -> None:
    h = Harness()

    await h.sut.execute(command(file_size_bytes=5 * MIB))

    assert len(h.world.jobs) == 1


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

    await h.sut.execute(command(**overrides))

    assert h.world.jobs == {}
    ((chat_id, text),) = h.messages.sent
    assert chat_id == USER and word in text


def fill(world: World, *, voice: bool, count: int) -> None:
    for number in range(count):
        common: dict[str, Any] = dict(
            bot_id=1,
            chat_id=USER,
            message_id=number,
            author_id=MEMBER_ID,
            author_telegram_id=USER,
            author_name="Ann",
            message_date=NOW,
            received_at=NOW,
            now=NOW,
        )
        job = (
            Job.accept_voice(VoiceMessage("f", "u", 1, None, None), **common)
            if voice
            else Job.accept_text(text="x", **common)
        )
        world.jobs[job.id] = job


async def test_over_the_voice_limit_the_author_is_told_and_nothing_is_stored() -> None:
    h = Harness()
    fill(h.world, voice=True, count=2)  # two unfinished voice jobs per author

    await h.sut.execute(command())

    ((_, text),) = h.messages.sent
    assert "голос" in text.lower()
    assert len(h.world.jobs) == 2 and h.world.outbox == {}


async def test_over_the_common_limit_the_author_gets_the_common_reply() -> None:
    h = Harness()
    fill(h.world, voice=False, count=10)

    await h.sut.execute(command())

    assert h.messages.sent == [
        (USER, "Слишком много задач в обработке. Повторите отправку позже.")
    ]
    assert len(h.world.jobs) == 10


async def test_a_duplicate_of_an_acknowledged_voice_sends_nothing() -> None:
    h = Harness()
    await h.sut.execute(command())
    h.messages.sent.clear()

    await h.sut.execute(command())

    assert h.messages.sent == []


async def test_a_duplicate_that_was_never_acknowledged_is_acknowledged_now() -> None:
    h = Harness()
    h.messages.failure = RuntimeError("telegram down")
    await h.sut.execute(command())
    h.messages.failure = None

    await h.sut.execute(command())

    assert len(h.messages.sent) == 1
    assert h.only_job().acked_at == NOW + timedelta(milliseconds=300)


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


async def test_nothing_persisted_holds_a_telegram_file_url_or_a_token() -> None:
    h = Harness()

    await h.sut.execute(command())

    stored = json.dumps(
        [vars(h.only_job().voice), list(h.world.outbox.values())],
        default=str,
        ensure_ascii=False,
    )
    assert "api.telegram.org" not in stored
    assert "/file/bot" not in stored
