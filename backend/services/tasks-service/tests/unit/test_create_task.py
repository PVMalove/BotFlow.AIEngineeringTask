"""CreateTaskFromCommand at its command seam: envelope in, one stored result out."""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from botflow_platform.consumer import InvalidMessage

from application.commands.create_task import CreateTaskFromCommand
from application.ports import NewTask, TaskCreatedMessage

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
JOB = uuid4()
AUTHOR = uuid4()


class Store:
    def __init__(self) -> None:
        self.calls: list[tuple[str, NewTask, TaskCreatedMessage]] = []
        self.failure: Exception | None = None

    async def create(
        self, message_id: str, task: NewTask, created: TaskCreatedMessage
    ) -> bool:
        if self.failure:
            raise self.failure
        self.calls.append((message_id, task, created))
        return True


def command(**overrides: Any) -> bytes:
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
    payload.update(overrides)
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


def use_case(store: Store) -> CreateTaskFromCommand:
    return CreateTaskFromCommand(store, clock=lambda: NOW)


async def test_a_valid_command_stores_the_task_with_its_source_and_author() -> None:
    store = Store()

    await use_case(store).execute("m-1", "CreateTask", command())

    ((message_id, task, created),) = store.calls
    assert message_id == "m-1"
    assert task.source_key == "123:456:7"
    assert task.correlation_id == JOB
    assert (task.text, task.author_id, task.author_name) == (
        "Купить молоко",
        AUTHOR,
        "Ann (@ann)",
    )
    assert task.author_telegram_id == 456
    assert created.correlation_id == JOB


async def test_the_created_event_names_the_task_and_the_job_in_its_envelope() -> None:
    store = Store()

    await use_case(store).execute("m-1", "CreateTask", command())

    _, task, created = store.calls[0]
    envelope = created.payload
    assert envelope["envelope_version"] == 1
    assert envelope["type"] == "TaskCreated"
    assert envelope["message_id"] == str(created.id)
    assert envelope["correlation_id"] == str(JOB)
    assert envelope["payload"] == {
        "job_id": str(JOB),
        "task_id": str(task.id),
        "source_key": "123:456:7",
    }
    assert created.routing_key == "task.created"


async def test_a_second_message_for_one_source_yields_the_same_task_and_result() -> (
    None
):
    store = Store()

    await use_case(store).execute("m-1", "CreateTask", command())
    await use_case(store).execute("m-2", "CreateTask", command())

    (_, first, first_event), (_, second, second_event) = store.calls
    assert first.id == second.id  # the store's unique source then makes it one task
    assert first_event.id == second_event.id  # and one TaskCreated outbox row


async def test_a_different_source_is_a_different_task() -> None:
    store = Store()

    await use_case(store).execute("m-1", "CreateTask", command(message_id=7))
    await use_case(store).execute("m-2", "CreateTask", command(message_id=8))

    assert store.calls[0][1].id != store.calls[1][1].id


@pytest.mark.parametrize(
    "type_, body",
    [
        ("Other", command()),
        (None, command()),
        ("CreateTask", b"not json"),
        ("CreateTask", command(text="")),
        ("CreateTask", command(text="x" * 4097)),
        ("CreateTask", command(author_id="nope")),
        ("CreateTask", command(chat_id="1")),
        ("CreateTask", command(author_name="")),
        ("CreateTask", command(author_name="n" * 65)),
    ],
)
async def test_a_message_that_can_never_work_is_invalid(
    type_: str | None, body: bytes
) -> None:
    store = Store()

    with pytest.raises(InvalidMessage):
        await use_case(store).execute("m-1", type_, body)

    assert store.calls == []


async def test_a_missing_message_id_is_invalid() -> None:
    with pytest.raises(InvalidMessage):
        await use_case(Store()).execute(None, "CreateTask", command())


async def test_a_storage_failure_propagates_so_the_message_is_requeued() -> None:
    store = Store()
    store.failure = OSError("pg down")

    with pytest.raises(OSError):
        await use_case(store).execute("m-1", "CreateTask", command())
