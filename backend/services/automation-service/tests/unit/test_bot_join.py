"""/join and the /start hints: Identity alone decides who has access."""

from typing import Any

import httpx
import pytest

from application.commands.handle_bot_command import (
    HandleBotCommand,
    HandleBotCommandDto,
)
from application.ports import (
    IdentityUnavailable,
    JoinOutcome,
    LoginDenied,
    LoginRateLimited,
    TelegramMember,
)

USER = 7_000_000_001


class Identity:
    failure: Exception | None = None
    outcome = JoinOutcome.SUBMITTED
    member: TelegramMember | None = None

    def __init__(self) -> None:
        self.joins: list[tuple[int, str | None]] = []
        self.codes = 0

    async def find_member(self, telegram_id: int) -> TelegramMember | None:
        return self.member

    async def issue_login_code(self, telegram_id: int) -> str:
        self.codes += 1
        return "A" * 32

    async def submit_join_request(
        self, telegram_id: int, display_name: str | None
    ) -> JoinOutcome:
        if self.failure:
            raise self.failure
        self.joins.append((telegram_id, display_name))
        return self.outcome


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


async def send(sut: HandleBotCommand, text: str, **overrides: object) -> None:
    values: dict[str, Any] = dict(
        update_id=1,
        telegram_id=USER,
        chat_id=USER,
        text=text,
        private=True,
        display_name="Ann (@ann)",
    )
    values.update(overrides)
    await sut.execute(HandleBotCommandDto(**values))


def bot(identity: Identity, messages: Messages) -> HandleBotCommand:
    return HandleBotCommand(identity, messages, Inbox())


async def test_join_passes_the_applicant_to_identity_and_confirms() -> None:
    identity, messages = Identity(), Messages()

    await send(bot(identity, messages), "/join")

    assert identity.joins == [(USER, "Ann (@ann)")]
    assert messages.sent == [
        (USER, "Заявка отправлена. Администратор рассмотрит её, мы сообщим о решении.")
    ]


@pytest.mark.parametrize(
    ("outcome", "reply"),
    [
        (JoinOutcome.ALREADY_PENDING, "Заявка уже на рассмотрении."),
        (JoinOutcome.ALREADY_MEMBER, "Вы уже участник. Для входа отправьте /login."),
        (JoinOutcome.BLOCKED, "Доступ заблокирован. Обратитесь к администратору."),
    ],
)
async def test_join_replies_according_to_the_identity_outcome(
    outcome: JoinOutcome, reply: str
) -> None:
    identity, messages = Identity(), Messages()
    identity.outcome = outcome

    await send(bot(identity, messages), "/join")

    assert messages.sent == [(USER, reply)]


async def test_join_is_rate_limited_with_a_retry_hint() -> None:
    identity, messages = Identity(), Messages()
    identity.failure = LoginRateLimited()

    await send(bot(identity, messages), "/join")

    assert messages.sent == [
        (USER, "Слишком много запросов. Повторите /join через минуту.")
    ]


@pytest.mark.parametrize("failure", [IdentityUnavailable(), LoginDenied()])
async def test_join_with_unavailable_identity_gets_a_safe_retry_message(
    failure: Exception,
) -> None:
    identity, messages = Identity(), Messages()
    identity.failure = failure

    await send(bot(identity, messages), "/join")

    assert messages.sent == [
        (USER, "Заявки временно недоступны. Повторите /join позже.")
    ]


async def test_join_is_ignored_outside_private_chats_and_for_replayed_updates() -> None:
    identity, messages = Identity(), Messages()
    sut = bot(identity, messages)

    await send(sut, "/join", private=False)
    await send(sut, "/join", update_id=2)
    await send(sut, "/join", update_id=2)

    assert len(identity.joins) == 1
    assert len(messages.sent) == 1


