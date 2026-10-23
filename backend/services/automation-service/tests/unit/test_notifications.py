"""Decision notifications: inbox, intents, stream hand-off and stale suppression."""

import json
from uuid import UUID, uuid4

import pytest
from conftest import FakeAutomationUnitOfWork, FakeMessenger, Log, World
from kernel_domain.result import Result
from kernel_platform.consumer import InvalidMessage

from application.commands.deliver_notification import (
    MAX_ATTEMPTS,
    DeliverNotificationCommand,
    DeliverNotificationCommandHandler,
    Delivery,
    RunNotifier,
)
from application.commands.relay_intents import RelayIntents
from application.commands.store_member_decision import (
    StoreMemberDecisionCommand,
    StoreMemberDecisionCommandHandler,
)
from application.ports import IdentityUnavailable, QueuedIntent, TelegramMember
from domain.entities.notification_intent import NotificationIntent
from domain.value_objects.intent_id import IntentId
from domain.value_objects.intent_status import IntentStatus, Verdict
from domain.value_objects.join_outcome import JoinOutcome
from infrastructure.messaging.identity_event_consumer import IdentityEventConsumer

USER = 7_000_000_001
MEMBER_ID = uuid4()
EVENT_ID = uuid4()


def intent(
    kind: str = "access.approved",
    version: int = 2,
    status: IntentStatus = IntentStatus.PENDING,
) -> NotificationIntent:
    return NotificationIntent(
        IntentId(uuid4()),
        chat_id=USER,
        member_id=MEMBER_ID,
        kind=kind,
        access_version=version,
        status=status,
    )


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
    verdict = intent(kind, intent_version).delivery_verdict(status, version)

    assert verdict.is_ok and verdict.value is expected


def test_a_kind_without_a_rule_is_never_sent_by_default() -> None:
    verdict = intent("marketing.blast", 1).delivery_verdict("approved", 1)

    assert verdict.is_err and verdict.error.code == "unknown_notification_kind"


def test_an_intent_settles_once_while_it_is_open() -> None:
    sut = intent()

    assert sut.mark_enqueued() is True and sut.mark_enqueued() is False
    assert sut.settle(IntentStatus.SENT) is True
    assert sut.settle(IntentStatus.FAILED) is False
    assert sut.status is IntentStatus.SENT


def test_failures_are_counted_until_the_attempts_are_spent() -> None:
    sut = intent(status=IntentStatus.ENQUEUED)

    assert sut.record_failure(3) is IntentStatus.ENQUEUED
    assert sut.record_failure(3) is IntentStatus.ENQUEUED
    assert sut.record_failure(3) is IntentStatus.FAILED
    assert sut.attempts == 3


# ---- consumer --------------------------------------------------------------


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


def consumer(world: World) -> IdentityEventConsumer:
    return IdentityEventConsumer(
        StoreMemberDecisionCommandHandler(lambda: FakeAutomationUnitOfWork(world))
    )


async def test_a_redelivered_event_reports_nothing_new() -> None:
    world = World()
    handler = StoreMemberDecisionCommandHandler(lambda: FakeAutomationUnitOfWork(world))
    command = StoreMemberDecisionCommand(EVENT_ID, MEMBER_ID, USER, "approved", 2)

    first = await handler.execute(command)
    again = await handler.execute(command)

    assert first.value is True and again.value is False


async def test_an_event_becomes_one_intent_for_the_applicants_private_chat() -> None:
    world = World()

    await consumer(world).handle(str(EVENT_ID), "member.access-decided", event())

    stored = world.intents[IntentId(EVENT_ID)]
    assert (stored.chat_id, stored.member_id, stored.kind) == (
        USER,
        MEMBER_ID,
        "access.approved",
    )
    assert (stored.access_version, stored.status) == (2, IntentStatus.PENDING)
    assert world.inbox == {str(EVENT_ID)}


async def test_a_redelivered_event_stores_nothing_new() -> None:
    world = World()
    sut = consumer(world)
    await sut.handle(str(EVENT_ID), "member.access-decided", event())
    world.intents[IntentId(EVENT_ID)].status = IntentStatus.SENT

    await sut.handle(str(EVENT_ID), "member.access-decided", event())

    assert len(world.intents) == 1
    assert world.intents[IntentId(EVENT_ID)].status is IntentStatus.SENT


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
    world = World()

    with pytest.raises(InvalidMessage):
        await consumer(world).handle(message_id, kind, body)

    assert world.intents == {} and world.inbox == set()


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


def relay(world: World, queue: Queue) -> RelayIntents:
    return RelayIntents(lambda: FakeAutomationUnitOfWork(world), queue)


async def test_pending_intents_go_to_the_stream_once_and_are_marked() -> None:
    world, queue = World(), Queue()
    first, second = intent(), intent()
    world.intents = {first.id: first, second.id: second}
    sut = relay(world, queue)

    assert await sut.execute() == 2
    assert await sut.execute() == 0

    assert sorted(queue.added) == sorted([first.id.value, second.id.value])
    assert {i.status for i in world.intents.values()} == {IntentStatus.ENQUEUED}


async def test_an_intent_stays_pending_when_the_stream_is_down() -> None:
    world, queue = World(), Queue()
    waiting = intent()
    world.intents = {waiting.id: waiting}
    queue.failure = ConnectionError("redis down")

    with pytest.raises(ConnectionError):
        await relay(world, queue).execute()

    assert world.intents[waiting.id].status is IntentStatus.PENDING


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


