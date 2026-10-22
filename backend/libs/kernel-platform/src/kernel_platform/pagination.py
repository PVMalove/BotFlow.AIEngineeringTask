# ruff: noqa: E501
"""Общий keyset-курсор и форма страницы для list-эндпоинтов (issue #200).

Ранее локальный для `catalog-service` контракт: непрозрачный курсор
(`created_at`, `id`), форма страницы (`has_more`/`has_prev` и т.п.) и
кодирование/декодирование курсора в base64-строку. Второй подтверждённый
потребитель — `identity-service` (issue #201/#202) — делает дублирование
между сервисами неоправданным (тот же admission-принцип, что и ADR 0006 для
generic drain-в-outbox).
"""

import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import datetime

DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100


class InvalidCursorError(ValueError):
    """Курсор пагинации не удалось декодировать."""


@dataclass(frozen=True)
class Cursor:
    """Keyset-позиция в отсортированном по (`created_at`, `id`) списке."""

    created_at: datetime
    id: uuid.UUID


@dataclass(frozen=True)
class PageInfo:
    """Форма страницы курсорного листинга — не зависит от типа элементов."""

    next_cursor: str | None
    prev_cursor: str | None
    has_more: bool
    has_prev: bool


@dataclass(frozen=True)
class Page[T]:
    """Транспортно-независимая страница: элементы плюс их `PageInfo` —
    то, что list-хэндлеры возвращают через `Result`, а `match_page`
    разворачивает в `data`/`meta` BFF-конверта (issue #221, ADR 0002)."""

    items: list[T]
    page_info: PageInfo


def encode_cursor(created_at: datetime, entity_id: uuid.UUID) -> str:
    """Кодирует keyset-позицию в непрозрачный курсор.

    Args:
        created_at (datetime): Значение `created_at` строки-границы страницы.
        entity_id (uuid.UUID): Id той же строки — тай-брейкер сортировки.

    Returns:
        str: Непрозрачная base64-строка курсора."""
    raw = f"{created_at.isoformat()}|{entity_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(token: str) -> Cursor:
    """Декодирует курсор, выданный `encode_cursor`, обратно в `Cursor`.

    Args:
        token (str): Курсор из query-параметра `after`/`before`.

    Returns:
        Cursor: Декодированная keyset-позиция.

    Raises:
        InvalidCursorError: Курсор повреждён или не декодируется."""
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        created_at_raw, id_raw = raw.split("|")
        return Cursor(
            created_at=datetime.fromisoformat(created_at_raw), id=uuid.UUID(id_raw)
        )
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidCursorError("Некорректный курсор пагинации") from exc
