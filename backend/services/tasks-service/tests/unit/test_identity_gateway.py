"""The Identity adapter forwards the session cookie and maps its verdicts."""

from uuid import UUID

import httpx
import pytest

from application.ports import IdentityUnavailable, NotApproved, Unauthenticated
from domain.tasks import Actor, Role
from infrastructure.identity_gateway import HttpIdentityGateway


def gateway(handler: httpx.MockTransport) -> HttpIdentityGateway:
    return HttpIdentityGateway(
        httpx.AsyncClient(transport=handler, base_url="http://identity")
    )


def answer(status: int, body: object = None) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, json=body))


APPROVED = {
    "id": "7f1c7a52-1111-4222-8333-444455556666",
    "telegram_id": 1,
    "role": "user",
    "status": "approved",
    "access_version": 1,
}


async def test_the_session_cookie_is_forwarded_to_the_current_member_endpoint() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=APPROVED)

    await gateway(httpx.MockTransport(handler)).require_approved("tok")

    assert seen[0].method == "GET" and seen[0].url.path == "/api/v1/users/me"
    assert seen[0].headers["cookie"] == "botflow_session=tok"


async def test_an_approved_member_passes_as_an_actor_with_id_and_role() -> None:
    actor = await gateway(answer(200, APPROVED)).require_approved("tok")

    assert actor == Actor(UUID(str(APPROVED["id"])), Role.USER)


async def test_an_admin_is_reported_with_the_admin_role() -> None:
    actor = await gateway(answer(200, {**APPROVED, "role": "admin"})).require_approved(
        "tok"
    )

    assert actor.role is Role.ADMIN


@pytest.mark.parametrize(
    "status, body, error",
    [
        (401, {"detail": "unauthenticated"}, Unauthenticated),
        (403, {"detail": "not_approved"}, NotApproved),
        (200, {**APPROVED, "status": "blocked"}, NotApproved),
        (500, None, IdentityUnavailable),
        (429, None, IdentityUnavailable),
        (200, {"unexpected": True}, IdentityUnavailable),
        (200, {**APPROVED, "role": "owner"}, IdentityUnavailable),
        (200, {**APPROVED, "id": "not-a-uuid"}, IdentityUnavailable),
    ],
)
async def test_identitys_answers_map_to_domain_errors(
    status: int, body: object, error: type[Exception]
) -> None:
    with pytest.raises(error):
        await gateway(answer(status, body)).require_approved("tok")


async def test_a_network_failure_is_identity_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(IdentityUnavailable):
        await gateway(httpx.MockTransport(handler)).require_approved("tok")
