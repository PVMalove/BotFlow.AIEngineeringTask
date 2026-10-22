"""Decision notifications: inbox, intents, stream hand-off and stale suppression."""

import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from kernel_domain.result import Result
from kernel_platform.consumer import InvalidMessage

from application.commands.deliver_notification import (
    MAX_ATTEMPTS,
    DeliverNotification,
    Delivery,
    RunNotifier,
)
from application.commands.relay_intents import RelayIntents
from application.commands.store_member_decision import (
    StoreMemberDecisionCommand,
    StoreMemberDecisionCommandHandler,
)
from application.ports import IdentityUnavailable, QueuedIntent, TelegramMember
from domain.notifications import (
    IntentStatus,
    NotificationIntent,
    UnknownNotificationKind,
    Verdict,
    delivery_verdict,
)
from domain.value_objects.join_outcome import JoinOutcome
from infrastructure.messaging.identity_event_consumer import IdentityEventConsumer

USER = 7_000_000_001
MEMBER_ID = uuid4()
EVENT_ID = uuid4()


# ---- the delivery rule -----------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "intent_version", "status", "version", "expected"),
    [
        ("access.approved", 2, "approved", 2, Verdict.SEND),
        ("access.rejected", 2, "rejected", 2, Verdict.SEND),
        ("access.unblocked", 3, "approved", 3, Verdict.SEND),
        ("access.approved", 2, "blocked", 3, Verdict.STALE),
        ("access.rejected", 2, "pending", 3, Verdict.STALE),
        ("access.approved", 2, None, 0, Verdict.STALE),
        ("task.result", 1, "approved", 9, Verdict.SEND),
        ("task.result", 1, "blocked", 9, Verdict.BLOCKED),
        ("transcription.result", 1, "blocked", 9, Verdict.BLOCKED),
        ("task.result", 1, None, 0, Verdict.BLOCKED),
    ],
)
def test_delivery_verdict(
    kind: str, intent_version: int, status: str | None, version: int, expected: Verdict
) -> None:
    assert delivery_verdict(kind, intent_version, status, version) is expected


def test_a_kind_without_a_rule_is_never_sent_by_default() -> None:
    with pytest.raises(UnknownNotificationKind):
        delivery_verdict("marketing.blast", 1, "approved", 1)


# ---- doubles ---------------------------------------------------------------


class Store:
    def __init__(self) -> None:
        self.intents: dict[UUID, NotificationIntent] = {}
        self.messages: set[str] = set()

    async def store_event(self, message_id: str, intent: NotificationIntent) -> bool:
        if message_id in self.messages:
            return False
        self.messages.add(message_id)
        self.intents[intent.id] = intent
        return True

    async def pending(self, limit: int) -> list[UUID]:
        return [
            i.id for i in self.intents.values() if i.status is IntentStatus.PENDING
        ][:limit]

    async def mark_enqueued(self, intent_id: UUID) -> None:
        self._set(intent_id, IntentStatus.ENQUEUED)

    async def get(self, intent_id: UUID) -> NotificationIntent | None:
        return self.intents.get(intent_id)

    async def settle(self, intent_id: UUID, status: IntentStatus) -> None:
        self._set(intent_id, status)

    async def record_failure(self, intent_id: UUID, max_attempts: int) -> IntentStatus:
        intent = self.intents[intent_id]
        attempts = intent.attempts + 1
        status = IntentStatus.FAILED if attempts >= max_attempts else intent.status
        self.intents[intent_id] = replace(intent, attempts=attempts, status=status)
        return status

    def _set(self, intent_id: UUID, status: IntentStatus) -> None:
        self.intents[intent_id] = replace(self.intents[intent_id], status=status)


def event(**overrides: object) -> bytes:
    payload: dict[str, object] = {
        "member_id": str(MEMBER_ID),
        "telegram_id": USER,
        "decision": "approved",
        "access_version": 2,
        "decided_at": "2026-01-01T12:00:00+00:00",
    }
    payload.update(overrides)
    return json.dumps(payload).encode()


# ---- consumer --------------------------------------------------------------


def consumer(store: "Store") -> IdentityEventConsumer:
    return IdentityEventConsumer(StoreMemberDecisionCommandHandler(store))


async def test_a_redelivered_event_reports_nothing_new() -> None:
    store = Store()
    handler = StoreMemberDecisionCommandHandler(store)
    command = StoreMemberDecisionCommand(EVENT_ID, MEMBER_ID, USER, "approved", 2)

    first = await handler.execute(command)
    again = await handler.execute(command)

    assert first.value is True and again.value is False


