"""Tasks REST contract: board visibility, keyset cursor, status filter, auth."""

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
from conftest import AUTHOR, Board, FakeTasksUnitOfWork
from kernel_domain.errors import Error
from kernel_domain.result import Result
from kernel_platform.security.actor import Actor, ActorRole
from kernel_platform.security.errors import AuthErrors

from api.containers import Services
from api.dependencies import get_services
from api.main import create_tasks_app
from application.commands.change_task_status import ChangeTaskStatusCommandHandler
from application.ports import IdentityUnavailable
from application.queries.get_task import GetTaskQueryHandler
from application.queries.list_tasks import ListTasksQueryHandler
from domain.entities.task import Task
from domain.value_objects.task_source import TaskSource
from domain.value_objects.task_status import TaskStatus

COOKIES = {"botflow_session": "session-token"}


class Identity:
    def __init__(self) -> None:
        self.tokens: list[str] = []
        self.error: Error | None = None
        self.unavailable = False
        self.actor = Actor(AUTHOR, ActorRole.USER)

    async def require_approved(self, token: str) -> Result[Actor]:
        self.tokens.append(token)
        if self.unavailable:
            raise IdentityUnavailable
        if self.error:
            return Result[Actor].fail(self.error)
        return Result[Actor].ok(self.actor)


class Env:
    def __init__(self) -> None:
        self.identity = Identity()
        self.board = Board()
        self.services = Services(
            identity=self.identity,  # type: ignore[arg-type]
            list_tasks=ListTasksQueryHandler(self.board),
            get_task=GetTaskQueryHandler(self.board),
            change_task_status=ChangeTaskStatusCommandHandler(
                lambda: FakeTasksUnitOfWork(self.board)
            ),
        )


@pytest.fixture
def env() -> Env:
    return Env()


@pytest.fixture
async def client(env: Env) -> AsyncIterator[httpx.AsyncClient]:
    app = create_tasks_app()

    async def services() -> Services:
        return env.services

    app.dependency_overrides[get_services] = services
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        cookies=COOKIES,
        headers={"Origin": "http://test"},
    ) as client:
        yield client


def error_of(response: httpx.Response) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    assert set(body) == {"error"}
    error: dict[str, Any] = body["error"]
    return error


async def test_the_list_returns_the_whole_board_newest_first_with_authorship(
    env: Env, client: httpx.AsyncClient
) -> None:
    older = env.board.add(1, author_id=uuid4(), author_name="Bob")
    newer = env.board.add(2)

    response = await client.get("/api/v1/tasks")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert [t["id"] for t in body["data"]] == [str(newer.id.value), str(older.id.value)]
    assert body["data"][0] == {
        "id": str(newer.id.value),
        "text": "task 2",
        "status": "pending",
        "version": 1,
        "author_id": str(AUTHOR),
        "author_name": "Ann (@ann)",
        "created_at": "2026-01-01T12:02:00Z",
        "updated_at": "2026-01-01T12:02:00Z",
        "source_type": None,
    }
    assert body["data"][1]["author_name"] == "Bob"  # tasks of other authors too
    assert body["meta"] == {"next_cursor": None, "has_more": False}


async def test_the_default_page_has_fifty_tasks_and_the_cursor_continues_it(
    env: Env, client: httpx.AsyncClient
) -> None:
    for minute in range(60):
        env.board.add(minute)

    first = (await client.get("/api/v1/tasks")).json()
    assert len(first["data"]) == 50
    assert first["meta"]["has_more"] is True
    second = (
        await client.get(
            "/api/v1/tasks", params={"cursor": first["meta"]["next_cursor"]}
        )
    ).json()

    assert len(second["data"]) == 10
    assert second["meta"]["next_cursor"] is None
    assert second["meta"]["has_more"] is False
    ids = [t["id"] for t in first["data"] + second["data"]]
    assert len(set(ids)) == 60  # no overlap and no gap between pages


async def test_tasks_created_in_the_same_instant_are_paged_without_loss(
    env: Env, client: httpx.AsyncClient
) -> None:
    for _ in range(5):
        env.board.add(1)

    seen: list[str] = []
    cursor: str | None = None
    while True:
        params = {"limit": "2", **({"cursor": cursor} if cursor else {})}
        page = (await client.get("/api/v1/tasks", params=params)).json()
        seen += [t["id"] for t in page["data"]]
        cursor = page["meta"]["next_cursor"]
        if not page["meta"]["has_more"]:
            break

    assert len(seen) == len(set(seen)) == 5


@pytest.mark.parametrize(
    "limit, expected", [("100", 200), ("1", 200), ("101", 422), ("0", 422)]
)
async def test_the_limit_is_at_most_one_hundred(
    client: httpx.AsyncClient, limit: str, expected: int
) -> None:
    response = await client.get("/api/v1/tasks", params={"limit": limit})

    assert response.status_code == expected