def deliver(
    world: World, member: TelegramMember | None, messages: FakeMessenger | None = None
) -> tuple[DeliverNotificationCommandHandler, Identity, FakeMessenger]:
    identity, out = Identity(member), messages or FakeMessenger(Log())
    handler = DeliverNotificationCommandHandler(
        lambda: FakeAutomationUnitOfWork(world), identity, out
    )
    return handler, identity, out


def member(status: str = "approved", version: int = 2) -> TelegramMember:
    return TelegramMember(USER, "user", status, version)


def queued(world: World, new: NotificationIntent) -> DeliverNotificationCommand:
    new.status = IntentStatus.ENQUEUED
    world.intents[new.id] = new
    return DeliverNotificationCommand(new.id.value)


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
    world = World()
    command = queued(world, intent(kind))
    sut, _, messages = deliver(world, member())

    assert await sut.execute(command) is Delivery.DONE
    assert await sut.execute(command) is Delivery.DONE  # duplicate stream entry

    assert messages.sent == [(USER, text)]
    assert world.intents[IntentId(command.intent_id)].status is IntentStatus.SENT


async def test_a_decision_older_than_identitys_access_version_is_never_sent() -> None:
    world = World()
    command = queued(world, intent("access.approved", version=2))
    sut, _, messages = deliver(world, member("blocked", version=3))

    assert await sut.execute(command) is Delivery.DONE

    assert messages.sent == []
    stored = world.intents[IntentId(command.intent_id)]
    assert stored.status is IntentStatus.SUPPRESSED_STALE


async def test_the_final_notification_to_a_blocked_author_is_suppressed() -> None:
    world = World()
    command = queued(world, intent("task.result", version=1))
    sut, _, messages = deliver(world, member("blocked", version=3))

    assert await sut.execute(command) is Delivery.DONE

    assert messages.sent == []
    stored = world.intents[IntentId(command.intent_id)]
    assert stored.status is IntentStatus.SUPPRESSED_BLOCKED


async def test_the_result_notification_sends_the_stored_body() -> None:
    world = World()
    result = NotificationIntent.result(
        IntentId(uuid4()),
        chat_id=USER,
        member_id=MEMBER_ID,
        kind="task.result",
        body="Задача создана: Купить молоко",
    )
    command = queued(world, result)
    sut, _, messages = deliver(world, member())

    assert await sut.execute(command) is Delivery.DONE

    assert messages.sent == [(USER, "Задача создана: Купить молоко")]
    assert world.intents[result.id].status is IntentStatus.SENT


async def test_a_result_notification_without_a_body_is_failed_not_sent() -> None:
    world = World()
    command = queued(world, intent("task.result", version=0))
    sut, _, messages = deliver(world, member())

    await sut.execute(command)

    assert messages.sent == []
    assert world.intents[IntentId(command.intent_id)].status is IntentStatus.FAILED


async def test_an_unavailable_identity_is_retried_then_marked_failed() -> None:
    world = World()
    command = queued(world, intent())
    sut, identity, messages = deliver(world, member())
    identity.failure = IdentityUnavailable()

    results = [await sut.execute(command) for _ in range(MAX_ATTEMPTS)]

    assert results == [Delivery.RETRY] * (MAX_ATTEMPTS - 1) + [Delivery.DONE]
    assert world.intents[IntentId(command.intent_id)].status is IntentStatus.FAILED
    assert messages.sent == []


async def test_a_telegram_failure_is_retried_and_a_later_success_is_sent() -> None:
    world = World()
    command = queued(world, intent())
    messages = FakeMessenger(Log())
    messages.failure = ConnectionError("telegram down")
    sut, _, _ = deliver(world, member(), messages)

    assert await sut.execute(command) is Delivery.RETRY
    messages.failure = None
    assert await sut.execute(command) is Delivery.DONE

    assert len(messages.sent) == 1
    assert world.intents[IntentId(command.intent_id)].status is IntentStatus.SENT


async def test_unknown_intents_and_kinds_are_settled_without_sending() -> None:
    world = World()
    odd = queued(world, intent("marketing.blast"))
    sut, _, messages = deliver(world, member())

    assert await sut.execute(DeliverNotificationCommand(uuid4())) is Delivery.DONE
    assert await sut.execute(odd) is Delivery.DONE

    assert messages.sent == []
    assert world.intents[IntentId(odd.intent_id)].status is IntentStatus.FAILED


async def test_the_notifier_takes_stuck_entries_first_and_acks_only_settled_ones() -> (
    None
):
    world, queue = World(), Queue()
    settled = queued(world, intent())
    retried = queued(world, intent())
    sut, _, messages = deliver(world, member())

    class Flaky(DeliverNotificationCommandHandler):
        async def execute(self, command: DeliverNotificationCommand) -> Delivery:
            if command == retried:
                return Delivery.RETRY
            return await sut.execute(command)

    queue.idle = [QueuedIntent("1-0", retried.intent_id)]
    queue.new = [QueuedIntent("2-0", settled.intent_id), QueuedIntent("3-0", None)]
    notifier = RunNotifier(
        queue,
        Flaky(lambda: FakeAutomationUnitOfWork(world), Identity(member()), messages),
    )

    assert await notifier.execute(block_ms=1) == 3

    assert queue.acked == ["2-0", "3-0"]  # the retry stays pending; junk is dropped
    assert len(messages.sent) == 1