async def test_an_event_becomes_one_intent_for_the_applicants_private_chat() -> None:
    store = Store()

    await consumer(store).handle(str(EVENT_ID), "member.access-decided", event())

    assert store.intents == {
        EVENT_ID: NotificationIntent(
            id=EVENT_ID,
            chat_id=USER,
            member_id=MEMBER_ID,
            kind="access.approved",
            access_version=2,
        )
    }


async def test_a_redelivered_event_stores_nothing_new() -> None:
    store = Store()
    sut = consumer(store)
    await sut.handle(str(EVENT_ID), "member.access-decided", event())
    store.intents[EVENT_ID] = replace(store.intents[EVENT_ID], status=IntentStatus.SENT)

    await sut.handle(str(EVENT_ID), "member.access-decided", event())

    assert len(store.intents) == 1
    assert store.intents[EVENT_ID].status is IntentStatus.SENT


@pytest.mark.parametrize(
    ("message_id", "kind", "body"),
    [
        (None, "member.access-decided", event()),
        ("not-a-uuid", "member.access-decided", event()),
        (str(EVENT_ID), "member.something-else", event()),
        (str(EVENT_ID), "member.access-decided", b"not json"),
        (str(EVENT_ID), "member.access-decided", b"[]"),
        (str(EVENT_ID), "member.access-decided", event(decision="blocked")),
        (str(EVENT_ID), "member.access-decided", event(telegram_id=0)),
        (str(EVENT_ID), "member.access-decided", event(access_version="2")),
        (str(EVENT_ID), "member.access-decided", event(member_id="nope")),
    ],
)
async def test_an_event_that_can_never_be_valid_is_refused(
    message_id: str | None, kind: str, body: bytes
) -> None:
    store = Store()

    with pytest.raises(InvalidMessage):
        await consumer(store).handle(message_id, kind, body)

    assert store.intents == {} and store.messages == set()


# ---- stream hand-off -------------------------------------------------------


class Queue:
    def __init__(self) -> None:
        self.added: list[UUID] = []
        self.failure: Exception | None = None
        self.idle: list[QueuedIntent] = []
        self.new: list[QueuedIntent] = []
        self.acked: list[str] = []

    async def add(self, intent_id: UUID) -> None:
        if self.failure:
            raise self.failure
        self.added.append(intent_id)

    async def claim_idle(self, count: int) -> list[QueuedIntent]:
        taken, self.idle = self.idle, []
        return taken

    async def read(self, count: int, block_ms: int) -> list[QueuedIntent]:
        taken, self.new = self.new, []
        return taken

    async def ack(self, entry_id: str) -> None:
        self.acked.append(entry_id)


def intent(
    kind: str = "access.approved", version: int = 2, **kw: object
) -> NotificationIntent:
    return NotificationIntent(
        id=uuid4(),
        chat_id=USER,
        member_id=MEMBER_ID,
        kind=kind,
        access_version=version,
        **kw,  # type: ignore[arg-type]
    )


async def test_pending_intents_go_to_the_stream_once_and_are_marked() -> None:
    store, queue = Store(), Queue()
    first, second = intent(), intent()
    store.intents = {first.id: first, second.id: second}
    sut = RelayIntents(store, queue)

    assert await sut.execute() == 2
    assert await sut.execute() == 0

    assert sorted(queue.added) == sorted([first.id, second.id])
    assert {i.status for i in store.intents.values()} == {IntentStatus.ENQUEUED}


async def test_an_intent_stays_pending_when_the_stream_is_down() -> None:
    store, queue = Store(), Queue()
    waiting = intent()
    store.intents = {waiting.id: waiting}
    queue.failure = ConnectionError("redis down")

    with pytest.raises(ConnectionError):
        await RelayIntents(store, queue).execute()

    assert store.intents[waiting.id].status is IntentStatus.PENDING


# ---- delivery --------------------------------------------------------------


class Identity:
    def __init__(self, member: TelegramMember | None) -> None:
        self.member = member
        self.failure: Exception | None = None

    async def find_member(self, telegram_id: int) -> TelegramMember | None:
        if self.failure:
            raise self.failure
        return self.member

    async def issue_login_code(self, telegram_id: int) -> Result[str]:
        raise AssertionError("notifications never issue codes")

    async def submit_join_request(
        self, telegram_id: int, display_name: str | None
    ) -> Result[JoinOutcome]:
        raise AssertionError("notifications never create requests")


class Messages:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.failure: Exception | None = None

    async def send(self, chat_id: int, text: str) -> None:
        if self.failure:
            raise self.failure
        self.sent.append((chat_id, text))


