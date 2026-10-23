"""ChangeTaskStatus at its command seam: who may change what, and what is stored."""

from datetime import timedelta
from itertools import permutations
from uuid import uuid4

import pytest
from kernel_domain.errors import ErrorType
from kernel_platform.security.actor import Actor, ActorRole
from support.fakes import AUTHOR, BASE, Board, FakeTasksUnitOfWork

from application.commands.change_task_status import (
    ChangeTaskStatusCommand,
    ChangeTaskStatusCommandHandler,
)
from domain.entities.task import Task
from domain.events.task_domain_event import TaskStatusChanged
from domain.value_objects.task_status import TaskStatus

NOW = BASE + timedelta(hours=1)


class Env:
    def __init__(self) -> None:
        self.board = Board()
        self.units: list[FakeTasksUnitOfWork] = []
        self.sut = ChangeTaskStatusCommandHandler(self._uow, clock=lambda: NOW)

    def _uow(self) -> FakeTasksUnitOfWork:
        unit = FakeTasksUnitOfWork(self.board)
        self.units.append(unit)
        return unit


def command(
    task: Task,
    actor: Actor,
    target: TaskStatus = TaskStatus.COMPLETED,
    version: int = 1,
) -> ChangeTaskStatusCommand:
    return ChangeTaskStatusCommand(task.id.value, target, version, actor)


@pytest.mark.parametrize(
    "actor",
    [
        Actor(AUTHOR, ActorRole.USER),
        Actor(uuid4(), ActorRole.ADMIN),
        Actor(AUTHOR, ActorRole.ADMIN),
    ],
)
async def test_the_author_and_any_admin_may_change_the_status(actor: Actor) -> None:
    env = Env()
    task = env.board.add(1)

    result = await env.sut.execute(command(task, actor))

    assert result.is_ok and result.value.status == "completed"
    assert result.value.version == 2
    stored = env.board.tasks[task.id]
    assert (stored.status, stored.version, stored.updated_at) == (
        TaskStatus.COMPLETED,
        2,
        NOW,
    )
    (event,) = env.board.events
    assert isinstance(event, TaskStatusChanged)


async def test_a_plain_user_cannot_change_a_foreign_task() -> None:
    env = Env()
    task = env.board.add(1)

    result = await env.sut.execute(command(task, Actor(uuid4(), ActorRole.USER)))

    assert result.is_err and result.error.type is ErrorType.FORBIDDEN
    assert result.error.code == "forbidden"
    assert env.board.tasks[task.id].status is TaskStatus.PENDING  # the server refuses
    assert not env.units[0].committed


async def test_a_missing_task_is_not_found_before_any_write() -> None:
    env = Env()
    task = env.board.add(1)
    del env.board.tasks[task.id]

    result = await env.sut.execute(command(task, Actor(AUTHOR, ActorRole.ADMIN)))

    assert result.is_err and result.error.type is ErrorType.NOT_FOUND
    assert not env.units[0].committed


@pytest.mark.parametrize("source, target", list(permutations(TaskStatus, 2)))
async def test_every_transition_between_the_three_statuses_is_allowed(
    source: TaskStatus, target: TaskStatus
) -> None:
    env = Env()
    task = env.board.add(1, status=source)

    result = await env.sut.execute(command(task, Actor(AUTHOR, ActorRole.USER), target))

    assert result.is_ok and result.value.status == target.value


async def test_a_stale_version_is_a_conflict_and_changes_nothing() -> None:
    env = Env()
    task = env.board.add(1, version=3)

    result = await env.sut.execute(
        command(task, Actor(AUTHOR, ActorRole.USER), version=2)
    )

    assert result.is_err and result.error.type is ErrorType.CONFLICT
    assert env.board.tasks[task.id].version == 3
    assert not env.units[0].committed


async def test_a_no_op_with_the_current_version_stores_and_signals_nothing() -> None:
    env = Env()
    task = env.board.add(1, version=3, status=TaskStatus.COMPLETED)

    result = await env.sut.execute(
        command(task, Actor(AUTHOR, ActorRole.USER), version=3)
    )

    assert result.is_ok and result.value.version == 3
    assert env.board.events == []
    assert not env.units[0].committed


async def test_a_writer_that_lost_the_race_gets_a_conflict_and_no_event() -> None:
    """The repository's version check fails after this handler loaded the task."""
    env = Env()
    task = env.board.add(1)

    def another_session_wins() -> None:
        env.board.tasks[task.id].version += 1

    def uow() -> FakeTasksUnitOfWork:
        unit = FakeTasksUnitOfWork(env.board)
        unit.before_update = another_session_wins
        return unit

    sut = ChangeTaskStatusCommandHandler(uow, clock=lambda: NOW)

    result = await sut.execute(command(task, Actor(AUTHOR, ActorRole.USER)))

    assert result.is_err and result.error.code == "version_conflict"
    assert env.board.events == []
