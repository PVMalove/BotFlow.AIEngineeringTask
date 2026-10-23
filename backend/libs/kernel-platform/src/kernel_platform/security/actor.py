"""The authenticated caller, independent of the transport."""

import enum
import uuid
from dataclasses import dataclass

from kernel_platform.http.errors import ApiError


class ActorRole(enum.StrEnum):
    ADMIN = "admin"
    USER = "user"


@dataclass(frozen=True)
class Actor:
    """The caller that the HTTP adapter passes to a use case."""

    id: uuid.UUID
    role: ActorRole


def require_admin(actor: Actor) -> Actor:
    """The shared admin-only gate for the `AdminActor` dependency of a service."""
    if actor.role is not ActorRole.ADMIN:
        raise ApiError(
            status_code=403,
            code="forbidden",
            message="Доступ только для администраторов!",
        )
    return actor
