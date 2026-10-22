"""Admin HTTP contract: lists and decisions behind a session cookie."""

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
from conftest import NOW
from test_http_auth import ADMIN, ORIGIN, Env, client_for

from application.commands.auth import IssueLoginCodeCommand
from domain.entities.auth import Member, MemberStatus, Role

APPLICANT = 7_000_000_001
USER = 7_000_000_002
BASE = "/api/v1/admin"


def add(
    env: Env, telegram_id: int, status: MemberStatus, role: Role = Role.USER
) -> Member:
    member = Member(
        id=uuid4(),
        telegram_id=telegram_id,
        role=role,
        status=status,
        created_at=NOW,
        display_name=f"name-{telegram_id}",
    )
    env.store.members[member.id] = member
    return member


async def login_as(client: httpx.AsyncClient, env: Env, telegram_id: int) -> None:
    code = await env.issue.execute(IssueLoginCodeCommand(telegram_id))
    response = await client.post(
        "/api/v1/auth/exchange", json={"code": code.code}, headers=ORIGIN
    )
    assert response.status_code == 200


@pytest.fixture
def env() -> Env:
    env = Env(secure=False)
    add(env, ADMIN, MemberStatus.APPROVED, Role.ADMIN)
    return env


@pytest.fixture
async def admin(env: Env) -> AsyncIterator[httpx.AsyncClient]:
    async for client in client_for(env):
        await login_as(client, env, ADMIN)
        yield client


async def decide(
    client: httpx.AsyncClient,
    member: Member,
    action: str,
    version: int | None = None,
    **kwargs: Any,
) -> httpx.Response:
    return await client.post(
        f"{BASE}/members/{member.id}/{action}",
        json={"expected_version": version or member.access_version},
        headers=kwargs.pop("headers", ORIGIN),
    )


async def test_join_request_list_shows_waiting_applicants_only(
    admin: httpx.AsyncClient, env: Env
) -> None:
    waiting = add(env, APPLICANT, MemberStatus.PENDING)
    add(env, USER, MemberStatus.APPROVED)

    response = await admin.get(f"{BASE}/join-requests")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    (item,) = response.json()
    assert item == {
        "id": str(waiting.id),
        "telegram_id": APPLICANT,
        "display_name": f"name-{APPLICANT}",
        "role": "user",
        "status": "pending",
        "access_version": 1,
        "created_at": NOW.isoformat().replace("+00:00", "Z"),
        "status_changed_at": None,
    }


async def test_join_request_list_can_show_rejected_applicants(
    admin: httpx.AsyncClient, env: Env
) -> None:
    add(env, APPLICANT, MemberStatus.PENDING)
    add(env, USER, MemberStatus.REJECTED)

    response = await admin.get(f"{BASE}/join-requests", params={"status": "rejected"})

    assert [m["telegram_id"] for m in response.json()] == [USER]
    assert (
        await admin.get(f"{BASE}/join-requests", params={"status": "approved"})
    ).status_code == 422


async def test_member_list_has_approved_and_blocked_members_with_filter_and_paging(
    admin: httpx.AsyncClient, env: Env
) -> None:
    add(env, APPLICANT, MemberStatus.PENDING)
    add(env, USER, MemberStatus.BLOCKED)

    everyone = await admin.get(f"{BASE}/members")
    blocked = await admin.get(f"{BASE}/members", params={"status": "blocked"})
    page = await admin.get(f"{BASE}/members", params={"limit": 1, "offset": 1})

    assert sorted(m["telegram_id"] for m in everyone.json()) == [ADMIN, USER]
    assert [m["telegram_id"] for m in blocked.json()] == [USER]
    assert len(page.json()) == 1
    assert (await admin.get(f"{BASE}/members", params={"limit": 0})).status_code == 422
    assert (
        await admin.get(f"{BASE}/members", params={"limit": 101})
    ).status_code == 422


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", f"{BASE}/join-requests"),
        ("GET", f"{BASE}/members"),
        ("POST", f"{BASE}/members/{uuid4()}/approve"),
    ],
)
async def test_without_a_session_the_admin_api_is_401(
    env: Env, method: str, path: str
) -> None:
    async for client in client_for(env):
        response = await client.request(
            method, path, json={"expected_version": 1}, headers=ORIGIN
        )

        assert response.status_code == 401
        assert response.json() == {"detail": "unauthenticated"}


async def test_an_approved_non_admin_gets_403(env: Env) -> None:
    add(env, USER, MemberStatus.APPROVED)
    target = add(env, APPLICANT, MemberStatus.PENDING)
    async for client in client_for(env):
        await login_as(client, env, USER)

        listing = await client.get(f"{BASE}/members")
        decision = await decide(client, target, "approve")

        assert listing.status_code == 403
        assert listing.json() == {"detail": "admin_required"}
        assert decision.status_code == 403
        assert env.store.members[target.id].status is MemberStatus.PENDING


