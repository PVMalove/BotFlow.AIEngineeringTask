"""The Task aggregate: creation rules, free status changes and their events."""

from datetime import timedelta
from itertools import permutations
from uuid import uuid4

import pytest
from kernel_domain.errors import ErrorType
from support.fakes import BASE, Board

from domain.entities.task import MAX_NAME, MAX_TEXT, Task
from domain.events.task_domain_event import TaskCreated, TaskStatusChanged
from domain.value_objects.task_id import TaskId
from domain.value_objects.task_status import TaskStatus

JOB = uuid4()
AUTHOR = uuid4()


def created(**overrides: object) -> Task:
    values: dict[str, object] = dict(
        text="Купить молоко",
        author_id=AUTHOR,
        author_telegram_id=456,
        author_name="Ann (@ann)",
        source_key="123:456:7",
        correlation_id=JOB,
        now=BASE,
    )
    values.update(overrides)
    result = Task.create(TaskId.from_source("123:456:7"), **values)  # type: ignore[arg-type]
    assert result.is_ok
    return result.value


def test_a_new_task_is_pending_at_version_one_with_a_created_event() -> None:
    task = created()

    assert (task.status, task.version) == (TaskStatus.PENDING, 1)
    assert task.created_at == task.updated_at == BASE
    (event,) = task.pull_events()
    assert isinstance(event, TaskCreated)
    assert (event.task_id, event.correlation_id, event.source_key) == (
        task.id,
        JOB,
        "123:456:7",
    )


def test_the_id_is_a_function_of_the_source() -> None:
    assert TaskId.from_source("1:2:3") == TaskId.from_source("1:2:3")
    assert TaskId.from_source("1:2:3") != TaskId.from_source("1:2:4")


@pytest.mark.parametrize(
    "overrides, field",
    [
        ({"text": ""}, "text"),
        ({"text": "x" * (MAX_TEXT + 1)}, "text"),
        ({"author_name": ""}, "author_name"),
        ({"author_name": "n" * (MAX_NAME + 1)}, "author_name"),
    ],
)
def test_an_invalid_task_is_not_created(
    overrides: dict[str, object], field: str
) -> None:
    values: dict[str, object] = dict(
        text="ok",
        author_id=AUTHOR,
        author_telegram_id=1,
        author_name="Ann",
        source_key="1:1:1",
        correlation_id=JOB,
        now=BASE,
    )
    values.update(overrides)

    result = Task.create(TaskId.from_source("1:1:1"), **values)  # type: ignore[arg-type]

    assert result.is_err
    assert result.error.type is ErrorType.VALIDATION
    assert result.error.invalid_field == field


@pytest.mark.parametrize("source, target", list(permutations(TaskStatus, 2)))
def test_every_transition_between_the_statuses_bumps_the_version_once(
    source: TaskStatus, target: TaskStatus
) -> None:
    task = Board().add(1, status=source, version=3)
    later = BASE + timedelta(hours=1)

    result = task.change_status(target, 3, later)

    assert result.is_ok and result.value is True
    assert (task.status, task.version, task.updated_at) == (target, 4, later)
    (event,) = task.pull_events()
    assert isinstance(event, TaskStatusChanged)
    assert (event.previous_status, event.status, event.version) == (source, target, 4)


def test_the_current_version_and_status_make_a_silent_no_op() -> None:
    task = Board().add(1, status=TaskStatus.COMPLETED, version=3)
    before = (task.status, task.version, task.updated_at)

    result = task.change_status(TaskStatus.COMPLETED, 3, BASE + timedelta(hours=1))

    assert result.is_ok and result.value is False
    assert (task.status, task.version, task.updated_at) == before
    assert task.pull_events() == []


@pytest.mark.parametrize("target", list(TaskStatus))
def test_a_stale_version_is_a_conflict_even_when_the_target_is_the_status(
    target: TaskStatus,
) -> None:
    task = Board().add(1, status=TaskStatus.COMPLETED, version=3)

    result = task.change_status(target, 2, BASE)

    assert result.is_err and result.error.type is ErrorType.CONFLICT
    assert result.error.code == "version_conflict"
    assert (task.status, task.version) == (TaskStatus.COMPLETED, 3)
    assert task.pull_events() == []
