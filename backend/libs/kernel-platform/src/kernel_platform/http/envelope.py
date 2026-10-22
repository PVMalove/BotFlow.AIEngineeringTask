"""Общий конверт успешного BFF-ответа (ADR 0002).

`ApiResponse[T]` не зависит ни от одного catalog/identity/support DTO — `T`
подставляет сам вызывающий сервис (`ApiResponse[ProductView]` и т.п.)."""

from pydantic import BaseModel


class ApiResponse[T](BaseModel):
    """Успешный BFF-ответ: `data` — полезная нагрузка (может быть `None` для
    удаления), `meta` — вспомогательные данные, всегда объект, пустой при
    отсутствии (пагинация будущих list-эндпоинтов и т.п.)."""

    data: T
    meta: dict[str, object] = {}
