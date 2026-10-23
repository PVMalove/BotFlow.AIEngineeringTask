"""Opaque keyset cursor and the page shape of list endpoints."""

import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import datetime

DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100


class InvalidCursorError(ValueError):
    """The pagination cursor cannot be decoded."""


@dataclass(frozen=True)
class Cursor:
    """A position in a list sorted by (`created_at`, `id`)."""

    created_at: datetime
    id: uuid.UUID


@dataclass(frozen=True)
class PageInfo:
    """The shape of a page, independent of the item type."""

    next_cursor: str | None
    has_more: bool


@dataclass(frozen=True)
class Page[T]:
    """Items plus their `PageInfo`: what list handlers return in a `Result`."""

    items: list[T]
    page_info: PageInfo


def encode_cursor(created_at: datetime, entity_id: uuid.UUID) -> str:
    """Encode the boundary row of a page into an opaque cursor."""
    raw = f"{created_at.isoformat()}|{entity_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(token: str) -> Cursor:
    """Decode a cursor from `encode_cursor`; a damaged one is `InvalidCursorError`."""
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        created_at_raw, id_raw = raw.split("|")
        return Cursor(
            created_at=datetime.fromisoformat(created_at_raw), id=uuid.UUID(id_raw)
        )
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidCursorError("Некорректный курсор пагинации") from exc