@pytest.mark.parametrize(
    ("member", "hint"),
    [
        (None, "Чтобы запросить доступ, отправьте /join."),
        (
            TelegramMember(USER, "user", "rejected"),
            "Заявка отклонена. Подать её снова можно командой /join.",
        ),
        (TelegramMember(USER, "user", "pending"), "Заявка на рассмотрении."),
        (
            TelegramMember(USER, "user", "blocked"),
            "Доступ не предоставлен. Обратитесь к администратору.",
        ),
    ],
)
async def test_start_tells_non_members_how_to_ask_for_access(
    member: TelegramMember | None, hint: str
) -> None:
    identity, messages = Identity(), Messages()
    identity.member = member

    await send(bot(identity, messages), "/start")

    assert messages.sent == [(USER, hint)]
    assert identity.codes == 0


@pytest.mark.parametrize("member", [None, TelegramMember(USER, "user", "pending")])
async def test_a_pending_or_unknown_person_gets_no_login_code(
    member: TelegramMember | None,
) -> None:
    identity, messages = Identity(), Messages()
    identity.member = member

    await send(bot(identity, messages), "/login")

    assert identity.codes == 0
    assert messages.sent == [
        (USER, "Доступ не предоставлен. Обратитесь к администратору.")
    ]


async def test_http_gateway_posts_the_join_request_with_the_automation_key() -> None:
    from infrastructure.identity_gateway import HttpIdentityGateway

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/internal/v1/join-requests"
        assert request.headers["X-Service-Key"] == "test-automation-key"
        assert request.read() == b'{"telegram_id":7000000001,"display_name":"Ann"}'
        return httpx.Response(
            200,
            json={
                "outcome": "submitted",
                "member": {"telegram_id": USER, "role": "user", "status": "pending"},
            },
        )

    async with httpx.AsyncClient(
        base_url="http://identity", transport=httpx.MockTransport(respond)
    ) as client:
        sut = HttpIdentityGateway(client, "test-automation-key")

        outcome = await sut.submit_join_request(USER, "Ann")

    assert outcome is JoinOutcome.SUBMITTED


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (429, {}, LoginRateLimited),
        (403, {}, LoginDenied),
        (500, {}, IdentityUnavailable),
        (200, {"outcome": "approved-by-gateway"}, IdentityUnavailable),
        (200, {}, IdentityUnavailable),
    ],
)
async def test_http_gateway_fails_closed_on_join_errors(
    status: int, body: dict[str, Any], expected: type[Exception]
) -> None:
    from infrastructure.identity_gateway import HttpIdentityGateway

    async with httpx.AsyncClient(
        base_url="http://identity",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, json=body)
        ),
    ) as client:
        sut = HttpIdentityGateway(client, "secret-key")

        with pytest.raises(expected) as failure:
            await sut.submit_join_request(USER, None)

    assert "secret-key" not in str(failure.value)


async def test_aiogram_join_update_names_the_applicant_and_ignores_fsm_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aiogram import Bot
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.types import Update

    from infrastructure.telegram import TelegramMessenger, create_dispatcher

    sent: list[tuple[int, str]] = []

    async def send_message(bot: Bot, *, chat_id: int, text: str, **kwargs: Any) -> None:
        sent.append((chat_id, text))

    monkeypatch.setattr(Bot, "send_message", send_message)
    identity = Identity()
    async with Bot("123456:dummy-token-for-local-tests") as telegram:
        sut = create_dispatcher(
            HandleBotCommand(identity, TelegramMessenger(telegram), Inbox())
        )
        # A planted conversation state must not grant anything.
        await sut.fsm.storage.set_state(
            StorageKey(telegram.id, USER, USER), "approved:admin"
        )

        def update(update_id: int, text: str) -> Update:
            return Update.model_validate(
                {
                    "update_id": update_id,
                    "message": {
                        "message_id": 1,
                        "date": 0,
                        "text": text,
                        "chat": {"id": USER, "type": "private"},
                        "from": {
                            "id": USER,
                            "is_bot": False,
                            "first_name": "Ann",
                            "last_name": "Lee",
                            "username": "ann",
                        },
                        "entities": [
                            {"type": "bot_command", "offset": 0, "length": len(text)}
                        ],
                    },
                }
            )

        await sut.feed_update(telegram, update(1, "/join"))
        await sut.feed_update(telegram, update(2, "/login"))

    assert identity.joins == [(USER, "Ann Lee (@ann)")]
    assert identity.codes == 0
    assert sent[-1] == (USER, "Доступ не предоставлен. Обратитесь к администратору.")