async def test_the_status_filter_selects_one_status(
    env: Env, client: httpx.AsyncClient
) -> None:
    env.board.add(1)
    done = env.board.add(2, status=TaskStatus.COMPLETED)

    response = await client.get("/api/v1/tasks", params={"status": "completed"})

    assert [t["id"] for t in response.json()["data"]] == [str(done.id.value)]


async def test_an_unknown_status_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/tasks", params={"status": "archived"})

    assert response.status_code == 422
    assert error_of(response)["code"] == "validation_error"


@pytest.mark.parametrize("cursor", ["not-a-cursor", "e30", "!!!"])
async def test_a_malformed_cursor_is_a_client_error(
    client: httpx.AsyncClient, cursor: str
) -> None:
    response = await client.get("/api/v1/tasks", params={"cursor": cursor})

    assert response.status_code == 400
    error = error_of(response)
    assert error["code"] == "invalid_cursor"
    assert error["message"]


async def test_the_card_returns_one_task(env: Env, client: httpx.AsyncClient) -> None:
    task = env.board.add(1)

    response = await client.get(f"/api/v1/tasks/{task.id.value}")

    assert response.status_code == 200
    assert response.json()["data"]["id"] == str(task.id.value)
    assert response.json()["data"]["version"] == 1
    assert response.json()["meta"] == {}


async def test_an_unknown_card_is_not_found(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/api/v1/tasks/{uuid4()}")

    assert response.status_code == 404
    assert error_of(response)["code"] == "task_not_found"


async def test_the_session_is_checked_with_identity_on_every_request(
    env: Env, client: httpx.AsyncClient
) -> None:
    task = env.board.add(1)

    await client.get("/api/v1/tasks")
    await client.get(f"/api/v1/tasks/{task.id.value}")

    assert env.identity.tokens == ["session-token", "session-token"]


async def test_a_request_without_a_session_cookie_is_unauthenticated(
    env: Env, client: httpx.AsyncClient
) -> None:
    client.cookies.clear()

    response = await client.get("/api/v1/tasks")

    assert response.status_code == 401
    assert error_of(response)["code"] == "unauthenticated"
    assert env.identity.tokens == []


async def test_a_user_who_is_not_approved_gets_403(
    env: Env, client: httpx.AsyncClient
) -> None:
    env.identity.error = AuthErrors.not_approved()

    response = await client.get("/api/v1/tasks")

    assert response.status_code == 403
    assert error_of(response)["code"] == "not_approved"


async def test_an_unavailable_identity_never_grants_access(
    env: Env, client: httpx.AsyncClient
) -> None:
    env.board.add(1)
    env.identity.unavailable = True

    response = await client.get("/api/v1/tasks")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert error_of(response)["code"] == "service_unavailable"


async def test_the_health_endpoint_still_reports_not_ready(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 503


async def test_a_storage_failure_is_service_unavailable(
    env: Env, client: httpx.AsyncClient
) -> None:
    async def broken(*args: object) -> list[Task]:
        raise OSError("pg down")

    env.board.list_page = broken  # type: ignore[method-assign,assignment]

    response = await client.get("/api/v1/tasks")

    assert response.status_code == 503
    error = error_of(response)
    assert error["code"] == "service_unavailable"
    assert "pg down" not in response.text
    assert error["request_id"] == response.headers["x-request-id"]


async def patch(
    client: httpx.AsyncClient,
    task: Task,
    status: str,
    version: int = 1,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return await client.patch(
        f"/api/v1/tasks/{task.id.value}",
        json={"status": status, "expected_version": version},
        headers=headers,
    )


async def test_the_author_moves_a_task_and_gets_the_new_version(
    env: Env, client: httpx.AsyncClient
) -> None:
    task = env.board.add(1)

    response = await patch(client, task, "in_progress")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()["data"]
    assert (data["status"], data["version"]) == ("in_progress", 2)


async def test_a_plain_user_gets_a_safe_403_for_a_foreign_task(
    env: Env, client: httpx.AsyncClient
) -> None:
    task = env.board.add(1, author_id=uuid4())

    response = await patch(client, task, "completed", headers={"X-Request-ID": "req-1"})

    assert response.status_code == 403
    assert response.headers["x-request-id"] == "req-1"
    error = error_of(response)
    assert error["code"] == "forbidden" and error["request_id"] == "req-1"
    assert error["message"] and set(error) == {"code", "message", "request_id"}
    assert env.board.tasks[task.id].status is TaskStatus.PENDING


async def test_a_stale_version_is_409_with_the_error_contract(
    env: Env, client: httpx.AsyncClient
) -> None:
    task = env.board.add(1, version=3)

    response = await patch(client, task, "completed", version=2)

    assert response.status_code == 409
    error = error_of(response)
    assert error["code"] == "version_conflict" and error["message"]
    assert error["request_id"] == response.headers["x-request-id"]
    assert env.board.tasks[task.id].version == 3


async def test_a_stale_version_with_the_current_status_as_target_is_still_409(
    env: Env, client: httpx.AsyncClient
) -> None:
    task = env.board.add(1, version=3, status=TaskStatus.COMPLETED)

    response = await patch(client, task, "completed", version=2)

    assert response.status_code == 409


async def test_a_no_op_with_the_current_version_is_200_and_unchanged(
    env: Env, client: httpx.AsyncClient
) -> None:
    task = env.board.add(1, version=3, status=TaskStatus.COMPLETED)

    response = await patch(client, task, "completed", version=3)

    assert response.status_code == 200
    assert response.json()["data"]["version"] == 3
    assert env.board.tasks[task.id] == task


async def test_an_admin_may_change_a_foreign_task(
    env: Env, client: httpx.AsyncClient
) -> None:
    env.identity.actor = Actor(uuid4(), ActorRole.ADMIN)
    task = env.board.add(1, author_id=uuid4())

    response = await patch(client, task, "in_progress")

    assert response.status_code == 200


async def test_an_unknown_task_is_404(client: httpx.AsyncClient) -> None:
    response = await client.patch(
        f"/api/v1/tasks/{uuid4()}", json={"status": "completed", "expected_version": 1}
    )

    assert response.status_code == 404
    assert error_of(response)["code"] == "task_not_found"


async def test_a_malformed_request_id_is_replaced_not_echoed(
    env: Env, client: httpx.AsyncClient
) -> None:
    task = env.board.add(1, version=2)

    bad = "bad id<x>"

    response = await patch(client, task, "completed", headers={"X-Request-ID": bad})

    assert response.status_code == 409
    assert response.headers["x-request-id"] != bad
    assert response.headers["x-request-id"] == error_of(response)["request_id"]


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "archived", "expected_version": 1},
        {"status": "completed", "expected_version": 0},
        {"status": "completed", "expected_version": 2**31},
        {"status": "completed"},
        {"expected_version": 1},
    ],
)
async def test_an_invalid_patch_body_is_rejected(
    env: Env, client: httpx.AsyncClient, payload: dict[str, object]
) -> None:
    task = env.board.add(1)

    response = await client.patch(f"/api/v1/tasks/{task.id.value}", json=payload)

    assert response.status_code == 422
    assert error_of(response)["code"] == "validation_error"


