"""CreateTask at its command seam and the queue adapter in front of it."""

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from conftest import Board, FakeTasksUnitOfWork
from kernel_domain.errors import ErrorType
from kernel_platform.consumer import InvalidMessage

from application.commands.create_task import (
    CreateTaskCommand,
    CreateTaskCommandHandler,
)
from domain.events.task_domain_event import TaskCreated
from domain.value_objects.task_id import TaskId
from domain.value_objects.task_source import TaskSource
from infrastructure.db.replay import replay_task_created
from infrastructure.messaging.create_task_consumer import CreateTaskConsumer

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
JOB = uuid4()
AUTHOR = uuid4()


def new_command(**overrides: Any) -> CreateTaskCommand:
    values: dict[str, Any] = dict(
        message_id="m-1",
        correlation_id=JOB,
        bot_id=123,
        chat_id=456,
        source_message_id=7,
        text="Купить молоко",
        author_id=AUTHOR,
        author_telegram_id=456,
        author_name="Ann (@ann)",
    )
    values.update(overrides)
    return CreateTaskCommand(**values)


def handler(board: Board) -> CreateTaskCommandHandler:
    return CreateTaskCommandHandler(lambda: FakeTasksUnitOfWork(board), lambda: NOW)


async def test_a_valid_command_stores_the_task_with_its_source_and_author() -> None:
    board = Board()

    result = await handler(board).execute(new_command())

    assert result.is_ok and result.value is True
    (task,) = board.tasks.values()
    assert task.id == TaskId.from_source("123:456:7")
    assert task.source_key == "123:456:7"
    assert task.correlation_id == JOB
    assert (task.text, task.author_id, task.author_name) == (
        "Купить молоко",
        AUTHOR,
        "Ann (@ann)",
    )
    assert task.author_telegram_id == 456
    assert task.created_at == NOW


@pytest.mark.parametrize("source_type", [TaskSource.VOICE, TaskSource.TEXT, None])
async def test_the_command_preserves_the_source_type_or_unknown_legacy_source(
    source_type: TaskSource | None,
) -> None:
    board = Board()

    await handler(board).execute(new_command(source_type=source_type))

    (task,) = board.tasks.values()
    assert task.source_type is source_type


async def test_the_created_event_names_the_task_and_the_job() -> None:
    board = Board()

    await handler(board).execute(new_command())

    (event,) = board.events
    assert isinstance(event, TaskCreated)
    assert event.task_id == TaskId.from_source("123:456:7")
    assert event.correlation_id == JOB
    assert event.source_key == "123:456:7"


async def test_a_redelivered_message_changes_nothing() -> None:
    board = Board()
    sut = handler(board)

    await sut.execute(new_command())
    again = await sut.execute(new_command())

    assert again.is_ok and again.value is False
    assert len(board.tasks) == 1 and len(board.events) == 1


async def test_a_second_message_for_one_source_yields_one_task_and_one_event() -> None:
    board = Board()
    sut = handler(board)

    await sut.execute(new_command(message_id="m-1"))
    second = await sut.execute(new_command(message_id="m-2"))

    assert second.is_ok
    assert len(board.tasks) == 1
    assert len(board.events) == 1  # and one TaskCreated outbox row


async def test_a_new_task_runs_the_after_commit_handlers_once() -> None:
    board = Board()
    handled = AsyncMock()
    sut = CreateTaskCommandHandler(
        lambda: FakeTasksUnitOfWork(board, [handled]), lambda: NOW
    )

    await sut.execute(new_command(message_id="m-1"))
    await sut.execute(new_command(message_id="m-1"))  # a redelivery

    handled.assert_awaited_once()
    assert isinstance(handled.await_args_list[0].args[0], TaskCreated)


async def test_a_failing_after_commit_handler_does_not_fail_the_creation() -> None:
    board = Board()
    handled = AsyncMock(side_effect=OSError("redis down"))
    sut = CreateTaskCommandHandler(
        lambda: FakeTasksUnitOfWork(board, [handled]), lambda: NOW
    )

    result = await sut.execute(new_command())

    assert result.is_ok and result.value is True
    assert len(board.tasks) == 1


async def test_a_different_source_is_a_different_task() -> None:
    board = Board()
    sut = handler(board)

    await sut.execute(new_command(message_id="m-1", source_message_id=7))
    await sut.execute(new_command(message_id="m-2", source_message_id=8))

    assert len(board.tasks) == 2


