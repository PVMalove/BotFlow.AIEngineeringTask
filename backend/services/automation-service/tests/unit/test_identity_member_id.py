"""The member id from Identity's MemberView identifies the author of a task."""

from uuid import uuid4

import httpx

from infrastructure.identity_gateway import HttpIdentityGateway

USER = 4_200_000_001


async def test_find_member_reads_the_stable_member_id() -> None:
    member_id = uuid4()

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": str(member_id),
                "telegram_id": USER,
                "role": "member",
                "status": "approved",
                "access_version": 3,
            },
        )

    async with httpx.AsyncClient(
        base_url="http://identity", transport=httpx.MockTransport(respond)
    ) as client:
        member = await HttpIdentityGateway(client, "key").find_member(USER)

    assert member is not None and member.member_id == member_id