def deliver(
    store: Store, member: TelegramMember | None, messages: Messages | None = None
) -> tuple[DeliverNotification, Identity, Messages]:
    identity, out = Identity(member), messages or Messages()
    return DeliverNotification(store, identity, out), identity, out


def member(status: str = "approved", version: int = 2) -> TelegramMember:
    return TelegramMember(USER, "user", status, version)


async def queued(store: Store, new: NotificationIntent) -> UUID:
    store.intents[new.id] = replace(new, status=IntentStatus.ENQUEUED)
    return new.id


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        (
            "access.approved",
            "Заявка одобрена. Для входа в веб-панель отправьте /login.",
        ),
        ("access.rejected", "Заявка отклонена. Подать её снова можно командой /join."),
        (
            "access.unblocked",
            "Доступ восстановлен. Для входа в веб-панель отправьте /login.",
        ),
    ],
)
async def test_a_current_decision_is_sent_once_to_the_applicant(
    kind: str, text: str
) -> None:
    store = Store()
    intent_id = await queued(store, intent(kind))
    sut, _, messages = deliver(store, member())

    assert await sut.execute(intent_id) is Delivery.DONE
    assert await sut.execute(intent_id) is Delivery.DONE  # duplicate stream entry

    assert messages.sent == [(USER, text)]
    assert store.intents[intent_id].status is IntentStatus.SENT


async def test_a_decision_older_than_identitys_access_version_is_never_sent() -> None:
    store = Store()
    intent_id = await queued(store, intent("access.approved", version=2))
    sut, _, messages = deliver(store, member("blocked", version=3))

    assert await sut.execute(intent_id) is Delivery.DONE

    assert messages.sent == []
    assert store.intents[intent_id].status is IntentStatus.SUPPRESSED_STALE


async def test_the_final_notification_to_a_blocked_author_is_suppressed() -> None:
    store = Store()
    intent_id = await queued(store, intent("task.result", version=1))
    sut, _, messages = deliver(store, member("blocked", version=3))

    assert await sut.execute(intent_id) is Delivery.DONE

    assert messages.sent == []
    assert store.intents[intent_id].status is IntentStatus.SUPPRESSED_BLOCKED


async def test_an_unavailable_identity_is_retried_then_marked_failed() -> None:
    store = Store()
    intent_id = await queued(store, intent())
    sut, identity, messages = deliver(store, member())
    identity.failure = IdentityUnavailable()

    results = [await sut.execute(intent_id) for _ in range(MAX_ATTEMPTS)]

    assert results == [Delivery.RETRY] * (MAX_ATTEMPTS - 1) + [Delivery.DONE]
    assert store.intents[intent_id].status is IntentStatus.FAILED
    assert messages.sent == []


async def test_a_telegram_failure_is_retried_and_a_later_success_is_sent() -> None:
    store = Store()
    intent_id = await queued(store, intent())
    messages = Messages()
    messages.failure = ConnectionError("telegram down")
    sut, _, _ = deliver(store, member(), messages)

    assert await sut.execute(intent_id) is Delivery.RETRY
    messages.failure = None
    assert await sut.execute(intent_id) is Delivery.DONE

    assert len(messages.sent) == 1
    assert store.intents[intent_id].status is IntentStatus.SENT


async def test_unknown_intents_and_kinds_are_settled_without_sending() -> None:
    store = Store()
    odd = await queued(store, intent("marketing.blast"))
    sut, _, messages = deliver(store, member())

    assert await sut.execute(uuid4()) is Delivery.DONE
    assert await sut.execute(odd) is Delivery.DONE

    assert messages.sent == []
    assert store.intents[odd].status is IntentStatus.FAILED


async def test_the_notifier_takes_stuck_entries_first_and_acks_only_settled_ones() -> (
    None
):
    store, queue = Store(), Queue()
    settled = await queued(store, intent())
    retried = await queued(store, intent())
    messages = Messages()
    sut, identity, _ = deliver(store, member(), messages)

    class Flaky(DeliverNotification):
        async def execute(self, intent_id: UUID) -> Delivery:
            if intent_id == retried:
                return Delivery.RETRY
            return await sut.execute(intent_id)

    queue.idle = [QueuedIntent("1-0", retried)]
    queue.new = [QueuedIntent("2-0", settled), QueuedIntent("3-0", None)]
    notifier = RunNotifier(queue, Flaky(store, identity, messages))

    assert await notifier.execute(block_ms=1) == 3

    assert queue.acked == ["2-0", "3-0"]  # the retry stays pending; junk is dropped
    assert len(messages.sent) == 1