@pytest.mark.parametrize(
    "overrides", [{"text": ""}, {"text": "x" * 4097}, {"author_name": ""}]
)
async def test_an_invalid_task_is_a_validation_error_and_stores_nothing(
    overrides: dict[str, Any],
) -> None:
    board = Board()

    result = await handler(board).execute(new_command(**overrides))

    assert result.is_err and result.error.type is ErrorType.VALIDATION
    assert board.tasks == {} and board.inbox == set()


def envelope(**payload_overrides: Any) -> bytes:
    payload: dict[str, Any] = {
        "job_id": str(JOB),
        "bot_id": 123,
        "chat_id": 456,
        "message_id": 7,
        "author_id": str(AUTHOR),
        "author_telegram_id": 456,
        "author_name": "Ann (@ann)",
        "text": "Купить молоко",
    }
    payload.update(payload_overrides)
    return json.dumps(
        {
            "envelope_version": 1,
            "message_id": str(uuid4()),
            "type": "CreateTask",
            "correlation_id": str(JOB),
            "occurred_at": "2026-01-01T11:59:59+00:00",
            "payload": payload,
        }
    ).encode()


def consumer(board: Board) -> CreateTaskConsumer:
    return CreateTaskConsumer(handler(board))


async def test_a_valid_envelope_becomes_a_stored_task() -> None:
    board = Board()

    await consumer(board).handle("m-1", "CreateTask", envelope())

    (task,) = board.tasks.values()
    assert task.source_key == "123:456:7"
    assert board.inbox == {"m-1"}


@pytest.mark.parametrize("source_type", ["voice", "text", None])
async def test_the_envelope_source_type_reaches_the_task(
    source_type: str | None,
) -> None:
    board = Board()

    await consumer(board).handle("m-1", "CreateTask", envelope(source_type=source_type))

    (task,) = board.tasks.values()
    assert task.source_type == source_type


@pytest.mark.parametrize(
    "type_, body",
    [
        ("Other", envelope()),
        (None, envelope()),
        ("CreateTask", b"not json"),
        ("CreateTask", envelope(text="")),
        ("CreateTask", envelope(text="x" * 4097)),
        ("CreateTask", envelope(author_id="nope")),
        ("CreateTask", envelope(chat_id="1")),
        ("CreateTask", envelope(source_type="photo")),
        ("CreateTask", envelope(author_name="")),
        ("CreateTask", envelope(author_name="n" * 65)),
    ],
)
async def test_a_message_that_can_never_work_is_invalid(
    type_: str | None, body: bytes
) -> None:
    board = Board()

    with pytest.raises(InvalidMessage):
        await consumer(board).handle("m-1", type_, body)

    assert board.tasks == {}


async def test_a_missing_message_id_is_invalid() -> None:
    with pytest.raises(InvalidMessage):
        await consumer(Board()).handle(None, "CreateTask", envelope())


async def test_a_storage_failure_propagates_so_the_message_is_requeued() -> None:
    board = Board()

    def broken() -> FakeTasksUnitOfWork:
        raise OSError("pg down")

    sut = CreateTaskConsumer(CreateTaskCommandHandler(broken, lambda: NOW))

    with pytest.raises(OSError):
        await sut.handle("m-1", "CreateTask", envelope())

    assert board.tasks == {}


async def test_replay_task_created_commits_when_outbox_row_replayed() -> None:
    session = AsyncMock()
    session.__aenter__.return_value = session
    sessions = MagicMock(return_value=session)

    with patch(
        "infrastructure.db.replay.replay", new_callable=AsyncMock
    ) as mock_replay:
        mock_replay.return_value = True
        replayed = await replay_task_created(sessions, JOB)

        assert replayed is True
        session.commit.assert_awaited_once()


async def test_replay_task_created_does_not_commit_when_nothing_replayed() -> None:
    session = AsyncMock()
    session.__aenter__.return_value = session
    sessions = MagicMock(return_value=session)

    with patch(
        "infrastructure.db.replay.replay", new_callable=AsyncMock
    ) as mock_replay:
        mock_replay.return_value = False
        replayed = await replay_task_created(sessions, JOB)

        assert replayed is False
        session.commit.assert_not_awaited()
