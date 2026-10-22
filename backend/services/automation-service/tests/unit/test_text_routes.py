"""Telegram routing contract for text, unknown commands, edits and other content."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from aiogram import Bot
from aiogram.types import Update
from kernel_domain.result import Result

from application.commands.accept_text import AcceptTextDto, AcceptTextMessage
from application.commands.handle_bot_command import HandleBotCommand
from application.ports import (
    Accepted,
    NewJob,
    OutboxMessage,
    TelegramMember,
)
from infrastructure.polling import TelegramPoller
from infrastructure.telegram import create_dispatcher

USER = 4_200_000_001
TOKEN = "123456:dummy-token-for-local-tests"


class Accept:
    def __init__(self) -> None:
        self.commands: list[AcceptTextDto] = []

    async def execute(self, command: AcceptTextDto) -> None:
        self.commands.append(command)


class Messages:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


class Identity:
    async def find_member(self, telegram_id: int) -> TelegramMember | None:
        return TelegramMember(USER, "member", "approved", 1, uuid4())

    async def issue_login_code(self, telegram_id: int) -> Result[str]:
        return Result[str].ok("A" * 32)

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
        "from": {
            "id": USER,
            "is_bot": False,
            "first_name": "Ann",
            "username": "ann",
        },
    }
    base.update(message)
    return Update.model_validate({"update_id": update_id, "message": base})


class Setup:
    def __init__(self) -> None:
        self.accept = Accept()
        self.messages = Messages()
        self.dispatcher = create_dispatcher(
            HandleBotCommand(Identity(), self.messages, Inbox()),
            self.accept,  # type: ignore[arg-type]
            self.messages,
        )

    async def feed(self, update: Update, **kwargs: Any) -> None:
        async with Bot(TOKEN) as bot:
            await self.dispatcher.feed_update(bot, update, **kwargs)


async def test_plain_private_text_is_handed_to_the_accept_use_case() -> None:
    setup = Setup()
    arrived = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    await setup.feed(message_update(text="Купить молоко"), received_at=arrived)

    (command,) = setup.accept.commands
    assert (command.bot_id, command.chat_id, command.message_id) == (123456, USER, 7)
    assert (command.telegram_id, command.text) == (USER, "Купить молоко")
    assert command.display_name == "Ann (@ann)"
    assert command.message_date == datetime.fromtimestamp(1_700_000_000, UTC)
    assert command.received_at == arrived
    assert setup.messages.sent == []  # the use case replies, not the route


async def test_an_unknown_command_gets_a_short_reply_and_no_job() -> None:
    setup = Setup()

    await setup.feed(
        message_update(
            text="/frobnicate now",
            entities=[{"type": "bot_command", "offset": 0, "length": 10}],
        )
    )

    assert setup.accept.commands == []
    assert len(setup.messages.sent) == 1
    assert "команда" in setup.messages.sent[0][1].lower()


@pytest.mark.parametrize(
    "content",
    [
        {"audio": {"file_id": "f", "file_unique_id": "u", "duration": 3}},
        {"photo": [{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}]},
        {"dice": {"emoji": "x", "value": 1}},
    ],
)
async def test_unsupported_content_gets_a_short_reply_and_no_job(
    content: dict[str, Any],
) -> None:
    setup = Setup()

    await setup.feed(message_update(**content))

    assert setup.accept.commands == []
    assert len(setup.messages.sent) == 1


async def test_an_edited_message_is_ignored_silently() -> None:
    setup = Setup()
    update = Update.model_validate(
        {
            "update_id": 43,
            "edited_message": {
                "message_id": 7,
                "date": 1_700_000_000,
                "edit_date": 1_700_000_100,
                "text": "changed",
                "chat": {"id": USER, "type": "private"},
                "from": {"id": USER, "is_bot": False, "first_name": "Ann"},
            },
        }
    )

    await setup.feed(update)

    assert setup.accept.commands == [] and setup.messages.sent == []


async def test_text_in_a_group_is_ignored() -> None:
    setup = Setup()

    await setup.feed(
        message_update(text="hello", chat={"id": -100, "type": "supergroup"})
    )

    assert setup.accept.commands == [] and setup.messages.sent == []


class FailingJobs:
    async def accept(self, job: NewJob, message: OutboxMessage) -> Accepted:
        raise OSError("pg down")

    async def mark_acked(self, job_id: UUID, at: datetime) -> None:
        raise AssertionError("no ACK without a stored job")


async def test_a_storage_failure_leaves_the_offset_on_the_unsaved_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = Messages()
    accept = AcceptTextMessage(Identity(), FailingJobs(), messages)
    dispatcher = create_dispatcher(
        HandleBotCommand(Identity(), messages, Inbox()),
        accept,
        messages,
    )
    offsets: list[int | None] = []
    updates = [message_update(update_id=50, text="Купить молоко")]

    async def get_updates(self: Bot, **kwargs: Any) -> list[Update]:
        offsets.append(kwargs.get("offset"))
        return updates if len(offsets) == 1 else []

    async def no_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(Bot, "get_updates", get_updates)
    async with Bot(TOKEN) as bot:
        poller = TelegramPoller(bot, dispatcher, sleep=no_sleep, log=lambda line: None)

        await poller.poll_once()
        await poller.poll_once()

    assert offsets == [None, None]  # update 50 is still unconfirmed
    assert messages.sent == []  # and no positive ACK was sent
