"""ChangeTaskStatus at its command seam: who may change what, and what is delegated."""

from datetime import UTC, datetime
from itertools import permutations
from uuid import UUID, uuid4

import pytest
from kernel_domain.errors import ErrorType
from kernel_domain.result import Result
from kernel_platform.security import Actor, ActorRole

from application.commands.change_task_status import (
    ChangeTaskStatusCommand,
    ChangeTaskStatusCommandHandler,
)
from domain.errors import TaskErrors
from domain.tasks import Task, TaskStatus

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
AUTHOR = uuid4()


def task(status: TaskStatus = TaskStatus.PENDING, version: int = 1) -> Task:
    return Task(uuid4(), "text", status, version, AUTHOR, "Ann", NOW, NOW)


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
        self.conflict = False

    async def change(
        self, task_id: UUID, target: TaskStatus, expected_version: int
    ) -> Result[Task]:
        self.calls.append((task_id, target, expected_version))
        if self.conflict:
            return Result[Task].fail(TaskErrors.version_conflict())
        return Result[Task].ok(task(target, expected_version + 1))


def handler(*tasks: Task) -> tuple[ChangeTaskStatusCommandHandler, Store]:
    store = Store()
    return ChangeTaskStatusCommandHandler(Reader(*tasks), store), store


def command(
    card_id: UUID, actor: Actor, target: TaskStatus = TaskStatus.COMPLETED
) -> ChangeTaskStatusCommand:
    return ChangeTaskStatusCommand(card_id, target, 1, actor)


@pytest.mark.parametrize(
    "actor",
    [
        Actor(AUTHOR, ActorRole.USER),
        Actor(uuid4(), ActorRole.ADMIN),
        Actor(AUTHOR, ActorRole.ADMIN),
    ],
)
async def test_the_author_and_any_admin_may_change_the_status(actor: Actor) -> None:
    card = task()
    sut, store = handler(card)

    result = await sut.execute(command(card.id, actor))

    assert result.is_ok and result.value.status == "completed"
    assert store.calls == [(card.id, TaskStatus.COMPLETED, 1)]


async def test_a_plain_user_cannot_change_a_foreign_task() -> None:
    card = task()
    sut, store = handler(card)

    result = await sut.execute(command(card.id, Actor(uuid4(), ActorRole.USER)))

    assert result.is_err and result.error.type is ErrorType.FORBIDDEN
    assert result.error.code == "forbidden"
    assert store.calls == []  # the server, not the client, refuses


async def test_a_missing_task_is_not_found_before_any_write() -> None:
    sut, store = handler()

    result = await sut.execute(command(uuid4(), Actor(AUTHOR, ActorRole.ADMIN)))

    assert result.is_err and result.error.type is ErrorType.NOT_FOUND
    assert store.calls == []


@pytest.mark.parametrize("source, target", list(permutations(TaskStatus, 2)))
async def test_every_transition_between_the_three_statuses_is_allowed(
    source: TaskStatus, target: TaskStatus
) -> None:
    card = task(source)
    sut, _ = handler(card)

    result = await sut.execute(command(card.id, Actor(AUTHOR, ActorRole.USER), target))

    assert result.is_ok and result.value.status == target.value


async def test_a_version_conflict_from_the_store_is_returned() -> None:
    card = task()
    sut, store = handler(card)
    store.conflict = True

    result = await sut.execute(command(card.id, Actor(AUTHOR, ActorRole.USER)))

    assert result.is_err and result.error.type is ErrorType.CONFLICT
    assert result.error.code == "version_conflict"
