"""The broker message envelope shared by producers and consumers."""

ENVELOPE_VERSION = 1


def as_int(value: object) -> int:
    """A payload field that must be an integer; anything else is a ValueError."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError
    return value


def as_str(value: object) -> str:
    """A payload field that must be a string; anything else is a ValueError."""
    if not isinstance(value, str):
        raise ValueError
    return value
