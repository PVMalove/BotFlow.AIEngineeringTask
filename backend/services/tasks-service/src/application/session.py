"""Session check shared by the use cases."""

from application.ports import MemberAuthenticator, Unauthenticated
from domain.tasks import Actor


async def authenticate(auth: MemberAuthenticator, token: str | None) -> Actor:
    if not token:
        raise Unauthenticated
    return await auth.require_approved(token)  # on every request: access can be revoked
