from enum import StrEnum


class TaskSource(StrEnum):
    """The kind of Telegram message a task came from; unknown for old tasks."""

    TEXT = "text"
    VOICE = "voice"
