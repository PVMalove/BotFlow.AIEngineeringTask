"""ChangeTaskStatus at its command seam: who may change what, and what is delegated."""

from datetime import UTC, datetime
from itertools import permutations
from uuid import UUID, uuid4

import pytest

from application.commands.change_task_status import ChangeTaskStatus
from application.ports import (
    Forbidden,
    NotApproved,
    TaskNotFound,
    Unauthenticated,
    VersionConflict,
)
from domain.tasks import Actor, Role, Task, TaskStatus

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
AUTHOR = uuid4()


def task(status: TaskStatus = TaskStatus.PENDING, version: int = 1) -> Task:
    return Task(uuid4(), "text", status, version, AUTHOR, "Ann", NOW, NOW)


class Identity:
    def __init__(self, actor: Actor) -> None:
        self.actor = actor
        self.failure: Exception | None = None

    async def require_approved(self, token: str) -> Actor:
        if self.failure:
            raise self.failure
        return self.actor


class Reader:
    def __init__(self, *tasks: Task) -> None:
        self.tasks = {t.id: t for t in tasks}

    async def list_page(self, *args: object) -> list[Task]:
        raise NotImplementedError

    async def get(self, task_id: UUID) -> Task | None:
        return self.tasks.get(task_id)


class Store:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, TaskStatus, int]] = []
        self.failure: Exception | None = None

    async def change(
        self, task_id: UUID, target: TaskStatus, expected_version: int
    ) -> Task:
        self.calls.append((task_id, target, expected_version))
        if self.failure:
            raise self.failure
        return task(target, expected_version + 1)


def use_case(actor: Actor, *tasks: Task) -> tuple[ChangeTaskStatus, Identity, Store]:
    identity, store = Identity(actor), Store()
    return ChangeTaskStatus(identity, Reader(*tasks), store), identity, store


@pytest.mark.parametrize(
    "actor",
    [Actor(AUTHOR, Role.USER), Actor(uuid4(), Role.ADMIN), Actor(AUTHOR, Role.ADMIN)],
)
async def test_the_author_and_any_admin_may_change_the_status(actor: Actor) -> None:
    card = task()
    sut, _, store = use_case(actor, card)

    result = await sut.execute("tok", card.id, TaskStatus.COMPLETED, 1)

    assert result.status is TaskStatus.COMPLETED
    assert store.calls == [(card.id, TaskStatus.COMPLETED, 1)]


async def test_a_plain_user_cannot_change_a_foreign_task() -> None:
    card = task()
    sut, _, store = use_case(Actor(uuid4(), Role.USER), card)

    with pytest.raises(Forbidden):
        await sut.execute("tok", card.id, TaskStatus.COMPLETED, 1)

    assert store.calls == []  # the server, not the client, refuses


async def test_a_missing_task_is_not_found_before_any_write() -> None:
    sut, _, store = use_case(Actor(AUTHOR, Role.ADMIN))

    with pytest.raises(TaskNotFound):
        await sut.execute("tok", uuid4(), TaskStatus.COMPLETED, 1)

    assert store.calls == []


@pytest.mark.parametrize("source, target", list(permutations(TaskStatus, 2)))
async def test_every_transition_between_the_three_statuses_is_allowed(
    source: TaskStatus, target: TaskStatus
) -> None:
    card = task(source)
    sut, _, _ = use_case(Actor(AUTHOR, Role.USER), card)

    result = await sut.execute("tok", card.id, target, 1)

    assert result.status is target


async def test_a_version_conflict_from_the_store_propagates() -> None:
    card = task()
    sut, _, store = use_case(Actor(AUTHOR, Role.USER), card)
    store.failure = VersionConflict()

    with pytest.raises(VersionConflict):
        await sut.execute("tok", card.id, TaskStatus.COMPLETED, 1)


@pytest.mark.parametrize("token", [None, ""])
async def test_a_missing_session_is_unauthenticated(token: str | None) -> None:
    card = task()
    sut, _, store = use_case(Actor(AUTHOR, Role.USER), card)

    with pytest.raises(Unauthenticated):
        await sut.execute(token, card.id, TaskStatus.COMPLETED, 1)

    assert store.calls == []


async def test_an_unapproved_member_changes_nothing() -> None:
    card = task()
    sut, identity, store = use_case(Actor(AUTHOR, Role.USER), card)
    identity.failure = NotApproved()

    with pytest.raises(NotApproved):
        await sut.execute("tok", card.id, TaskStatus.COMPLETED, 1)

    assert store.calls == []
