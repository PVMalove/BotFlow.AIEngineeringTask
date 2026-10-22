"""Tasks REST contract: board visibility, keyset cursor, status filter, auth."""

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest

from api.containers import Services
from api.dependencies import get_services
from api.main import create_tasks_app
from application.commands.change_task_status import ChangeTaskStatus
from application.ports import (
    IdentityUnavailable,
    NotApproved,
    PageCursor,
    TaskNotFound,
    Unauthenticated,
    VersionConflict,
)
from application.queries.tasks import GetTask, ListTasks
from domain.tasks import Actor, Role, Task, TaskStatus

BASE = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
COOKIES = {"botflow_session": "session-token"}
AUTHOR = uuid4()


class Identity:
    def __init__(self) -> None:
        self.tokens: list[str] = []
        self.failure: Exception | None = None

    async def require_approved(self, token: str) -> Actor:
        self.tokens.append(token)
        if self.failure:
            raise self.failure
        return Actor(AUTHOR, Role.USER)


class Board:
    """Rows ordered like the SQL adapter: (created_at, id) descending."""

    def __init__(self) -> None:
        self.tasks: dict[UUID, Task] = {}

    def add(self, minutes: int, **overrides: object) -> Task:
        values: dict[str, object] = dict(
            id=uuid4(),
            text=f"task {minutes}",
            status=TaskStatus.PENDING,
            version=1,
            author_id=AUTHOR,
            author_name="Ann (@ann)",
            created_at=BASE + timedelta(minutes=minutes),
            updated_at=BASE + timedelta(minutes=minutes),
        )
        values.update(overrides)
        task = Task(**values)  # type: ignore[arg-type]
        self.tasks[task.id] = task
        return task

    async def list_page(
        self, status: TaskStatus | None, after: PageCursor | None, limit: int
    ) -> list[Task]:
        rows = sorted(
            self.tasks.values(), key=lambda t: (t.created_at, t.id), reverse=True
        )
        if status is not None:
            rows = [t for t in rows if t.status is status]
        if after is not None:
            rows = [
                t for t in rows if (t.created_at, t.id) < (after.created_at, after.id)
            ]
        return rows[:limit]

    async def get(self, task_id: UUID) -> Task | None:
        return self.tasks.get(task_id)


class StatusStore:
    """The store's contract over the in-memory board: same rules as the SQL UPDATE."""

    def __init__(self, board: Board) -> None:
        self.board = board

    async def change(
        self, task_id: UUID, target: TaskStatus, expected_version: int
    ) -> Task:
        task = self.board.tasks.get(task_id)
        if task is None:
            raise TaskNotFound
        if task.version != expected_version:
            raise VersionConflict
        if task.status is target:
            return task
        changed = replace(
            task,
            status=target,
            version=task.version + 1,
            updated_at=task.updated_at + timedelta(minutes=1),
        )
        self.board.tasks[task_id] = changed
        return changed


class Env:
    def __init__(self) -> None:
        self.identity = Identity()
        self.board = Board()
        self.services = Services(
            list_tasks=ListTasks(self.identity, self.board),
            get_task=GetTask(self.identity, self.board),
            change_task_status=ChangeTaskStatus(
                self.identity, self.board, StatusStore(self.board)
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
    ) as client:
        yield client


async def test_the_list_returns_the_whole_board_newest_first_with_authorship(
    env: Env, client: httpx.AsyncClient
) -> None:
    older = env.board.add(1, author_id=uuid4(), author_name="Bob")
    newer = env.board.add(2)

    response = await client.get("/api/v1/tasks")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert [t["id"] for t in body["data"]] == [str(newer.id), str(older.id)]
    assert body["data"][0] == {
        "id": str(newer.id),
        "text": "task 2",
        "status": "pending",
        "version": 1,
        "author_id": str(AUTHOR),
        "author_name": "Ann (@ann)",
        "created_at": "2026-01-01T12:02:00Z",
        "updated_at": "2026-01-01T12:02:00Z",
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
    assert second["meta"] == {"next_cursor": None, "has_more": False}
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

    assert [t["id"] for t in response.json()["data"]] == [str(done.id)]


async def test_an_unknown_status_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/tasks", params={"status": "archived"})

    assert response.status_code == 422


@pytest.mark.parametrize("cursor", ["not-a-cursor", "e30", "!!!"])
async def test_a_malformed_cursor_is_a_client_error(
    client: httpx.AsyncClient, cursor: str
) -> None:
    response = await client.get("/api/v1/tasks", params={"cursor": cursor})

    assert response.status_code == 400
    assert response.json() == {"detail": "invalid_cursor"}


async def test_the_card_returns_one_task(env: Env, client: httpx.AsyncClient) -> None:
    task = env.board.add(1)

    response = await client.get(f"/api/v1/tasks/{task.id}")

    assert response.status_code == 200
    assert response.json()["data"]["id"] == str(task.id)
    assert response.json()["data"]["version"] == 1


async def test_an_unknown_card_is_not_found(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/api/v1/tasks/{uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"detail": "task_not_found"}


async def test_the_session_is_checked_with_identity_on_every_request(
    env: Env, client: httpx.AsyncClient
) -> None:
    task = env.board.add(1)

    await client.get("/api/v1/tasks")
    await client.get(f"/api/v1/tasks/{task.id}")

    assert env.identity.tokens == ["session-token", "session-token"]


async def test_a_request_without_a_session_cookie_is_unauthenticated(
    env: Env, client: httpx.AsyncClient
) -> None:
    client.cookies.clear()

    response = await client.get("/api/v1/tasks")

    assert response.status_code == 401
    assert response.json() == {"detail": "unauthenticated"}
    assert env.identity.tokens == []


@pytest.mark.parametrize(
    "failure, status, detail",
    [
        (Unauthenticated(), 401, "unauthenticated"),
        (NotApproved(), 403, "not_approved"),
        (IdentityUnavailable(), 503, "identity_unavailable"),
    ],
)
async def test_identitys_verdict_is_relayed_without_exposing_tasks(
    env: Env,
    client: httpx.AsyncClient,
    failure: Exception,
    status: int,
    detail: str,
) -> None:
    env.board.add(1)
    env.identity.failure = failure
    card = env.board.add(2)

    for path in ("/api/v1/tasks", f"/api/v1/tasks/{card.id}"):
        response = await client.get(path)
        assert response.status_code == status
        assert response.json() == {"detail": detail}


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
    assert response.json() == {"detail": "tasks_unavailable"}


async def test_the_author_moves_a_task_and_gets_the_new_version(
    env: Env, client: httpx.AsyncClient
) -> None:
    task = env.board.add(1)

    response = await client.patch(
        f"/api/v1/tasks/{task.id}",
        json={"status": "in_progress", "expected_version": 1},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()["data"]
    assert (data["status"], data["version"]) == ("in_progress", 2)