async def test_an_unauthenticated_status_change_is_refused(
    env: Env, client: httpx.AsyncClient
) -> None:
    task = env.board.add(1)
    env.identity.error = AuthErrors.unauthenticated()

    response = await patch(client, task, "completed")

    assert response.status_code == 401
    assert error_of(response)["code"] == "unauthenticated"
    assert env.board.tasks[task.id].status is TaskStatus.PENDING


@pytest.mark.parametrize("origin", [None, "http://evil.example", "https://test"])
async def test_a_status_change_from_a_foreign_origin_is_refused(
    env: Env, client: httpx.AsyncClient, origin: str | None
) -> None:
    task = env.board.add(1)
    del client.headers["Origin"]
    headers = {} if origin is None else {"Origin": origin}

    response = await patch(client, task, "completed", headers=headers)

    assert response.status_code == 403
    assert error_of(response)["code"] == "origin_not_allowed"
    assert env.identity.tokens == []
    assert env.board.tasks[task.id].status is TaskStatus.PENDING


async def test_the_largest_integer_version_is_accepted(
    env: Env, client: httpx.AsyncClient
) -> None:
    task = env.board.add(1, version=2**31 - 1)

    response = await patch(client, task, "completed", version=2**31 - 1)

    assert response.status_code == 200


@pytest.mark.parametrize("source_type", ["voice", "text", None])
async def test_source_type_is_preserved_in_list_get_and_status_patch(
    env: Env, client: httpx.AsyncClient, source_type: str | None
) -> None:
    task = env.board.add(
        1, source_type=TaskSource(source_type) if source_type else None
    )

    listed = await client.get("/api/v1/tasks")
    card = await client.get(f"/api/v1/tasks/{task.id.value}")
    changed = await client.patch(
        f"/api/v1/tasks/{task.id.value}",
        json={"status": "in_progress", "expected_version": 1},
    )

    assert listed.json()["data"][0]["source_type"] == source_type
    assert card.json()["data"]["source_type"] == source_type
    assert changed.status_code == 200
    assert changed.json()["data"]["source_type"] == source_type