async def test_decisions_demand_the_same_origin(
    admin: httpx.AsyncClient, env: Env
) -> None:
    target = add(env, APPLICANT, MemberStatus.PENDING)

    foreign = await decide(
        admin, target, "approve", headers={"Origin": "https://evil.example"}
    )
    missing = await decide(admin, target, "approve", headers={})

    assert foreign.status_code == missing.status_code == 403
    assert foreign.json() == {"detail": "origin_not_allowed"}
    assert env.store.members[target.id].status is MemberStatus.PENDING


async def test_approve_returns_the_member_and_lets_them_log_in(
    admin: httpx.AsyncClient, env: Env
) -> None:
    target = add(env, APPLICANT, MemberStatus.PENDING)

    response = await decide(admin, target, "approve")

    assert response.status_code == 200
    body = response.json()
    assert body["changed"] is True
    assert body["member"]["status"] == "approved"
    assert body["member"]["access_version"] == 2
    assert response.headers["cache-control"] == "no-store"
    async for applicant in client_for(env):
        await login_as(applicant, env, APPLICANT)
        assert (await applicant.get("/api/v1/users/me")).status_code == 200


async def test_a_repeated_decision_is_200_unchanged(
    admin: httpx.AsyncClient, env: Env
) -> None:
    target = add(env, APPLICANT, MemberStatus.PENDING)
    await decide(admin, target, "reject")

    again = await decide(admin, target, "reject")

    assert again.status_code == 200
    assert again.json()["changed"] is False
    assert again.json()["member"]["access_version"] == 2


@pytest.mark.parametrize(
    ("status", "action", "version", "detail"),
    [
        (MemberStatus.PENDING, "approve", 5, "version_conflict"),
        (MemberStatus.APPROVED, "reject", 1, "invalid_transition"),
        (MemberStatus.BLOCKED, "approve", 1, "invalid_transition"),
    ],
)
async def test_conflicting_decisions_are_409(
    admin: httpx.AsyncClient,
    env: Env,
    status: MemberStatus,
    action: str,
    version: int,
    detail: str,
) -> None:
    target = add(env, APPLICANT, status)

    response = await decide(admin, target, action, version)

    assert response.status_code == 409
    assert response.json() == {"detail": detail}


async def test_an_admin_cannot_block_themselves(
    admin: httpx.AsyncClient, env: Env
) -> None:
    me = next(m for m in env.store.members.values() if m.telegram_id == ADMIN)

    response = await decide(admin, me, "block")

    assert response.status_code == 409
    assert response.json() == {"detail": "cannot_block_self"}
    assert env.store.members[me.id].status is MemberStatus.APPROVED


async def test_unknown_member_and_bad_requests(admin: httpx.AsyncClient) -> None:
    ghost = Member(uuid4(), 1, Role.USER, MemberStatus.PENDING, NOW)

    assert (await decide(admin, ghost, "approve")).status_code == 404
    assert (await decide(admin, ghost, "promote")).status_code == 422
    bad_id = await admin.post(
        f"{BASE}/members/not-a-uuid/approve",
        json={"expected_version": 1},
        headers=ORIGIN,
    )
    no_version = await admin.post(
        f"{BASE}/members/{ghost.id}/approve", json={}, headers=ORIGIN
    )
    zero = await admin.post(
        f"{BASE}/members/{ghost.id}/approve",
        json={"expected_version": 0},
        headers=ORIGIN,
    )
    assert bad_id.status_code == no_version.status_code == zero.status_code == 422


async def test_block_ends_the_session_and_unblock_does_not_revive_it(
    admin: httpx.AsyncClient, env: Env
) -> None:
    user = add(env, USER, MemberStatus.APPROVED)
    async for member_client in client_for(env):
        await login_as(member_client, env, USER)
        cookie = member_client.cookies["botflow_session"]
        assert (await member_client.get("/api/v1/users/me")).status_code == 200

        blocked = await decide(admin, user, "block")
        after_block = await member_client.get("/api/v1/users/me")
        unblocked = await decide(admin, env.store.members[user.id], "unblock")
        after_unblock = await member_client.get(
            "/api/v1/users/me", headers={"Cookie": f"botflow_session={cookie}"}
        )

        assert blocked.status_code == unblocked.status_code == 200
        assert after_block.status_code == 401
        assert after_unblock.status_code == 401
        async for fresh in client_for(env):
            await login_as(fresh, env, USER)
            assert (await fresh.get("/api/v1/users/me")).status_code == 200


async def test_a_blocked_applicant_cannot_get_a_code_and_a_rejected_one_can_reapply(
    admin: httpx.AsyncClient, env: Env
) -> None:
    target = add(env, APPLICANT, MemberStatus.PENDING)
    await decide(admin, target, "reject")
    async for bot in client_for(env):
        join = {"telegram_id": APPLICANT}
        key = {"X-Service-Key": "automation-key-for-tests"}
        reapplied = await bot.post("/internal/v1/join-requests", json=join, headers=key)
        await decide(admin, env.store.members[target.id], "block")
        blocked = await bot.post("/internal/v1/join-requests", json=join, headers=key)

        assert reapplied.json()["outcome"] == "submitted"
        assert reapplied.json()["member"]["access_version"] == 3
        assert blocked.json()["outcome"] == "blocked"
        assert env.store.members[target.id].status is MemberStatus.BLOCKED
