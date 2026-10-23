"""Transport-neutral security primitives.

`require_same_origin` needs FastAPI, so it is imported from `security.origin`."""

from kernel_platform.security.actor import Actor, ActorRole, require_admin
from kernel_platform.security.errors import AuthErrors

__all__ = ["Actor", "ActorRole", "AuthErrors", "require_admin"]
