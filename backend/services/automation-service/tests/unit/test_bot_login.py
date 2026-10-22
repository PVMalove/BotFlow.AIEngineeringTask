"""Telegram login behavior at the command and external HTTP seams."""

from typing import Any

import httpx
import pytest

from application.commands.handle_bot_command import (
    HandleBotCommand,
    HandleBotCommandDto,
)
from application.ports import IdentityUnavailable, TelegramMember

ADMIN = 4_200_000_001
CODE = "A" * 32


class Identity:
    failure: Exception | None = None
    member: TelegramMember | None = TelegramMember(ADMIN, "admin", "approved")

    async def find_member(self, telegram_id: int) -> TelegramMember | None:
        if self.failure:
            raise self.failure
        return self.member

    async def issue_login_code(self, telegram_id: int) -> str:
        return CODE


class Messages:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


class Inbox:
    def __init__(self) -> None:
        self.seen: set[int] = set()

    async def claim(self, update_id: int) -> bool:
        if update_id in self.seen:
            return False
        self.seen.add(update_id)
        return True


async def command(sut: HandleBotCommand, text: str, **overrides: object) -> None:
    values: dict[str, Any] = dict(
        update_id=1, telegram_id=ADMIN, chat_id=ADMIN, text=text, private=True
    )
    values.update(overrides)
    await sut.execute(HandleBotCommandDto(**values))


async def test_login_sends_the_one_time_code_to_the_admin() -> None:
    messages = Messages()
    sut = HandleBotCommand(Identity(), messages, Inbox())

    await command(sut, "/login")

    assert messages.sent == [
        (ADMIN, f"Код входа: {CODE}\nДействует 5 минут, только один раз.")
    ]


async def test_start_explains_how_to_log_in_without_issuing_a_code() -> None:
    messages = Messages()
    sut = HandleBotCommand(Identity(), messages, Inbox())

    await command(sut, "/start")

    assert messages.sent == [(ADMIN, "Для входа в веб-панель отправьте /login.")]


@pytest.mark.parametrize("member", [None, TelegramMember(ADMIN, "admin", "blocked")])
async def test_unapproved_people_cannot_get_a_login_code(
    member: TelegramMember | None,
) -> None:
    identity = Identity()
    identity.member = member
    messages = Messages()
    sut = HandleBotCommand(identity, messages, Inbox())

    await command(sut, "/login")

    assert messages.sent == [
        (ADMIN, "Доступ не предоставлен. Обратитесь к администратору.")
    ]


async def test_duplicate_update_does_not_send_a_second_code() -> None:
    messages = Messages()
    sut = HandleBotCommand(Identity(), messages, Inbox())

    await command(sut, "/login")
    await command(sut, "/login")

    assert len(messages.sent) == 1


async def test_group_commands_do_not_disclose_a_code() -> None:
    messages = Messages()
    sut = HandleBotCommand(Identity(), messages, Inbox())

    await command(sut, "/login", private=False)

    assert messages.sent == []


async def test_unavailable_identity_gets_a_safe_retry_message() -> None:
    identity = Identity()
    identity.failure = IdentityUnavailable()
    messages = Messages()
    sut = HandleBotCommand(identity, messages, Inbox())

    await command(sut, "/login")

    assert messages.sent == [
        (ADMIN, "Вход временно недоступен. Повторите /login позже.")
    ]


async def test_http_gateway_uses_the_automation_key_for_member_lookup() -> None:
    from infrastructure.identity_gateway import HttpIdentityGateway

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/internal/v1/members/by-telegram/{ADMIN}"
        assert request.headers["X-Service-Key"] == "test-automation-key"
        return httpx.Response(
            200, json={"telegram_id": ADMIN, "role": "admin", "status": "approved"}
        )

    async with httpx.AsyncClient(
        base_url="http://identity", transport=httpx.MockTransport(respond)
    ) as client:
        sut = HttpIdentityGateway(client, "test-automation-key")

        member = await sut.find_member(ADMIN)

    assert member == TelegramMember(ADMIN, "admin", "approved")


@pytest.mark.parametrize("status", [401, 500, 503])
async def test_http_gateway_fails_closed_without_leaking_the_key(status: int) -> None:
    from infrastructure.identity_gateway import HttpIdentityGateway

    async with httpx.AsyncClient(
        base_url="http://identity",
        transport=httpx.MockTransport(lambda request: httpx.Response(status)),
    ) as client:
        sut = HttpIdentityGateway(client, "secret-key")

        with pytest.raises(IdentityUnavailable) as failure:
            await sut.issue_login_code(ADMIN)

    assert "secret-key" not in str(failure.value)


async def test_aiogram_update_reaches_the_login_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aiogram import Bot
    from aiogram.types import Update

    from infrastructure.telegram import TelegramMessenger, create_dispatcher

    sent: list[tuple[int, str]] = []

    async def send(bot: Bot, *, chat_id: int, text: str, **kwargs: Any) -> None:
        sent.append((chat_id, text))

    monkeypatch.setattr(Bot, "send_message", send)
    async with Bot("123456:dummy-token-for-local-tests") as bot:
        sut = create_dispatcher(
            HandleBotCommand(Identity(), TelegramMessenger(bot), Inbox())
        )
        update = Update.model_validate(
            {
                "update_id": 42,
                "message": {
                    "message_id": 1,
                    "date": 0,
                    "text": "/login",
                    "chat": {"id": ADMIN, "type": "private"},
                    "from": {"id": ADMIN, "is_bot": False, "first_name": "Admin"},
                    "entities": [{"type": "bot_command", "offset": 0, "length": 6}],
                },
            }
        )

        await sut.feed_update(bot, update)
        await sut.feed_update(bot, update)

    assert sent == [(ADMIN, f"Код входа: {CODE}\nДействует 5 минут, только один раз.")]
