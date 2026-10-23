import base64
import uuid
from datetime import UTC, datetime

import pytest

from kernel_platform.pagination import (
    Cursor,
    InvalidCursorError,
    decode_cursor,
    encode_cursor,
)


def test_encode_decode_roundtrip() -> None:
    created_at = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)
    entity_id = uuid.uuid4()

    token = encode_cursor(created_at, entity_id)

    assert decode_cursor(token) == Cursor(created_at=created_at, id=entity_id)


def test_decode_invalid_base64_raises_invalid_cursor_error() -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor("not-valid-base64!!")


def test_decode_malformed_payload_raises_invalid_cursor_error() -> None:
    token = base64.urlsafe_b64encode(b"no-separator-here").decode("ascii")

    with pytest.raises(InvalidCursorError):
        decode_cursor(token)
