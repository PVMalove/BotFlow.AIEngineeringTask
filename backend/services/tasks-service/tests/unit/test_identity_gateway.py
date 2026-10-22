"""The Identity adapter forwards the session cookie and maps its verdicts."""

from uuid import UUID

import httpx
import pytest
from kernel_platform.security import Actor, ActorRole

from application.ports import IdentityUnavailable
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
    result = await gateway(answer(200, APPROVED)).require_approved("tok")

    assert result.value == Actor(UUID(str(APPROVED["id"])), ActorRole.USER)


async def test_an_admin_is_reported_with_the_admin_role() -> None:
    result = await gateway(answer(200, {**APPROVED, "role": "admin"})).require_approved(
        "tok"
    )

    assert result.value.role is ActorRole.ADMIN


@pytest.mark.parametrize(
    "status, body, code",
    [
        (401, {"detail": "unauthenticated"}, "unauthenticated"),
        (403, {"detail": "not_approved"}, "not_approved"),
        (200, {**APPROVED, "status": "blocked"}, "not_approved"),
    ],
)
async def test_identitys_refusals_come_back_as_errors(
    status: int, body: object, code: str
) -> None:
    result = await gateway(answer(status, body)).require_approved("tok")

    assert result.is_err and result.error.code == code


@pytest.mark.parametrize(
    "status, body",
    [
        (500, None),
        (429, None),
        (200, {"unexpected": True}),
        (200, {**APPROVED, "role": "owner"}),
        (200, {**APPROVED, "id": "not-a-uuid"}),
    ],
)
async def test_an_unusable_answer_is_identity_unavailable(
    status: int, body: object
) -> None:
    with pytest.raises(IdentityUnavailable):
        await gateway(answer(status, body)).require_approved("tok")


async def test_a_network_failure_is_identity_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(IdentityUnavailable):
        await gateway(httpx.MockTransport(handler)).require_approved("tok")
