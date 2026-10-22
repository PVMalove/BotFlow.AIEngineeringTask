"""Общий framework-independent контракт Actor (ADR 0005).

`Actor` заменяет локальные для identity/support/catalog пары
`actor_id`/`is_admin` и сервис-локальные дубликаты `Actor`: security-адаптер
аутентифицирует вызывающего и строит это transport-neutral значение, а
application-хендлеры сами принимают решения об авторизации над ним."""

import enum
import uuid
from dataclasses import dataclass

from kernel_platform.http.errors import ApiError


class ActorRole(enum.StrEnum):
    ADMIN = "admin"
    USER = "user"


@dataclass(frozen=True)
class Actor:
    """Аутентифицированный вызывающий, передаваемый из HTTP-адаптера в use case."""

    id: uuid.UUID
    role: ActorRole


_ADMIN_ONLY = ApiError(
    status_code=403, code="FORBIDDEN", message="Доступ только для администраторов!"
)


def require_admin(actor: Actor) -> Actor:
    """Общий admin-only гейт: `AdminActor`-зависимость FastAPI каждого
    сервиса оборачивает эту функцию вместо дублирования проверки роли и
    текста ошибки (ADR 0005)."""
    if actor.role is not ActorRole.ADMIN:
        raise _ADMIN_ONLY
    return actor
