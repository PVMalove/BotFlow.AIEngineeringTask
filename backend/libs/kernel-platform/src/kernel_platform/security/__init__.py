"""Transport-neutral security primitives."""

from kernel_platform.security.actor import Actor, ActorRole, require_admin
from kernel_platform.security.errors import AuthErrors
from kernel_platform.security.origin import require_same_origin

__all__ = ["Actor", "ActorRole", "AuthErrors", "require_admin", "require_same_origin"]
