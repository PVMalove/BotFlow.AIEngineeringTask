"""Telegram routing contract for voice messages."""

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from aiogram import Bot
from aiogram.types import Update

from application.commands.accept_text import AcceptTextDto
from application.commands.accept_voice import AcceptVoiceDto, AcceptVoiceMessage
from application.commands.handle_bot_command import HandleBotCommand
from application.ports import (
    Accepted,
    AcceptOutcome,
    NewVoiceJob,
    OutboxMessage,
    TelegramMember,
)
from infrastructure.telegram import UNSUPPORTED, create_dispatcher

USER = 4_200_000_001
TOKEN = "123456:dummy-token-for-local-tests"
VOICE = {
    "file_id": "AwACAgIAAxkBAAI",
    "file_unique_id": "AgADuniq",
    "duration": 12,
    "mime_type": "audio/ogg",
    "file_size": 48_000,
}


class Accept:
    async def execute(self, command: AcceptTextDto) -> None:
        raise AssertionError("a voice message is no text")


class AcceptVoice:
    def __init__(self) -> None:
        self.commands: list[AcceptVoiceDto] = []

    async def execute(self, command: AcceptVoiceDto) -> None:
        self.commands.append(command)


class Messages:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


class Identity:
    async def find_member(self, telegram_id: int) -> TelegramMember | None:
        return TelegramMember(USER, "member", "approved", 1, uuid4())

    async def issue_login_code(self, telegram_id: int) -> str:
        return "A" * 32

    async def submit_join_request(self, *args: Any) -> Any:
        raise AssertionError("not used")


class Inbox:
    async def claim(self, update_id: int) -> bool:
        return True


def message_update(update_id: int = 42, **message: Any) -> Update:
    base: dict[str, Any] = {
        "message_id": 7,
        "date": 1_700_000_000,
        "chat": {"id": USER, "type": "private"},
        "from": {"id": USER, "is_bot": False, "first_name": "Ann", "username": "ann"},
    }
    base.update(message)
    return Update.model_validate({"update_id": update_id, "message": base})


class Setup:
    def __init__(self) -> None:
        self.voice = AcceptVoice()
        self.messages = Messages()
        self.dispatcher = create_dispatcher(
            HandleBotCommand(Identity(), self.messages, Inbox()),
            Accept(),  # type: ignore[arg-type]
            self.messages,
            self.voice,  # type: ignore[arg-type]
        )

    async def feed(self, update: Update, **kwargs: Any) -> None:
        async with Bot(TOKEN) as bot:
            await self.dispatcher.feed_update(bot, update, **kwargs)


async def test_a_private_voice_is_handed_to_the_voice_use_case() -> None:
    setup = Setup()
    arrived = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    await setup.feed(message_update(voice=VOICE), received_at=arrived)

    (command,) = setup.voice.commands
    assert (command.bot_id, command.chat_id, command.message_id) == (123456, USER, 7)
    assert command.telegram_id == USER
    assert (command.file_id, command.file_unique_id) == (
        "AwACAgIAAxkBAAI",
        "AgADuniq",
    )
    assert (command.duration_seconds, command.file_size_bytes) == (12, 48_000)
    assert command.mime_type == "audio/ogg"
    assert command.display_name == "Ann (@ann)"
    assert command.message_date == datetime.fromtimestamp(1_700_000_000, UTC)
    assert command.received_at == arrived
    assert setup.messages.sent == []  # the use case replies, not the route


async def test_a_voice_without_a_size_is_handed_over_with_an_unknown_size() -> None:
    setup = Setup()
    voice = {k: v for k, v in VOICE.items() if k != "file_size"}

    await setup.feed(message_update(voice=voice))

    assert setup.voice.commands[0].file_size_bytes is None


async def test_a_voice_in_a_group_is_ignored() -> None:
    setup = Setup()

    await setup.feed(
        message_update(voice=VOICE, chat={"id": -100, "type": "supergroup"})
    )

    assert setup.voice.commands == [] and setup.messages.sent == []


async def test_an_edited_voice_is_ignored_silently() -> None:
    setup = Setup()
    update = Update.model_validate(
        {
            "update_id": 43,
            "edited_message": {
                "message_id": 7,
                "date": 1_700_000_000,
                "edit_date": 1_700_000_100,
                "voice": VOICE,
                "chat": {"id": USER, "type": "private"},
                "from": {"id": USER, "is_bot": False, "first_name": "Ann"},
            },
        }
    )

    await setup.feed(update)

    assert setup.voice.commands == [] and setup.messages.sent == []


async def test_other_media_is_still_unsupported_and_the_reply_names_voice() -> None:
    setup = Setup()

    await setup.feed(
        message_update(audio={"file_id": "f", "file_unique_id": "u", "duration": 3})
    )

    assert setup.voice.commands == []
    assert setup.messages.sent == [(USER, UNSUPPORTED)]
    assert "голос" in UNSUPPORTED.lower()


class RecordingJobs:
    def __init__(self) -> None:
        self.accepted: list[tuple[NewVoiceJob, OutboxMessage]] = []

    async def accept_voice(self, job: NewVoiceJob, message: OutboxMessage) -> Accepted:
        self.accepted.append((job, message))
        return Accepted(AcceptOutcome.ACCEPTED, job.id, False)

    async def mark_acked(self, job_id: UUID, at: datetime) -> None:
        return None


async def test_through_the_real_use_case_no_token_or_file_url_is_persisted() -> None:
    messages = Messages()
    jobs = RecordingJobs()
    dispatcher = create_dispatcher(
        HandleBotCommand(Identity(), messages, Inbox()),
        Accept(),  # type: ignore[arg-type]
        messages,
        AcceptVoiceMessage(Identity(), jobs, messages),
    )
    update = message_update(voice=VOICE)

    async with Bot(TOKEN) as bot:
        await dispatcher.feed_update(
            bot, update, received_at=datetime(2026, 1, 1, tzinfo=UTC)
        )

    ((job, message),) = jobs.accepted
    stored = json.dumps([asdict(job), asdict(message)], default=str)
    assert TOKEN not in stored and "api.telegram.org" not in stored
    assert job.message_date - datetime.fromtimestamp(1_700_000_000, UTC) == timedelta(0)
